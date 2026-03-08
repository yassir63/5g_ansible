import os
import sys
import redis
import ast
import ipaddress
from scapy.all import sniff
from scapy.contrib.pfcp import PFCP

# -------------------------
# Config
# -------------------------
interface = os.getenv("smf_sniffer_iface")
if not interface:
    if len(sys.argv) > 1:
        interface = sys.argv[1]
    else:
        print("❌ Error: No interface provided. Set smf_sniffer_iface or pass as argument.")
        sys.exit(1)

redis_host = os.getenv("REDIS_HOST", "redis.open5gs.svc.cluster.local")
print(f"🔗 Connecting to Redis at {redis_host}:6379...")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

DEBUG = os.getenv("PFCP_DEBUG", "0") in ("1", "true", "True", "YES", "yes")

# -------------------------
# State
# -------------------------
seid_ctx: dict[int, dict] = {}     # canonical_seid -> {"ue_ip":..., "imsi":..., "ul_teid":..., "dl_teid":...}
seid_alias: dict[int, int] = {}   # seid -> canonical_seid (Open5GS flips SEID by direction)

# NEW: correlate by PFCP sequence number (50/51 share same seq)
seq_cache: dict[int, dict] = {}   # seq -> {"ue_ip":..., "imsi":...}

def canon(seid: int) -> int:
    """Resolve alias chain to canonical SEID."""
    if not seid:
        return 0
    while seid in seid_alias and seid_alias[seid] != seid:
        seid = seid_alias[seid]
    return seid

def get_ctx(seid: int) -> dict:
    c = canon(seid)
    if c not in seid_ctx:
        seid_ctx[c] = {}
        seid_alias.setdefault(c, c)
    return seid_ctx[c]

def merge_ctx(a: dict, b: dict):
    """Merge b into a without deleting."""
    for k in ("ue_ip", "imsi", "ul_teid", "dl_teid"):
        if k not in a and k in b:
            a[k] = b[k]

def union_seids(a: int, b: int):
    """
    Bidirectionally link SEIDs so either endpoint SEID resolves to the same canonical session.
    """
    if not a or not b:
        return

    seid_alias.setdefault(a, a)
    seid_alias.setdefault(b, b)

    ca, cb = canon(a), canon(b)
    if ca == 0:
        ca = a
        seid_alias[a] = a
        seid_ctx.setdefault(a, {})
    if cb == 0:
        cb = b
        seid_alias[b] = b
        seid_ctx.setdefault(b, {})

    if ca == cb:
        return

    root = min(ca, cb)
    other = max(ca, cb)

    seid_alias[other] = root

    rctx = get_ctx(root)
    octx = get_ctx(other)
    merge_ctx(rctx, octx)
    merge_ctx(octx, rctx)

def cleanup_pfcp_session(seid_for_cleanup: int):
    """
    Delete Redis mappings for a single PFCP session (one PDU session).
    Trigger this on PFCP Session Deletion Request (type 54).
    """
    cseid = canon(seid_for_cleanup)
    ctx = seid_ctx.get(cseid, {})

    ue_ip = ctx.get("ue_ip")
    imsi = ctx.get("imsi")
    ul = ctx.get("ul_teid")
    dl = ctx.get("dl_teid")

    if ul:
        rdb.delete(f"teid:{ul}")
    if dl:
        rdb.delete(f"teid:{dl}")

    if ue_ip:
        rdb.delete(f"ip:{ue_ip}")
    if imsi:
        rdb.delete(f"imsi:{imsi}")

    seid_ctx.pop(cseid, None)
    for k, v in list(seid_alias.items()):
        if canon(k) == cseid or v == cseid:
            seid_alias.pop(k, None)

    print(f"[🧹][SMF] PFCP session deleted: seid={seid_for_cleanup} canon={cseid} "
          f"imsi={imsi or '-'} ip={ue_ip or '-'} ul={ul or '-'} dl={dl or '-'}")

# -------------------------
# Helpers
# -------------------------
def normalize_teid(teid) -> str | None:
    try:
        if teid is None:
            return None
        if isinstance(teid, int):
            return f"{teid:08x}"
        s = str(teid).strip()
        if s.startswith("0x"):
            return f"{int(s, 16):08x}"
        if s.isdigit():
            return f"{int(s):08x}"
        return f"{int(s, 16):08x}"
    except Exception:
        return None

def tbcd_to_digits(b: bytes) -> str:
    digits = []
    for byte in b:
        lo = byte & 0x0F
        hi = (byte >> 4) & 0x0F
        if lo != 0x0F:
            digits.append(str(lo))
        if hi != 0x0F:
            digits.append(str(hi))
    return "".join(digits)

def normalize_imsi(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        d = tbcd_to_digits(bytes(v))
        return d if d else None
    if isinstance(v, str) and v.startswith("b'"):
        try:
            b = ast.literal_eval(v)
            d = tbcd_to_digits(b)
            return d if d else None
        except Exception:
            return None
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return digits if digits else None

def extract_all_ies(obj):
    ies = []
    stack = [obj]
    seen = set()
    while stack:
        cur = stack.pop()
        cid = id(cur)
        if cid in seen:
            continue
        seen.add(cid)

        if hasattr(cur, "ietype"):
            ies.append(cur)
        if hasattr(cur, "IE_list") and isinstance(cur.IE_list, list):
            stack.extend(cur.IE_list)
    return ies

def ie_name(ie) -> str:
    return type(ie).__name__

def ie_type(ie) -> int | None:
    return getattr(ie, "ietype", None)

def get_attr(obj, *names):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v is not None:
                return v
    return None

def extract_ipv4_from_ue_ip_ie(ie) -> str | None:
    for attr in ("ipv4", "IPv4", "v4", "ip", "addr", "address", "ipv4_address", "ue_ip"):
        v = getattr(ie, attr, None)
        if v is None:
            continue
        if isinstance(v, str) and "." in v:
            return v
        if isinstance(v, int):
            try:
                return str(ipaddress.IPv4Address(v))
            except Exception:
                pass
        if isinstance(v, (bytes, bytearray)) and len(v) >= 4:
            try:
                return str(ipaddress.IPv4Address(v[-4:]))
            except Exception:
                pass

    try:
        b = bytes(ie)
        if len(b) >= 4:
            return str(ipaddress.IPv4Address(b[-4:]))
    except Exception:
        pass

    s = str(ie)
    for token in s.replace(",", " ").replace(";", " ").split():
        if token.count(".") == 3:
            return token.strip()

    return None

def store_teid_hash(teid_hex8: str, ue_ip: str | None, direction: str, imsi: str | None):
    key = f"teid:{teid_hex8}"
    existing = rdb.hgetall(key) or {}

    mapping = {"dir": direction}
    if ue_ip:
        mapping["ue_ip"] = ue_ip

    if imsi:
        if not existing.get("imsi") or existing.get("imsi") == "unknown":
            mapping["imsi"] = imsi

    rdb.hset(key, mapping=mapping)

    if direction == "UL" and ue_ip:
        rdb.set(f"ip:{ue_ip}", teid_hex8)
    if direction == "UL" and imsi:
        rdb.set(f"imsi:{imsi}", teid_hex8)

    print(f"[✓][SMF] {key} -> dir={direction} ip={ue_ip or '-'} imsi={mapping.get('imsi','-')}")

def looks_like_ngap_known_teid(teid_hex8: str) -> bool:
    h = rdb.hgetall(f"teid:{teid_hex8}") or {}
    return ("ran_ue_id" in h) or ("sst" in h) or ("sd" in h)

# -------------------------
# PFCP parsing strategy
# -------------------------
def is_access_interface(val) -> bool:
    if val is None:
        return False
    if isinstance(val, int):
        return val == 0
    s = str(val).strip()
    if s.isdigit():
        return int(s) == 0
    return "access" in s.lower()

def parse_pfcp(pkt):
    pfcp = pkt[PFCP]
    msg_type = int(getattr(pfcp, "message_type", -1))
    hdr_seid = int(getattr(pfcp, "seid", 0) or 0)
    seq = int(getattr(pfcp, "seq", 0) or 0)

    ies = extract_all_ies(pfcp)

    if DEBUG:
        ie_types = [ie_type(x) for x in ies if ie_type(x) is not None]
        print(f"[PFCP][DBG] msg_type={msg_type} seq={seq} hdr_seid={hdr_seid} ie_types(sample)={ie_types[:20]}")

    # Teardown: PFCP Session Deletion Request
    if msg_type == 54 and hdr_seid:
        cleanup_pfcp_session(hdr_seid)
        return

    peer_fseid = None
    found_ip = None
    found_imsi = None

    ul_candidates = []
    dl_candidates = []

    # --- pass 1: UE IP, IMSI, and peer F-SEID ---
    for ie in ies:
        t = ie_type(ie)
        n = ie_name(ie)

        # F-SEID IE type is 57
        if t == 57 or "FSEID" in n.upper():
            v = get_attr(ie, "SEID", "seid")
            if v is not None:
                try:
                    peer_fseid = int(v)
                except Exception:
                    pass

        # UE IP Address IE type is 93
        if t == 93 or ("UE" in n.upper() and "IP" in n.upper()):
            ip = extract_ipv4_from_ue_ip_ie(ie)
            if ip:
                found_ip = ip

        if n in ("IE_User_ID", "IE_UserID", "IE_UserId"):
            raw = get_attr(ie, "IMSI", "imsi")
            x = normalize_imsi(raw)
            if not x:
                digits = "".join(ch for ch in str(ie) if ch.isdigit())
                if len(digits) >= 10:
                    x = digits
            if x:
                found_imsi = x

    # Cache by seq (50/51 share seq) so response can inherit UE IP even if it isn't repeated
    if seq:
        if found_ip:
            seq_cache.setdefault(seq, {})["ue_ip"] = found_ip
        if found_imsi:
            seq_cache.setdefault(seq, {})["imsi"] = found_imsi

    # Determine session key for this packet
    ctx_seid = hdr_seid or peer_fseid or 0

    # Link endpoint SEIDs if both present
    if hdr_seid and peer_fseid:
        union_seids(hdr_seid, peer_fseid)

    # If we didn't find UE IP in this message, try to inherit from seq cache
    if not found_ip and seq and seq in seq_cache:
        found_ip = seq_cache[seq].get("ue_ip") or found_ip
    if not found_imsi and seq and seq in seq_cache:
        found_imsi = seq_cache[seq].get("imsi") or found_imsi

    # Update session context
    ctx = get_ctx(ctx_seid)
    if found_ip:
        ctx["ue_ip"] = found_ip
    if found_imsi:
        ctx["imsi"] = found_imsi

    # --- pass 2: UL candidates ---
    if msg_type == 51:
        for ie in ies:
            if ie_name(ie) == "IE_CreatedPDR":
                inner = extract_all_ies(ie)
                for x in inner:
                    if ie_name(x) == "IE_FTEID":
                        teid_val = get_attr(x, "TEID", "teid")
                        teid_hex = normalize_teid(teid_val)
                        if teid_hex:
                            ul_candidates.append(teid_hex)

    if not ul_candidates:
        for ie in ies:
            if ie_name(ie) == "IE_FTEID":
                teid_val = get_attr(ie, "TEID", "teid")
                teid_hex = normalize_teid(teid_val)
                if teid_hex:
                    ul_candidates.append(teid_hex)

    # --- pass 3: DL candidates with context (Destination Interface == Access) ---
    def walk_with_context(node, dest_if=None):
        if hasattr(node, "ietype"):
            n = ie_name(node)

            if n == "IE_DestinationInterface":
                di = get_attr(node, "interface", "Interface", "dst_iface", "destination_interface")
                dest_if = di if di is not None else dest_if

            if n == "IE_OuterHeaderCreation":
                if is_access_interface(dest_if):
                    teid_val = get_attr(node, "TEID", "teid")
                    teid_hex = normalize_teid(teid_val)
                    if teid_hex:
                        dl_candidates.append(teid_hex)

        for ch in getattr(node, "IE_list", []) or []:
            walk_with_context(ch, dest_if)

    walk_with_context(pfcp, None)

    # --- choose UL TEID ---
    ul_teid = None
    if ul_candidates:
        if len(ul_candidates) > 1:
            for cand in ul_candidates:
                if looks_like_ngap_known_teid(cand):
                    ul_teid = cand
                    break
        ul_teid = ul_teid or ul_candidates[0]

    # --- choose DL TEID ---
    dl_teid = None
    if dl_candidates:
        if len(dl_candidates) > 1:
            for cand in dl_candidates:
                if looks_like_ngap_known_teid(cand):
                    dl_teid = cand
                    break
        dl_teid = dl_teid or dl_candidates[0]

    # Save chosen TEIDs into session context for cleanup later
    if ul_teid:
        get_ctx(ctx_seid)["ul_teid"] = ul_teid
    if dl_teid:
        get_ctx(ctx_seid)["dl_teid"] = dl_teid

    ue_ip = get_ctx(ctx_seid).get("ue_ip")
    imsi = get_ctx(ctx_seid).get("imsi")

    if DEBUG and msg_type in (50, 51, 52) and not ue_ip:
        print(f"[PFCP][DBG] no ue_ip yet: msg_type={msg_type} seq={seq} hdr_seid={hdr_seid} peer_fseid={peer_fseid}")

    if ul_teid:
        store_teid_hash(ul_teid, ue_ip, "UL", imsi)
    if dl_teid:
        store_teid_hash(dl_teid, ue_ip, "DL", imsi)

def handle_pfcp(pkt):
    if not pkt.haslayer(PFCP):
        return
    try:
        parse_pfcp(pkt)
    except Exception:
        return

def main():
    print(f"🚀 PFCP sniffer running on '{interface}' (udp/8805)...")
    sniff(filter="udp port 8805", iface=interface, prn=handle_pfcp, store=0)

if __name__ == "__main__":
    main()
    
# import os
# import sys
# import redis
# import ast
# import ipaddress
# from scapy.all import sniff
# from scapy.contrib.pfcp import PFCP

# # -------------------------
# # Config
# # -------------------------
# interface = os.getenv("smf_sniffer_iface")
# if not interface:
#     if len(sys.argv) > 1:
#         interface = sys.argv[1]
#     else:
#         print("❌ Error: No interface provided. Set smf_sniffer_iface or pass as argument.")
#         sys.exit(1)

# redis_host = os.getenv("REDIS_HOST", "redis")
# print(f"🔗 Connecting to Redis at {redis_host}:6379...")
# rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

# # -------------------------
# # State (NEW)
# # -------------------------
# seid_ctx: dict[int, dict] = {}     # canonical_seid -> {"ue_ip":..., "imsi":...}
# seid_alias: dict[int, int] = {}   # seid -> canonical_seid (for Open5GS seid switch)

# def canon(seid: int) -> int:
#     """Resolve alias chain to canonical SEID."""
#     if not seid:
#         return 0
#     while seid in seid_alias and seid_alias[seid] != seid:
#         seid = seid_alias[seid]
#     return seid

# def get_ctx(seid: int) -> dict:
#     c = canon(seid)
#     if c not in seid_ctx:
#         seid_ctx[c] = {}
#         seid_alias.setdefault(c, c)
#     return seid_ctx[c]

# def merge_ctx(a: dict, b: dict):
#     """Merge b into a without deleting."""
#     for k in ("ue_ip", "imsi"):
#         if k not in a and k in b:
#             a[k] = b[k]

# # -------------------------
# # Helpers
# # -------------------------
# def normalize_teid(teid) -> str | None:
#     try:
#         if teid is None:
#             return None
#         if isinstance(teid, int):
#             return f"{teid:08x}"
#         s = str(teid).strip()
#         if s.startswith("0x"):
#             return f"{int(s, 16):08x}"
#         if s.isdigit():
#             return f"{int(s):08x}"
#         return f"{int(s, 16):08x}"
#     except Exception:
#         return None

# def tbcd_to_digits(b: bytes) -> str:
#     digits = []
#     for byte in b:
#         lo = byte & 0x0F
#         hi = (byte >> 4) & 0x0F
#         if lo != 0x0F:
#             digits.append(str(lo))
#         if hi != 0x0F:
#             digits.append(str(hi))
#     return "".join(digits)

# def normalize_imsi(v) -> str | None:
#     if v is None:
#         return None
#     if isinstance(v, (bytes, bytearray)):
#         d = tbcd_to_digits(bytes(v))
#         return d if d else None
#     if isinstance(v, str) and v.startswith("b'"):
#         try:
#             b = ast.literal_eval(v)
#             d = tbcd_to_digits(b)
#             return d if d else None
#         except Exception:
#             return None
#     digits = "".join(ch for ch in str(v) if ch.isdigit())
#     return digits if digits else None

# def extract_all_ies(obj):
#     ies = []
#     stack = [obj]
#     seen = set()
#     while stack:
#         cur = stack.pop()
#         cid = id(cur)
#         if cid in seen:
#             continue
#         seen.add(cid)

#         if hasattr(cur, "ietype"):
#             ies.append(cur)
#         if hasattr(cur, "IE_list") and isinstance(cur.IE_list, list):
#             stack.extend(cur.IE_list)
#     return ies

# def ie_name(ie) -> str:
#     return type(ie).__name__

# def get_attr(obj, *names):
#     for n in names:
#         if hasattr(obj, n):
#             v = getattr(obj, n)
#             if v is not None:
#                 return v
#     return None

# def extract_ipv4_from_ue_ip_ie(ie) -> str | None:
#     """
#     Works across scapy versions: tries attrs, then bytes(node).
#     Your debug proved UE_IP is present, but not always as ie.ipv4.
#     """
#     # common attrs
#     for attr in ("ipv4", "IPv4", "v4", "ip", "addr", "address", "ipv4_address", "ue_ip"):
#         v = getattr(ie, attr, None)
#         if v is None:
#             continue
#         if isinstance(v, str) and "." in v:
#             return v
#         if isinstance(v, int):
#             try:
#                 return str(ipaddress.IPv4Address(v))
#             except Exception:
#                 pass
#         if isinstance(v, (bytes, bytearray)) and len(v) >= 4:
#             try:
#                 return str(ipaddress.IPv4Address(v[-4:]))
#             except Exception:
#                 pass

#     # raw bytes fallback
#     try:
#         b = bytes(ie)
#         if len(b) >= 4:
#             return str(ipaddress.IPv4Address(b[-4:]))
#     except Exception:
#         pass

#     # final fallback: string contains dotted ip (sometimes)
#     s = str(ie)
#     for token in s.split():
#         if token.count(".") == 3:
#             return token.strip(",;")

#     return None

# def store_teid_hash(teid_hex8: str, ue_ip: str | None, direction: str, imsi: str | None):
#     key = f"teid:{teid_hex8}"
#     existing = rdb.hgetall(key) or {}

#     mapping = {"dir": direction}
#     if ue_ip:
#         mapping["ue_ip"] = ue_ip

#     if imsi:
#         if not existing.get("imsi") or existing.get("imsi") == "unknown":
#             mapping["imsi"] = imsi

#     # merge-only write
#     rdb.hset(key, mapping=mapping)

#     if direction == "UL" and ue_ip:
#         rdb.set(f"ip:{ue_ip}", teid_hex8)
#     if direction == "UL" and imsi:
#         rdb.set(f"imsi:{imsi}", teid_hex8)

#     print(f"[✓][SMF] {key} -> dir={direction} ip={ue_ip or '-'} imsi={mapping.get('imsi','-')}")

# def looks_like_ngap_known_teid(teid_hex8: str) -> bool:
#     h = rdb.hgetall(f"teid:{teid_hex8}") or {}
#     return ("ran_ue_id" in h) or ("sst" in h) or ("sd" in h)

# # -------------------------
# # PFCP parsing strategy
# # -------------------------
# def is_access_interface(val) -> bool:
#     if val is None:
#         return False
#     if isinstance(val, int):
#         return val == 0
#     s = str(val).strip()
#     if s.isdigit():
#         return int(s) == 0
#     return "access" in s.lower()

# def parse_pfcp(pkt):
#     pfcp = pkt[PFCP]
#     msg_type = int(getattr(pfcp, "message_type", -1))
#     hdr_seid = int(getattr(pfcp, "seid", 0) or 0)  # header SEID (if S flag used)
#     ies = extract_all_ies(pfcp)

#     # Discover peer SEID from F-SEID IE (Open5GS uses this and may "switch" SEID after msg 51)
#     peer_fseid = None

#     # Extract any UE IP / IMSI present in this message
#     found_ip = None
#     found_imsi = None

#     # TEID candidates
#     ul_candidates = []
#     dl_candidates = []

#     # --- pass 1: UE IP, IMSI, and peer F-SEID ---
#     for ie in ies:
#         n = ie_name(ie)

#         if n == "IE_FSEID":
#             v = get_attr(ie, "SEID", "seid")
#             if v is not None:
#                 try:
#                     peer_fseid = int(v)
#                 except Exception:
#                     pass

#         elif n == "IE_UE_IP_Address":
#             ip = extract_ipv4_from_ue_ip_ie(ie)
#             if ip:
#                 found_ip = ip

#         elif n in ("IE_User_ID", "IE_UserID", "IE_UserId"):
#             raw = get_attr(ie, "IMSI", "imsi")
#             x = normalize_imsi(raw)
#             if not x:
#                 digits = "".join(ch for ch in str(ie) if ch.isdigit())
#                 if len(digits) >= 10:
#                     x = digits
#             if x:
#                 found_imsi = x

#     # Determine which SEID to use for context:
#     # - type 50 often has hdr_seid=0 but includes peer_fseid (SMF F-SEID)
#     # - type 51 has hdr_seid=that SMF SEID and includes peer_fseid=UPF SEID
#     ctx_seid = hdr_seid or peer_fseid or 0

#     # Ensure ctx exists and update with found data
#     ctx = get_ctx(ctx_seid)
#     if found_ip:
#         ctx["ue_ip"] = found_ip
#     if found_imsi:
#         ctx["imsi"] = found_imsi

#     # --- NEW: Link SEIDs on type 51 (this fixes Open5GS UE-IP loss) ---
#     # From your debug:
#     #   type 50: fseid=[SMF_SEID]
#     #   type 51: hdr_seid=SMF_SEID, fseid=[UPF_SEID]
#     #   type 52: hdr_seid=UPF_SEID
#     if msg_type == 51 and hdr_seid and peer_fseid:
#         # Make both SEIDs resolve to the same canonical context (choose SMF SEID as canonical)
#         seid_alias.setdefault(hdr_seid, hdr_seid)
#         seid_alias[peer_fseid] = hdr_seid

#         # Merge any data from peer ctx into hdr ctx (and vice versa)
#         a = get_ctx(hdr_seid)
#         b = get_ctx(peer_fseid)
#         merge_ctx(a, b)
#         merge_ctx(b, a)

#         # refresh ctx to canonical
#         ctx = get_ctx(hdr_seid)

#     # --- pass 2: UL candidates ---
#     if msg_type == 51:
#         for ie in ies:
#             if ie_name(ie) == "IE_CreatedPDR":
#                 inner = extract_all_ies(ie)
#                 for x in inner:
#                     if ie_name(x) == "IE_FTEID":
#                         teid_val = get_attr(x, "TEID", "teid")
#                         teid_hex = normalize_teid(teid_val)
#                         if teid_hex:
#                             ul_candidates.append(teid_hex)

#     if not ul_candidates:
#         for ie in ies:
#             if ie_name(ie) == "IE_FTEID":
#                 teid_val = get_attr(ie, "TEID", "teid")
#                 teid_hex = normalize_teid(teid_val)
#                 if teid_hex:
#                     ul_candidates.append(teid_hex)

#     # --- pass 3: DL candidates with context (Destination Interface == Access) ---
#     def walk_with_context(node, dest_if=None):
#         if hasattr(node, "ietype"):
#             n = ie_name(node)

#             if n == "IE_DestinationInterface":
#                 di = get_attr(node, "interface", "Interface", "dst_iface", "destination_interface")
#                 dest_if = di if di is not None else dest_if

#             if n == "IE_OuterHeaderCreation":
#                 if is_access_interface(dest_if):
#                     teid_val = get_attr(node, "TEID", "teid")
#                     teid_hex = normalize_teid(teid_val)
#                     if teid_hex:
#                         dl_candidates.append(teid_hex)

#         for ch in getattr(node, "IE_list", []) or []:
#             walk_with_context(ch, dest_if)

#     walk_with_context(pfcp, None)

#     # --- choose UL TEID ---
#     ul_teid = None
#     if ul_candidates:
#         if len(ul_candidates) > 1:
#             for cand in ul_candidates:
#                 if looks_like_ngap_known_teid(cand):
#                     ul_teid = cand
#                     break
#         ul_teid = ul_teid or ul_candidates[0]

#     # --- choose DL TEID ---
#     dl_teid = None
#     if dl_candidates:
#         if len(dl_candidates) > 1:
#             for cand in dl_candidates:
#                 if looks_like_ngap_known_teid(cand):
#                     dl_teid = cand
#                     break
#         dl_teid = dl_teid or dl_candidates[0]

#     # Store using cached UE IP / IMSI (THIS is the key change)
#     ue_ip = get_ctx(ctx_seid).get("ue_ip")
#     imsi = get_ctx(ctx_seid).get("imsi")

#     if ul_teid:
#         store_teid_hash(ul_teid, ue_ip, "UL", imsi)
#     if dl_teid:
#         store_teid_hash(dl_teid, ue_ip, "DL", imsi)

# def handle_pfcp(pkt):
#     if not pkt.haslayer(PFCP):
#         return
#     try:
#         parse_pfcp(pkt)
#     except Exception:
#         return

# def main():
#     print(f"🚀 PFCP sniffer running on '{interface}' (udp/8805)...")
#     sniff(filter="udp port 8805", iface=interface, prn=handle_pfcp, store=0)

# if __name__ == "__main__":
#     main()


import pyshark
import os
import sys
import redis

# -------------------------
# Config
# -------------------------
interface = os.getenv("amf_sniffer_iface")
if not interface:
    if len(sys.argv) > 1:
        interface = sys.argv[1]
    else:
        print("❌ Error: No interface provided. Set amf_sniffer_iface or pass as argument.")
        sys.exit(1)

redis_host = os.getenv("REDIS_HOST", "redis")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

ue_sessions = {}

# -------------------------
# Helpers
# -------------------------
def normalize_teid(teid) -> str | None:
    """Return 8-hex lowercase string or None."""
    if teid is None:
        return None
    try:
        s = str(teid).strip().replace(":", "").lower()
        if s.startswith("0x"):
            s = s[2:]
        return f"{int(s, 16):08x}"
    except Exception:
        return None

def get_all_ngap_teids(ngap) -> list[str]:
    """Collect all occurrences of gtp_teid exposed by tshark (Open5GS often nests it)."""
    out = []
    try:
        vals = ngap.get_field_values("gtp_teid") or []
        for v in vals:
            nv = normalize_teid(v)
            if nv and nv not in out:
                out.append(nv)
    except Exception:
        pass

    try:
        v = getattr(ngap, "gtp_teid", None)
        nv = normalize_teid(v)
        if nv and nv not in out:
            out.append(nv)
    except Exception:
        pass

    return out

def redis_hset_merge(key: str, mapping: dict):
    existing = rdb.hgetall(key) or {}
    to_write = {}

    for k, v in mapping.items():
        if v is None:
            continue
        v = str(v)
        if not v:
            continue

        # Don't overwrite good ue_ip/imsi from PFCP if already present
        if k in ("ue_ip", "imsi"):
            cur = existing.get(k, "")
            if cur and cur != "unknown":
                continue
            if v == "unknown":
                continue

        to_write[k] = v

    if to_write:
        rdb.hset(key, mapping=to_write)

def store_ngap_teids_to_redis(ue: dict):
    ul = normalize_teid(ue.get("UL_TEID"))
    dl = normalize_teid(ue.get("DL_TEID"))
    if not ul or not dl:
        return

    ran = str(ue.get("RAN_UE_ID"))
    sst = ue.get("SST", "unknown")
    sd  = ue.get("SD", "unknown")
    imsi = ue.get("IMSI", None)

    base = {"ran_ue_id": ran, "sst": sst, "sd": sd}
    if imsi:
        base["imsi"] = imsi

    redis_hset_merge(f"teid:{ul}", {**base, "dir": "UL"})
    redis_hset_merge(f"teid:{dl}", {**base, "dir": "DL"})

    # per-RAN convenience key
    rdb.hset(f"ran:{ran}", mapping={"ul_teid": ul, "dl_teid": dl, "sst": sst, "sd": sd})

    print(f"[✓][AMF] ran_ue_id={ran} UL={ul} DL={dl} sst={sst} sd={sd}")

def redis_has_teid(teid_hex8: str) -> bool:
    if not teid_hex8:
        return False
    try:
        return bool(rdb.exists(f"teid:{teid_hex8}"))
    except Exception:
        return False

def cleanup_ran(ran_id: int, reason: str = ""):
    """
    Remove RAN entry and local cache. Optionally deletes teid hashes referenced by ran:<id>.
    Safe if SMF already deleted them.
    """
    ran_key = f"ran:{ran_id}"
    ran_info = rdb.hgetall(ran_key) or {}

    ul = ran_info.get("ul_teid")
    dl = ran_info.get("dl_teid")

    # Remove per-RAN key
    rdb.delete(ran_key)

    # Optional: also delete TEID hashes if they still exist (won't hurt)
    if ul:
        rdb.delete(f"teid:{ul}")
    if dl:
        rdb.delete(f"teid:{dl}")

    # Drop local in-memory state so we can relearn on next attach
    ue_sessions.pop(ran_id, None)

    print(f"[🧹][AMF] Removed ran_ue_id={ran_id} ({reason or 'UEContextRelease'}) "
          f"ul={ul or '-'} dl={dl or '-'}")

def is_ue_context_release(ngap) -> bool:
    """
    Robust detection across tshark/pyshark versions:
    - check known field markers if present
    - fallback to string match
    """
    # field-based (may vary)
    for attr in (
        "uecontextreleasecommand_element",
        "uecontextreleasecomplete_element",
        "uecontextreleaserequest_element",
    ):
        if hasattr(ngap, attr):
            return True

    s = str(ngap)
    return ("UEContextReleaseCommand" in s) or ("UEContextReleaseComplete" in s) or ("UEContextReleaseRequest" in s)

def try_log_if_complete(ue):
    ul = normalize_teid(ue.get("UL_TEID"))
    dl = normalize_teid(ue.get("DL_TEID"))

    if not (ue.get("RAN_UE_ID") and ul and dl):
        return

    # If we previously logged, but Redis entries are gone, re-log.
    if ue.get("logged"):
        if (not redis_has_teid(ul)) or (not redis_has_teid(dl)):
            ue["logged"] = False

    # If TEIDs changed, re-log
    prev_ul = ue.get("_prev_ul")
    prev_dl = ue.get("_prev_dl")
    if prev_ul and prev_ul != ul:
        ue["logged"] = False
    if prev_dl and prev_dl != dl:
        ue["logged"] = False

    if not ue.get("logged"):
        store_ngap_teids_to_redis(ue)
        ue["logged"] = True
        ue["_prev_ul"] = ul
        ue["_prev_dl"] = dl

# -------------------------
# Packet processing
# -------------------------
def process_packet(pkt):
    if "ngap" not in pkt:
        return
    ngap = pkt["ngap"]

    ran_id = getattr(ngap, "ran_ue_ngap_id", None)
    if not ran_id:
        return
    ran_id = int(ran_id)

    # ✅ NEW: detect UE Context Release and delete ran:<id>
    if is_ue_context_release(ngap):
        cleanup_ran(ran_id, reason="UEContextRelease")
        return

    ue = ue_sessions.setdefault(ran_id, {"RAN_UE_ID": ran_id})

    # Optional IMSI-ish (may be missing/ciphered)
    if "IMSI" not in ue and hasattr(ngap, "nas_5gs_mm_suci_msin"):
        ue["IMSI"] = str(ngap.nas_5gs_mm_suci_msin)

    # Slice
    if "SST" not in ue and hasattr(ngap, "sst"):
        ue["SST"] = str(ngap.sst)

    if "SD" not in ue and hasattr(ngap, "sd"):
        sd_val = str(ngap.sd).replace(":", "").lower()
        ue["SD"] = "empty" if sd_val == "ffffff" else sd_val

    # TEIDs
    teids = get_all_ngap_teids(ngap)
    if not teids:
        return

    is_request = hasattr(ngap, "pdusessionresourcesetuprequest_element") or "PDUSessionResourceSetupRequest" in str(ngap)
    is_response = hasattr(ngap, "pdusessionresourcesetupresponse_element") or "PDUSessionResourceSetupResponse" in str(ngap)

    if is_request:
        new_ul = teids[0]
        if "UL_TEID" not in ue or normalize_teid(ue.get("UL_TEID")) != normalize_teid(new_ul):
            ue["UL_TEID"] = new_ul
            ue["logged"] = False

    if is_response:
        new_dl = teids[0]
        if "DL_TEID" not in ue or normalize_teid(ue.get("DL_TEID")) != normalize_teid(new_dl):
            ue["DL_TEID"] = new_dl
            ue["logged"] = False

    try_log_if_complete(ue)

print(f"[*] Starting pyshark NGAP sniffer on interface '{interface}'...")
capture = pyshark.LiveCapture(interface=interface, bpf_filter="sctp")

for pkt in capture.sniff_continuously():
    try:
        process_packet(pkt)
    except Exception:
        continue

# import pyshark
# import os
# import sys
# import redis

# # -------------------------
# # Config
# # -------------------------
# interface = os.getenv("amf_sniffer_iface")
# if not interface:
#     if len(sys.argv) > 1:
#         interface = sys.argv[1]
#     else:
#         print("❌ Error: No interface provided. Set amf_sniffer_iface or pass as argument.")
#         sys.exit(1)

# redis_host = os.getenv("REDIS_HOST", "redis")
# rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

# ue_sessions = {}

# # -------------------------
# # Helpers
# # -------------------------
# def normalize_teid(teid) -> str | None:
#     """Return 8-hex lowercase string or None."""
#     if teid is None:
#         return None
#     try:
#         s = str(teid).strip().replace(":", "").lower()
#         if s.startswith("0x"):
#             s = s[2:]
#         # TEID sometimes already 8 hex; ensure int->format works
#         return f"{int(s, 16):08x}"
#     except Exception:
#         return None

# def get_all_ngap_teids(ngap) -> list[str]:
#     """Collect all occurrences of gtp_teid exposed by tshark (Open5GS often nests it)."""
#     out = []
#     try:
#         vals = ngap.get_field_values("gtp_teid") or []
#         for v in vals:
#             nv = normalize_teid(v)
#             if nv and nv not in out:
#                 out.append(nv)
#     except Exception:
#         pass

#     # fallback
#     try:
#         v = getattr(ngap, "gtp_teid", None)
#         nv = normalize_teid(v)
#         if nv and nv not in out:
#             out.append(nv)
#     except Exception:
#         pass

#     return out

# def redis_hset_merge(key: str, mapping: dict):
#     """
#     Merge into redis hash WITHOUT wiping fields written by other sniffer.
#     Only sets keys for which value is not None/empty and doesn't overwrite good ue_ip/imsi.
#     """
#     existing = rdb.hgetall(key) or {}
#     to_write = {}

#     for k, v in mapping.items():
#         if v is None:
#             continue
#         v = str(v)
#         if not v:
#             continue

#         # Don't overwrite good ue_ip/imsi from PFCP if already present
#         if k in ("ue_ip", "imsi"):
#             cur = existing.get(k, "")
#             if cur and cur != "unknown":
#                 continue
#             if v == "unknown":
#                 continue

#         to_write[k] = v

#     if to_write:
#         rdb.hset(key, mapping=to_write)

# def store_ngap_teids_to_redis(ue: dict):
#     ul = normalize_teid(ue.get("UL_TEID"))
#     dl = normalize_teid(ue.get("DL_TEID"))
#     if not ul or not dl:
#         return

#     ran = str(ue.get("RAN_UE_ID"))
#     sst = ue.get("SST", "unknown")
#     sd  = ue.get("SD", "unknown")

#     # In Open5GS, "IMSI" from NGAP may be MSIN-like or absent (NAS ciphered). Keep it but don't rely on it.
#     imsi = ue.get("IMSI", None)

#     base = {
#         "ran_ue_id": ran,
#         "sst": sst,
#         "sd": sd,
#     }
#     if imsi:
#         base["imsi"] = imsi

#     redis_hset_merge(f"teid:{ul}", {**base, "dir": "UL"})
#     redis_hset_merge(f"teid:{dl}", {**base, "dir": "DL"})

#     # Optional per-RAN convenience key (helps debugging)
#     rdb.hset(f"ran:{ran}", mapping={"ul_teid": ul, "dl_teid": dl, "sst": sst, "sd": sd})

#     print(f"[✓][AMF] ran_ue_id={ran} UL={ul} DL={dl} sst={sst} sd={sd}")

# def try_log_if_complete(ue):
#     if ue.get("logged"):
#         return
#     if ue.get("RAN_UE_ID") and ue.get("UL_TEID") and ue.get("DL_TEID"):
#         store_ngap_teids_to_redis(ue)
#         ue["logged"] = True

# # -------------------------
# # Packet processing
# # -------------------------
# def process_packet(pkt):
#     if "ngap" not in pkt:
#         return
#     ngap = pkt["ngap"]

#     ran_id = getattr(ngap, "ran_ue_ngap_id", None)
#     if not ran_id:
#         return
#     ran_id = int(ran_id)
#     ue = ue_sessions.setdefault(ran_id, {"RAN_UE_ID": ran_id})

#     # Optional IMSI-ish (may be missing/ciphered)
#     if "IMSI" not in ue and hasattr(ngap, "nas_5gs_mm_suci_msin"):
#         ue["IMSI"] = str(ngap.nas_5gs_mm_suci_msin)

#     # Slice
#     if "SST" not in ue and hasattr(ngap, "sst"):
#         ue["SST"] = str(ngap.sst)

#     if "SD" not in ue and hasattr(ngap, "sd"):
#         sd_val = str(ngap.sd).replace(":", "").lower()
#         ue["SD"] = "empty" if sd_val == "ffffff" else sd_val

#     # TEIDs: robust extraction
#     teids = get_all_ngap_teids(ngap)
#     if not teids:
#         return

#     # Determine message type
#     is_request = hasattr(ngap, "pdusessionresourcesetuprequest_element") or "PDUSessionResourceSetupRequest" in str(ngap)
#     is_response = hasattr(ngap, "pdusessionresourcesetupresponse_element") or "PDUSessionResourceSetupResponse" in str(ngap)

#     # In your captures there's typically one TEID per message of this type.
#     if is_request and "UL_TEID" not in ue:
#         ue["UL_TEID"] = teids[0]
#     if is_response and "DL_TEID" not in ue:
#         ue["DL_TEID"] = teids[0]

#     try_log_if_complete(ue)

# print(f"[*] Starting pyshark NGAP sniffer on interface '{interface}'...")
# capture = pyshark.LiveCapture(interface=interface, bpf_filter="sctp")

# for pkt in capture.sniff_continuously():
#     try:
#         process_packet(pkt)
#     except Exception:
#         continue


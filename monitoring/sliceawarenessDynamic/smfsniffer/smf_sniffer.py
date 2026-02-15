import os
import redis
import time
from scapy.all import sniff
from scapy.contrib.pfcp import PFCP
from collections import defaultdict
import sys

# Get sniff interface from env or exit
interface = os.getenv("smf_sniffer_iface")
if not interface:
    if len(sys.argv) > 1:
        interface = sys.argv[1]
    else:
        print("❌ Error: No interface provided. Set smf_sniffer_iface or pass as argument.")
        sys.exit(1)

# Connect to Redis
redis_host = os.getenv("REDIS_HOST", "redis")
print(f"🔗 Connecting to Redis at {redis_host}:6379...")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

# Internal state
ue_sessions = defaultdict(dict)
seq_to_ue_ip = {}

def extract_all_ies(obj):
    ies = []
    stack = [obj]
    while stack:
        current = stack.pop()
        if hasattr(current, 'ietype'):
            ies.append(current)
        if hasattr(current, 'IE_list'):
            stack.extend(current.IE_list)
    return ies

def normalize_teid(teid):
    """
    Return 8-hex lowercase string without 0x.
    Accepts int, '0x..', decimal string, etc.
    """
    try:
        if isinstance(teid, str):
            teid = teid.strip()
            if teid.startswith("0x"):
                teid = int(teid, 16)
            elif teid.isdigit():
                teid = int(teid)
            else:
                # maybe already hex without 0x
                teid = int(teid, 16)
        return f"{int(teid):08x}"
    except Exception:
        return None

def update_ue_record_from_teid(teid_key: str, ue_ip: str):
    """
    If AMF sniffer already stored imsi/sst/sd in teid:<teid>,
    then enrich ue:<imsi> with ue_ip and last_seen.
    """
    try:
        data = rdb.hgetall(teid_key) or {}
        imsi = data.get("imsi")
        if not imsi:
            return

        now = str(int(time.time()))

        # Update canonical UE record if it exists (or create minimal one)
        ue_key = f"ue:{imsi}"
        existing = rdb.hgetall(ue_key) or {}

        # Keep any existing fields from AMF (sst/sd/ul_teid/dl_teid/ran_ue_id)
        patch = {"last_seen": now}
        if ue_ip and ue_ip != "unknown":
            patch["ue_ip"] = ue_ip

        # If ue record doesn't exist yet, create minimal with what we know
        if not existing:
            patch["imsi"] = imsi
            if data.get("ran_ue_id"):
                patch["ran_ue_id"] = data.get("ran_ue_id")
            if data.get("sst"):
                patch["sst"] = (data.get("sst") or "").lower()
            if data.get("sd"):
                patch["sd"] = (data.get("sd") or "").lower()

        rdb.hset(ue_key, mapping=patch)

        # Helpful reverse map: UE IP -> IMSI (optional but practical)
        if ue_ip and ue_ip != "unknown":
            rdb.set(f"ipimsi:{ue_ip}", imsi)

    except Exception:
        # keep sniffer robust
        return

def store_teid_in_redis(teid, ue_ip, direction):
    normalized_teid = normalize_teid(teid)
    if not normalized_teid or not ue_ip or direction not in {"UL", "DL"}:
        return

    teid_key = f"teid:{normalized_teid}"
    now = str(int(time.time()))

    print(f"[>] Storing TEID mapping: {teid_key} → ip={ue_ip}, dir={direction}")

    # Update teid hash (does NOT overwrite other fields like imsi/sst/sd)
    rdb.hset(teid_key, mapping={
        "ue_ip": ue_ip,
        "dir": direction,
        "last_seen": now,
    })

    # Track TEIDs seen for this UE IP (helps later for TTL cleanup)
    rdb.sadd(f"ueip:{ue_ip}:teids", normalized_teid)
    rdb.hset(f"ueip:{ue_ip}", mapping={"last_seen": now})

    # Backward-compatible mapping: ip:<ue_ip> -> UL_TEID
    # Only set if direction is UL (as you originally did)
    if direction == "UL":
        rdb.set(f"ip:{ue_ip}", normalized_teid)

    # NEW: if AMF already stored IMSI etc under this teid, enrich ue:<imsi>
    update_ue_record_from_teid(teid_key, ue_ip)

def handle_pfcp(pkt):
    if not pkt.haslayer(PFCP):
        return

    pfcp = pkt[PFCP]
    msg_type = pfcp.message_type
    seq = pfcp.seq

    all_ies = extract_all_ies(pfcp)
    found_ue_ip = None
    ul_teid = None
    dl_teid = None

    # Find UE IP in IE_UE_IP_Address if present
    for ie in all_ies:
        if type(ie).__name__ == "IE_UE_IP_Address":
            found_ue_ip = getattr(ie, 'ipv4', None)
            break

    # PFCP Session Establishment Request is typically type 50
    # Store seq -> UE IP to bind later responses
    if msg_type == 50 and found_ue_ip and seq:
        seq_to_ue_ip[seq] = found_ue_ip
        ue_sessions[found_ue_ip]

    current_ue_ip = found_ue_ip or seq_to_ue_ip.get(seq)
    if not current_ue_ip:
        return

    for ie in all_ies:
        ie_type = type(ie).__name__

        # Often carries UL F-TEID
        if ie_type == "IE_FTEID":
            teid = getattr(ie, 'TEID', None)
            if teid is not None:
                ul_teid = teid  # keep as int here; normalize later

        # Often carries DL Outer Header Creation TEID
        elif ie_type == "IE_OuterHeaderCreation":
            teid = getattr(ie, 'TEID', None)
            if teid is not None:
                dl_teid = teid

    if current_ue_ip:
        if dl_teid is not None:
            ue_sessions[current_ue_ip]['DL_TEID'] = f"0x{int(dl_teid):08x}"
            store_teid_in_redis(dl_teid, current_ue_ip, "DL")
        if ul_teid is not None:
            ue_sessions[current_ue_ip]['UL_TEID'] = f"0x{int(ul_teid):08x}"
            store_teid_in_redis(ul_teid, current_ue_ip, "UL")

def main():
    print(f"🚀 PFCP UE-to-TEID sniffer is running on interface '{interface}'...")
    sniff(filter="udp port 8805", iface=interface, prn=handle_pfcp, store=0)

if __name__ == "__main__":
    main()
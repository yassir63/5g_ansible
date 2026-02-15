import os
import redis
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
    try:
        if isinstance(teid, str) and teid.startswith("0x"):
            teid = int(teid, 16)
        elif isinstance(teid, str) and teid.isdigit():
            teid = int(teid)
        return f"{teid:08x}"
    except Exception as e:
        # print(f"[!] Failed to normalize TEID '{teid}': {e}")
        return None

def store_teid_in_redis(teid, ue_ip, direction):
    normalized_teid = normalize_teid(teid)
    if not normalized_teid or not ue_ip or direction not in {"UL", "DL"}:
        # print(f"[!] Skipping invalid TEID mapping: teid={teid}, ip={ue_ip}, dir={direction}")
        return

    teid_key = f"teid:{normalized_teid}"
    print(f"[>] Storing TEID mapping: {teid_key} → ip={ue_ip}, dir={direction}")
    existing_data = rdb.hgetall(teid_key)
    # if existing_data:
        # print(f"[↺] Existing data for {teid_key}: {existing_data}")

    updated_data = {
        "ue_ip": ue_ip,
        "dir": direction,
    }
    rdb.hset(teid_key, mapping=updated_data)

    if direction == "UL":
        rdb.set(f"ip:{ue_ip}", normalized_teid)
        # print(f"[+] Set Redis key ip:{ue_ip} → {normalized_teid}")

def handle_pfcp(pkt):
    if not pkt.haslayer(PFCP):
        return

    pfcp = pkt[PFCP]
    msg_type = pfcp.message_type
    seq = pfcp.seq
    # print(f"\n📦 PFCP message: type={msg_type}, seq={seq}")

    all_ies = extract_all_ies(pfcp)
    found_ue_ip = None
    ul_teid = None
    dl_teid = None

    for ie in all_ies:
        if type(ie).__name__ == "IE_UE_IP_Address":
            found_ue_ip = getattr(ie, 'ipv4', None)
            # print(f"[✓] Found UE IP: {found_ue_ip}")
            break

    if msg_type == 50 and found_ue_ip and seq:
        seq_to_ue_ip[seq] = found_ue_ip
        ue_sessions[found_ue_ip]
        # print(f"[✳] Stored sequence mapping: seq={seq} → ip={found_ue_ip}")

    current_ue_ip = found_ue_ip or seq_to_ue_ip.get(seq)
    if not current_ue_ip:
        # print(f"[!] No UE IP found for seq={seq}, skipping")
        return

    for ie in all_ies:
        ie_type = type(ie).__name__

        if ie_type == "IE_FTEID":
            teid = getattr(ie, 'TEID', None)
            if teid is not None:
                ul_teid = f"0x{teid:08x}"
                # print(f"[UL] Found UL TEID: {ul_teid}")

        elif ie_type == "IE_OuterHeaderCreation":
            teid = getattr(ie, 'TEID', None)
            if teid is not None:
                dl_teid = f"0x{teid:08x}"
                # print(f"[DL] Found DL TEID: {dl_teid}")

    if current_ue_ip:
        if dl_teid:
            ue_sessions[current_ue_ip]['DL_TEID'] = dl_teid
            store_teid_in_redis(dl_teid, current_ue_ip, "DL")
        if ul_teid:
            ue_sessions[current_ue_ip]['UL_TEID'] = ul_teid
            store_teid_in_redis(ul_teid, current_ue_ip, "UL")
        # print(f"[✓] Mapping for {current_ue_ip}: {ue_sessions[current_ue_ip]}")

def main():
    print(f"🚀 PFCP UE-to-TEID sniffer is running on interface '{interface}'...")
    sniff(filter="udp port 8805", iface=interface, prn=handle_pfcp, store=0)

if __name__ == "__main__":
    main()




# import os
# import redis
# from scapy.all import sniff
# from scapy.contrib.pfcp import PFCP
# from collections import defaultdict

# # Get sniff interface from env or exit
# interface = os.getenv("smf_sniffer_iface")
# if not interface:
#     print("❌ Error: Set 'smf_sniffer_iface' env variable.")
#     exit(1)

# # Connect to Redis
# redis_host = os.getenv("REDIS_HOST", "redis")
# rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

# # Internal state
# ue_sessions = defaultdict(dict)
# seq_to_ue_ip = {}

# def extract_all_ies(obj):
#     ies = []
#     stack = [obj]
#     while stack:
#         current = stack.pop()
#         if hasattr(current, 'ietype'):
#             ies.append(current)
#         if hasattr(current, 'IE_list'):
#             stack.extend(current.IE_list)
#     return ies

# def normalize_teid(teid):
#     """Ensure TEID is stored as lowercase 8-digit hex string without '0x'."""
#     try:
#         if isinstance(teid, str) and teid.startswith("0x"):
#             teid = int(teid, 16)
#         elif isinstance(teid, str) and teid.isdigit():
#             teid = int(teid)
#         return f"{teid:08x}"
#     except Exception as e:
#         print(f"[!] Failed to normalize TEID '{teid}': {e}")
#         return None

# def store_teid_in_redis(teid, ue_ip, direction):
#     normalized_teid = normalize_teid(teid)
#     if not normalized_teid or not ue_ip or direction not in {"UL", "DL"}:
#         print(f"[!] Skipping invalid TEID mapping: teid={teid}, ip={ue_ip}, dir={direction}")
#         return

#     teid_key = f"teid:{normalized_teid}"
#     existing_data = rdb.hgetall(teid_key)

#     updated_data = {
#         "ue_ip": ue_ip,
#         "dir": direction,
#     }

#     # Merge with existing values if present
#     if existing_data:
#         updated_data = {**{k.decode(): v.decode() for k, v in existing_data.items()}, **updated_data}

#     rdb.hset(teid_key, mapping=updated_data)

#     if direction == "UL":
#         rdb.set(f"ip:{ue_ip}", normalized_teid)



        
# def handle_pfcp(pkt):
#     if not pkt.haslayer(PFCP):
#         return

#     pfcp = pkt[PFCP]
#     msg_type = pfcp.message_type
#     seq = pfcp.seq

#     all_ies = extract_all_ies(pfcp)
#     found_ue_ip = None
#     ul_teid = None
#     dl_teid = None

#     for ie in all_ies:
#         if type(ie).__name__ == "IE_UE_IP_Address":
#             found_ue_ip = getattr(ie, 'ipv4', None)
#             break

#     if msg_type == 50 and found_ue_ip and seq:
#         seq_to_ue_ip[seq] = found_ue_ip
#         ue_sessions[found_ue_ip]  # initialize entry

#     current_ue_ip = found_ue_ip or seq_to_ue_ip.get(seq)

#     for ie in all_ies:
#         ie_type = type(ie).__name__

#         if ie_type == "IE_FTEID":
#             teid = getattr(ie, 'TEID', None)
#             if teid is not None:
#                 ul_teid = f"0x{teid:08x}"

#         elif ie_type == "IE_OuterHeaderCreation":
#             teid = getattr(ie, 'TEID', None)
#             if teid is not None:
#                 dl_teid = f"0x{teid:08x}"

#     if current_ue_ip:
#         if dl_teid:
#             ue_sessions[current_ue_ip]['DL_TEID'] = dl_teid
#             store_teid_in_redis(dl_teid, current_ue_ip, "DL")
#         if ul_teid:
#             ue_sessions[current_ue_ip]['UL_TEID'] = ul_teid
#             store_teid_in_redis(ul_teid, current_ue_ip, "UL")

#         print(f"[✓] Mapping for {current_ue_ip}: {ue_sessions[current_ue_ip]}")

# def main():
#     print(f"[*] PFCP UE-to-TEID sniffer is running on interface '{interface}'...")
#     sniff(filter="udp port 8805", iface=interface, prn=handle_pfcp, store=0)

# if __name__ == "__main__":
#     main()

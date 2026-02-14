import pyshark
import os
import sys
import redis

# Get interface
interface = os.getenv("amf_sniffer_iface")
if not interface:
    if len(sys.argv) > 1:
        interface = sys.argv[1]
    else:
        print("❌ Error: No interface provided. Set amf_sniffer_iface or pass as argument.")
        sys.exit(1)

# Connect to Redis
redis_host = os.getenv("REDIS_HOST", "redis")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

ue_sessions = {}

def normalize_teid(teid):
    try:
        return format(int(teid, 16), '08x') if teid.startswith("0x") else format(int(teid), '08x')
    except:
        return teid

# def store_ue_session_in_redis(ue):
#     ul_teid = normalize_teid(ue['UL_TEID'])
#     dl_teid = normalize_teid(ue['DL_TEID'])

#     # Get existing ue_ip from Redis if any
#     existing = rdb.hgetall(f"teid:{ul_teid}")
#     existing_ip = existing.get(b"ue_ip", b"").decode() if existing else ""

#     # Decide what to store
#     new_ip = ue.get("ue_ip", "unknown")
#     ue_ip = existing_ip if existing_ip and existing_ip != "unknown" else new_ip

#     # Prepare base mapping (skip ue_ip if it's "unknown")
#     base_data = {
#         'imsi': ue['IMSI'],
#         'sst': ue['SST'],
#         'sd': ue['SD'],
#         'ran_ue_id': str(ue['RAN_UE_ID'])
#     }

#     # Add ue_ip only if it's valid
#     if ue_ip != "unknown":
#         base_data["ue_ip"] = ue_ip

#     # Write hashes
#     rdb.hset(f"teid:{ul_teid}", mapping={**base_data, "dir": "UL"})
#     rdb.hset(f"teid:{dl_teid}", mapping={**base_data, "dir": "DL"})

#     # IMSI → TEID map (always write)
#     if ue.get("IMSI") and ul_teid:
#         rdb.set(f"imsi:{ue['IMSI']}", ul_teid)

#     # IP → TEID map (only if IP is known)
#     if ue_ip != "unknown" and ul_teid:
#         rdb.set(f"ip:{ue_ip}", ul_teid)

def store_ue_session_in_redis(ue):
    ul_teid = normalize_teid(ue['UL_TEID'])
    dl_teid = normalize_teid(ue['DL_TEID'])

    # Try to get existing ue_ip from UL or DL TEID
    existing_ip_ul = rdb.hget(f"teid:{ul_teid}", "ue_ip") or ""
    existing_ip_dl = rdb.hget(f"teid:{dl_teid}", "ue_ip") or ""

    # Final ue_ip is the most trustworthy known one
    new_ip = ue.get("ue_ip", "unknown")
    ue_ip = next(ip for ip in [existing_ip_ul, existing_ip_dl, new_ip] if ip and ip != "unknown") or "unknown"

    base_data = {
        'imsi': ue['IMSI'],
        'sst': ue['SST'],
        'sd': ue['SD'],
        'ran_ue_id': str(ue['RAN_UE_ID']),
        'ue_ip': ue_ip
    }

    # Set both TEIDs in Redis
    rdb.hset(f"teid:{ul_teid}", mapping={**base_data, "dir": "UL"})
    rdb.hset(f"teid:{dl_teid}", mapping={**base_data, "dir": "DL"})

    # Map IMSI → UL TEID
    if ue.get("IMSI") and ul_teid:
        rdb.set(f"imsi:{ue['IMSI']}", ul_teid)

    # Map UE IP → UL TEID only if IP is known
    if ue_ip != "unknown" and ul_teid:
        rdb.set(f"ip:{ue_ip}", ul_teid)

    print(f"[✓] Stored IMSI:{ue['IMSI']} → UL:{ul_teid}, DL:{dl_teid}, IP:{ue_ip}")



def print_ue_session(ue):
    log_line = (
        f"[✓] UE Found:\n"
        f"    IMSI: {ue['IMSI']}\n"
        f"    RAN_UE_ID: {ue['RAN_UE_ID']}\n"
        f"    SST: {ue['SST']}\n"
        f"    SD: {ue['SD']}\n"
        f"    UL_TEID: {ue['UL_TEID']}\n"
        f"    DL_TEID: {ue['DL_TEID']}\n"
    )
    print(log_line)

def try_log_if_complete(ue):
    required_keys = ['IMSI', 'RAN_UE_ID', 'SST', 'SD', 'UL_TEID', 'DL_TEID']
    if all(key in ue for key in required_keys) and not ue.get("logged"):
        print_ue_session(ue)
        store_ue_session_in_redis(ue)
        ue["logged"] = True

def process_packet(pkt):
    if 'ngap' not in pkt:
        return

    ngap = pkt['ngap']
    ran_id = getattr(ngap, 'ran_ue_ngap_id', None)
    if not ran_id:
        return

    ran_id = int(ran_id)
    ue = ue_sessions.setdefault(ran_id, {'RAN_UE_ID': ran_id})

    if hasattr(ngap, 'nas_5gs_mm_suci_msin') and 'IMSI' not in ue:
        ue['IMSI'] = ngap.nas_5gs_mm_suci_msin

    if hasattr(ngap, 'sst') and 'SST' not in ue:
        ue['SST'] = ngap.sst

    if hasattr(ngap, 'sd') and 'SD' not in ue:
        sd_val = ngap.sd.replace(":", "").lower()
        ue['SD'] = 'empty' if sd_val == 'ffffff' else sd_val

    if hasattr(ngap, 'gtp_teid') and hasattr(ngap, 'pdusessionresourcesetuprequest_element'):
        ul_teid = ngap.gtp_teid.replace(':', '')
        if 'UL_TEID' not in ue:
            ue['UL_TEID'] = ul_teid

    if hasattr(ngap, 'gtp_teid') and hasattr(ngap, 'pdusessionresourcesetupresponse_element'):
        dl_teid = ngap.gtp_teid.replace(':', '')
        if 'DL_TEID' not in ue:
            ue['DL_TEID'] = dl_teid

    try_log_if_complete(ue)

print(f"[*] Starting pyshark NGAP sniffer on interface '{interface}'...")
capture = pyshark.LiveCapture(interface=interface, bpf_filter="sctp")

for pkt in capture.sniff_continuously():
    try:
        process_packet(pkt)
    except Exception:
        continue

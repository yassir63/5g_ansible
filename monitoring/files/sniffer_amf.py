import pyshark
import datetime

interface = 'n3'
log_file = 'ue_mapping.log'

ue_sessions = {}

def log_ue_session(ue):
    with open(log_file, 'a') as f:
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
        f.write(log_line + "\n")

def try_log_if_complete(ue):
    required_keys = ['IMSI', 'RAN_UE_ID', 'SST', 'SD', 'UL_TEID', 'DL_TEID']
    if all(key in ue for key in required_keys) and not ue.get("logged"):
        log_ue_session(ue)
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

    # Extract IMSI
    if hasattr(ngap, 'nas_5gs_mm_suci_msin') and 'IMSI' not in ue:
        ue['IMSI'] = ngap.nas_5gs_mm_suci_msin

    # Extract SST
    if hasattr(ngap, 'sst') and 'SST' not in ue:
        ue['SST'] = ngap.sst

    # Extract SD
    if hasattr(ngap, 'sd') and 'SD' not in ue:
        sd_val = ngap.sd.replace(":", "").lower()
        ue['SD'] = 'empty' if sd_val == 'ffffff' else sd_val

    # UL TEID from SetupRequest
    if hasattr(ngap, 'gtp_teid') and hasattr(ngap, 'pdusessionresourcesetuprequest_element'):
        ul_teid = ngap.gtp_teid.replace(':', '')
        if 'UL_TEID' not in ue:
            ue['UL_TEID'] = ul_teid

    # DL TEID from SetupResponse
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
    except Exception as e:
        continue  # suppress tracebacks for noisy data

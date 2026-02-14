from scapy.all import sniff
from scapy.contrib.pfcp import PFCP
from collections import defaultdict

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

def handle_pfcp(pkt):
    if not pkt.haslayer(PFCP):
        return

    pfcp = pkt[PFCP]
    msg_type = pfcp.message_type
    seq = pfcp.seq
    # print(seq)

    # seid = getattr(pfcp, 'seid', None)

    all_ies = extract_all_ies(pfcp)
    found_ue_ip = None
    ul_teid = None
    dl_teid = None

    # Try to extract UE IP
    for ie in all_ies:
        if type(ie).__name__ == "IE_UE_IP_Address":
            found_ue_ip = getattr(ie, 'ipv4', None)
            break

    # If this is a session establishment request, cache UE IP under seq
    if msg_type == 50 and found_ue_ip and seq:
        seq_to_ue_ip[seq] = found_ue_ip
        ue_sessions[found_ue_ip]  # init if not exists

    current_ue_ip = found_ue_ip or seq_to_ue_ip.get(seq)

    # TEID extraction
    for ie in all_ies:
        ie_type = type(ie).__name__

        if ie_type == "IE_FTEID":
            teid = getattr(ie, 'TEID', None)
            if teid is not None:
                ul_teid = f"0x{teid:08x}"

        elif ie_type == "IE_OuterHeaderCreation":
            teid = getattr(ie, 'TEID', None)
            if teid is not None:
                dl_teid = f"0x{teid:08x}"

    if current_ue_ip:
        if dl_teid:
            ue_sessions[current_ue_ip]['DL_TEID'] = dl_teid
        if ul_teid:
            ue_sessions[current_ue_ip]['UL_TEID'] = ul_teid
        print(f"[✓] Mapping for {current_ue_ip}: {ue_sessions[current_ue_ip]}")

def main():
    print("[*] PFCP UE-to-TEID sniffer is running...")
    sniff(filter="udp port 8805", iface="eth0", prn=handle_pfcp, store=0)

if __name__ == "__main__":
    main()

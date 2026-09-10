#!/usr/bin/env python3
"""Reconstruct TCP ACK RTT samples from GTP-U pcaps.

This helper shells out to tshark when present,
extracts inner TCP fields from GTP-U packets, and matches TCP data packets to
the reverse ACK carrying the expected cumulative ACK number.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path


TSHARK_FIELDS = [
    "frame.time_epoch",
    "gtp.teid",
    "ip.src",
    "ip.dst",
    "tcp.srcport",
    "tcp.dstport",
    "tcp.seq_raw",
    "tcp.ack_raw",
    "tcp.seq",
    "tcp.ack",
    "tcp.len",
    "tcp.flags.syn",
    "tcp.flags.fin",
    "tcp.flags.ack",
]


def choose_last(value: str) -> str:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return parts[-1] if parts else ""


def choose_first(value: str) -> str:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return parts[0] if parts else ""


def as_int(value: str, default: int = 0) -> int:
    value = choose_last(value)
    if not value:
        return default
    lowered = value.lower()
    if lowered in {"true", "yes"}:
        return 1
    if lowered in {"false", "no"}:
        return 0
    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return default


def as_float(value: str, default: float = 0.0) -> float:
    value = choose_last(value)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def iter_tshark_rows(pcap: Path):
    cmd = [
        "tshark",
        "-r",
        str(pcap),
        "-o",
        "tcp.relative_sequence_numbers:false",
        "-Y",
        "gtp && tcp",
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=a",
    ]
    for field in TSHARK_FIELDS:
        cmd.extend(["-e", field])

    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"tshark exited with {proc.returncode}")

    for line in proc.stdout.splitlines():
        cols = line.split("\t")
        cols.extend([""] * (len(TSHARK_FIELDS) - len(cols)))
        yield dict(zip(TSHARK_FIELDS, cols))


def reconstruct_pcap(pcap: Path, writer: csv.DictWriter, max_pending_per_ack: int) -> int:
    pending = defaultdict(deque)
    emitted = 0

    for row in iter_tshark_rows(pcap):
        ts = as_float(row["frame.time_epoch"])
        teid = choose_first(row["gtp.teid"])
        src = choose_last(row["ip.src"])
        dst = choose_last(row["ip.dst"])
        sport = choose_last(row["tcp.srcport"])
        dport = choose_last(row["tcp.dstport"])

        if not (ts and src and dst and sport and dport):
            continue

        seq = as_int(row["tcp.seq_raw"], default=as_int(row["tcp.seq"]))
        ack = as_int(row["tcp.ack_raw"], default=as_int(row["tcp.ack"]))
        payload_len = as_int(row["tcp.len"])
        syn = as_int(row["tcp.flags.syn"])
        fin = as_int(row["tcp.flags.fin"])
        ack_flag = as_int(row["tcp.flags.ack"])

        if ack_flag and ack:
            ack_key = (src, dst, sport, dport, ack)
            if pending[ack_key]:
                data = pending[ack_key].popleft()
                rtt_ms = (ts - data["timestamp"]) * 1000.0
                if rtt_ms >= 0:
                    writer.writerow(
                        {
                            "pcap_file": str(pcap),
                            "timestamp": data["timestamp"],
                            "ack_timestamp": ts,
                            "rtt_ms": f"{rtt_ms:.6f}",
                            "data_src": data["src"],
                            "data_dst": data["dst"],
                            "data_sport": data["sport"],
                            "data_dport": data["dport"],
                            "ack_src": src,
                            "ack_dst": dst,
                            "ack_sport": sport,
                            "ack_dport": dport,
                            "expected_ack": ack,
                            "payload_len": data["payload_len"],
                            "data_teid": data["teid"],
                            "ack_teid": teid,
                        }
                    )
                    emitted += 1

        tcp_advance = payload_len + (1 if syn else 0) + (1 if fin else 0)
        if tcp_advance > 0 and seq:
            expected_ack = seq + tcp_advance
            reverse_key = (dst, src, dport, sport, expected_ack)
            queue = pending[reverse_key]
            queue.append(
                {
                    "timestamp": ts,
                    "src": src,
                    "dst": dst,
                    "sport": sport,
                    "dport": dport,
                    "payload_len": payload_len,
                    "teid": teid,
                }
            )
            while len(queue) > max_pending_per_ack:
                queue.popleft()

    return emitted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", action="append", default=[])
    parser.add_argument("--pcap-dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-pending-per-ack", type=int, default=4096)
    args = parser.parse_args()

    pcaps = [Path(item) for item in args.pcap]
    if args.pcap_dir:
        pcaps.extend(sorted(Path(args.pcap_dir).glob("*.pcap")))
        pcaps.extend(sorted(Path(args.pcap_dir).glob("*.pcapng")))
        pcaps.extend(sorted(Path(args.pcap_dir).glob("*.pcap.gz")))
        pcaps.extend(sorted(Path(args.pcap_dir).glob("*.pcapng.gz")))

    pcaps = [pcap for pcap in pcaps if pcap.exists()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "pcap_file",
        "timestamp",
        "ack_timestamp",
        "rtt_ms",
        "data_src",
        "data_dst",
        "data_sport",
        "data_dport",
        "ack_src",
        "ack_dst",
        "ack_sport",
        "ack_dport",
        "expected_ack",
        "payload_len",
        "data_teid",
        "ack_teid",
    ]

    with out.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()

        if not pcaps:
            print("warning: no pcap files found", file=sys.stderr)
            return 0

        if not shutil.which("tshark"):
            print("warning: tshark is not installed; pcap RTT extraction skipped", file=sys.stderr)
            return 0

        total = 0
        for pcap in pcaps:
            try:
                count = reconstruct_pcap(pcap, writer, args.max_pending_per_ack)
                total += count
                print(f"{pcap}: emitted {count} RTT samples")
            except Exception as exc:  # noqa: BLE001
                print(f"warning: failed to parse {pcap}: {exc}", file=sys.stderr)

    print(f"wrote reconstructed pcap RTT samples to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

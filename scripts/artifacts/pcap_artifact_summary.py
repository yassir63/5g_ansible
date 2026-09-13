#!/usr/bin/env python3
"""Summarize experiment pcap artifacts and tcpdump logs.

The script intentionally uses only the Python standard library so it can run
from the SLICES webshell without dependency setup.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_log_status(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "status=started" in text:
        return "started", ""
    if "status=tcpdump_not_found" in text:
        return "tcpdump_not_found", "tcpdump was missing and auto-install did not make it available"
    if "status=kubectl_not_found" in text:
        return "kubectl_not_found", "kubectl was not available on the selected control host"
    if "status=pod_not_found" in text:
        return "pod_not_found", "no pod matched the configured namespace/regex"
    if "No such file" in text or "not found" in text:
        return "not_found", "capture artifact or command was not found"
    return "log_only", ""


def summarize(pcap_dir: Path) -> dict:
    windows = []
    if not pcap_dir.exists():
        return {"pcap_dir": str(pcap_dir), "windows": [], "totals": {"pcaps": 0, "logs": 0}}

    for window_dir in sorted(p for p in pcap_dir.iterdir() if p.is_dir()):
        pcaps = sorted(window_dir.glob("*.pcap")) + sorted(window_dir.glob("*.pcapng"))
        logs = sorted(window_dir.glob("*.log"))
        pcap_by_stem = {p.stem: p for p in pcaps}
        targets = []
        for log in logs:
            stem = log.name
            for suffix in [".tcpdump.log", ".pod-discovery.log", ".pod-stop.log", ".log"]:
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            pcap = pcap_by_stem.get(stem)
            status, reason = parse_log_status(log)
            if pcap and pcap.exists() and pcap.stat().st_size > 0:
                status = "captured"
                reason = ""
            targets.append(
                {
                    "name": stem,
                    "status": status,
                    "reason": reason,
                    "pcap": str(pcap.relative_to(pcap_dir)) if pcap else "",
                    "pcap_bytes": pcap.stat().st_size if pcap and pcap.exists() else 0,
                    "log": str(log.relative_to(pcap_dir)),
                }
            )
        for pcap in pcaps:
            if pcap.stem not in {item["name"] for item in targets}:
                targets.append(
                    {
                        "name": pcap.stem,
                        "status": "captured" if pcap.stat().st_size > 0 else "empty",
                        "reason": "",
                        "pcap": str(pcap.relative_to(pcap_dir)),
                        "pcap_bytes": pcap.stat().st_size,
                        "log": "",
                    }
                )
        windows.append({"window": window_dir.name, "targets": targets})

    return {
        "pcap_dir": str(pcap_dir),
        "windows": windows,
        "totals": {
            "pcaps": sum(len(window["targets"]) for window in windows),
            "captured": sum(
                1
                for window in windows
                for target in window["targets"]
                if target["status"] == "captured"
            ),
            "not_found": sum(
                1
                for window in windows
                for target in window["targets"]
                if target["status"] in {"tcpdump_not_found", "kubectl_not_found", "pod_not_found", "not_found"}
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    summary = summarize(Path(args.pcap_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote pcap summary to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

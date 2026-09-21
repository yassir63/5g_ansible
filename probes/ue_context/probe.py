#!/usr/bin/env python3
"""Record sampled UE inventory history without changing the mapper or 5G stack."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request


UE_FIELDS = (
    "imsi", "ue_ip", "ran_ue_id", "slice_id", "sst", "sd", "ul_teid",
    "dl_teid", "source", "last_seen", "seid", "pdu_session_id",
)


def inventory_record(payload: dict, limit: int) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("ues"), list):
        raise ValueError("Expected an inventory object with a ues list")
    rows = payload["ues"]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Inventory contains a non-object UE row")
    reported_count = payload.get("count", len(rows))
    reported_limit = payload.get("limit", limit)
    if (type(reported_count) is not int or reported_count < 0
            or type(reported_limit) is not int or reported_limit < 1):
        raise ValueError("Invalid inventory count or limit")
    selected_rows = [{key: row[key] for key in UE_FIELDS if key in row} for row in rows]
    json.dumps(selected_rows, allow_nan=False)
    return {
        "status": "ok",
        "count": len(rows),
        "reported_count": reported_count,
        "limit": reported_limit,
        "possible_truncation": len(rows) >= min(limit, reported_limit) or reported_count > len(rows),
        "ues": selected_rows,
    }


def kubeconfig_args(requested: str) -> list[str]:
    if requested != "auto":
        return ["--kubeconfig", requested]
    if os.environ.get("KUBECONFIG"):
        return []
    for path in (Path.home() / ".kube/config", Path("/root/.kube/config"), Path("/etc/kubernetes/admin.conf")):
        if path.is_file() and os.access(path, os.R_OK):
            return ["--kubeconfig", str(path)]
    return []


def fetch_inventory(args, kube_args: list[str]) -> dict:
    if args.url:
        url = args.url.rstrip("/") + "/inventory/ues?" + urllib.parse.urlencode({"limit": args.limit})
        with urllib.request.urlopen(url, timeout=args.timeout_seconds) as response:
            return json.load(response)
    path = (
        f"/api/v1/namespaces/{args.namespace}/services/http:{args.service}:80/proxy/"
        f"inventory/ues?limit={args.limit}"
    )
    result = subprocess.run(
        ["kubectl", *kube_args, f"--request-timeout={args.timeout_seconds}s", "get", "--raw", path],
        capture_output=True, text=True, timeout=args.timeout_seconds + 2,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:1000] or "kubectl service proxy failed")
    return json.loads(result.stdout)


def sample(args, fetch, stop: threading.Event) -> dict:
    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    history = output / "history.jsonl"
    summary = {"run_id": args.run_id, "started_at": time.time(), "samples": 0, "errors": 0,
               "possibly_truncated_samples": 0, "interval_seconds": args.interval_seconds,
               "max_seconds": args.max_seconds, "history_type": "sampled_inventory"}
    deadline = time.monotonic() + args.max_seconds
    with history.open("x", encoding="utf-8") as stream:
        while True:
            start = time.monotonic()
            final = stop.is_set() or Path(args.stop_file).exists()
            expired = start >= deadline
            record = {"run_id": args.run_id, "sequence": summary["samples"],
                      "request_started_at": time.time(), "final": final or expired}
            try:
                record.update(inventory_record(fetch(), args.limit))
                summary["possibly_truncated_samples"] += int(record["possible_truncation"])
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                # A failed request must not look like an empty UE inventory.
                record.update(status="error", error=str(exc)[:1000])
                summary["errors"] += 1
            record["observed_at"] = time.time()
            record["request_duration_seconds"] = time.monotonic() - start
            stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()
            summary["samples"] += 1
            if summary["samples"] == 1:
                (output / "ready.json").write_text(json.dumps(record), encoding="utf-8")
            if final or expired:
                summary["stop_reason"] = "requested" if final else "max_seconds"
                break
            wait_until = min(deadline, start + args.interval_seconds)
            while time.monotonic() < wait_until and not Path(args.stop_file).exists():
                if stop.wait(min(0.2, max(0, wait_until - time.monotonic()))):
                    break
    summary["finished_at"] = time.time()
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--url", default="", help="Mapper API base URL; otherwise use the Kubernetes service proxy")
    parser.add_argument("--namespace", default="monitoring")
    parser.add_argument("--service", default="ue-mapper-api")
    parser.add_argument("--kubeconfig", default="auto")
    parser.add_argument("--interval-seconds", type=float, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=5)
    parser.add_argument("--max-seconds", type=int, default=7200)
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    if (not math.isfinite(args.interval_seconds) or args.interval_seconds < 0.1
            or min(args.timeout_seconds, args.max_seconds, args.limit) < 1):
        parser.error("Positive timeout, duration and limit, and interval >= 0.1 are required")
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    kube_args = kubeconfig_args(args.kubeconfig) if not args.url else []
    print(json.dumps(sample(args, lambda: fetch_inventory(args, kube_args), stop)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

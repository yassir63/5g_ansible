#!/usr/bin/env python3
"""Export Prometheus query ranges to a flat CSV file.

The script intentionally uses only Python's standard library so it can run on
the webshell without a dependency install step.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_QUERIES = [
    {
        "name": "direct_rtt_mean_ms",
        "query": """
(
  sum by (imsi, ue_ip, slice, probe_role, mode) (
    rate(gtp_teid_latency_observed_ns_sum[30s])
  )
/
  sum by (imsi, ue_ip, slice, probe_role, mode) (
    rate(gtp_teid_latency_observed_ns_count[30s])
  )
) / 1000000
""",
    },
    {
        "name": "direct_rtt_p95_ms",
        "query": """
histogram_quantile(
  0.95,
  sum by (le, imsi, ue_ip, slice, probe_role, mode) (
    rate(gtp_teid_latency_observed_ns_bucket[30s])
  )
) / 1000000
""",
    },
    {
        "name": "direct_median_1s_ms",
        "query": "gtp_teid_latency_median_1s_ns / 1000000",
    },
    {
        "name": "direct_event_rate_hz",
        "query": """
sum by (imsi, ue_ip, slice, probe_role, mode) (
  rate(gtp_teid_latency_observed_ns_count[30s])
)
""",
    },
    {
        "name": "direct_latest_ms",
        "query": "gtp_teid_latency_ns / 1000000",
    },
    {
        "name": "same_packet_upf_rtt_mean_ms",
        "query": """
(
  sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
    rate(gtp_same_packet_upf_rtt_ns_sum[30s])
  )
/
  sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
    rate(gtp_same_packet_upf_rtt_ns_count[30s])
  )
) / 1000000
""",
    },
    {
        "name": "same_packet_gnb_rtt_mean_ms",
        "query": """
(
  sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
    rate(gtp_same_packet_gnb_rtt_ns_sum[30s])
  )
/
  sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
    rate(gtp_same_packet_gnb_rtt_ns_count[30s])
  )
) / 1000000
""",
    },
    {
        "name": "same_packet_gap_mean_ms",
        "query": """
(
  sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
    rate(gtp_same_packet_rtt_gap_ns_sum[30s])
  )
/
  sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
    rate(gtp_same_packet_rtt_gap_ns_count[30s])
  )
) / 1000000
""",
    },
    {
        "name": "same_packet_pair_rate_hz",
        "query": """
sum by (imsi, ue_ip, slice, probe_role, mode, direction) (
  rate(gtp_same_packet_pairs_total[30s])
)
""",
    },
    {
        "name": "lost_observation_events_hz",
        "query": """
sum by (probe_role, stream) (
  rate(gtp_teid_latency_lost_events_total[30s])
)
""",
    },
    {
        "name": "container_cpu_cores",
        "query": """
sum by (namespace, pod, container) (
  rate(container_cpu_usage_seconds_total{container!="POD", image!=""}[30s])
)
""",
    },
    {
        "name": "container_memory_mib",
        "query": """
sum by (namespace, pod, container) (
  container_memory_working_set_bytes{container!="POD", image!=""}
) / 1024 / 1024
""",
    },
]

COMMON_LABELS = [
    "__name__",
    "imsi",
    "ue_ip",
    "slice",
    "probe_role",
    "mode",
    "direction",
    "kind",
    "proto",
    "protocol",
    "reason",
    "teid",
    "ue_rnti",
    "rnti",
    "round",
    "instance",
    "pod",
    "namespace",
    "container",
    "job",
    "stream",
]


def clean_query(query: str) -> str:
    return " ".join(query.strip().split())


def http_json(url: str, params: dict[str, object] | None = None, timeout: int = 30) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_queries(path: str | None) -> list[dict[str, str]]:
    if not path:
        return list(DEFAULT_QUERIES)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    queries = []
    for item in data:
        if not item.get("name") or not item.get("query"):
            raise ValueError(f"Invalid query item: {item!r}")
        queries.append({"name": str(item["name"]), "query": str(item["query"])})
    return queries


def discover_metric_queries(
    prometheus_url: str,
    regex: str,
    limit: int,
) -> list[dict[str, str]]:
    try:
        payload = http_json(f"{prometheus_url.rstrip('/')}/api/v1/label/__name__/values")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: metric discovery failed: {exc}", file=sys.stderr)
        return []

    names = payload.get("data", []) if payload.get("status") == "success" else []
    pattern = re.compile(regex)
    selected = [name for name in names if pattern.search(name)]
    selected = sorted(selected)[:limit]
    return [{"name": f"rf_{name}", "query": name} for name in selected]


def query_range(
    prometheus_url: str,
    query: str,
    start: float,
    end: float,
    step: str,
) -> dict:
    return http_json(
        f"{prometheus_url.rstrip('/')}/api/v1/query_range",
        {
            "query": clean_query(query),
            "start": start,
            "end": end,
            "step": step,
        },
        timeout=60,
    )


def write_rows(
    writer: csv.DictWriter,
    query_name: str,
    query: str,
    result: dict,
) -> int:
    count = 0
    for series in result.get("data", {}).get("result", []):
        metric = series.get("metric", {})
        labels = {label: metric.get(label, "") for label in COMMON_LABELS}
        for timestamp, value in series.get("values", []):
            row = {
                "query_name": query_name,
                "query": clean_query(query),
                "timestamp": timestamp,
                "value": value,
                "metric_json": json.dumps(metric, sort_keys=True),
            }
            row.update(labels)
            writer.writerow(row)
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--start", required=True, type=float)
    parser.add_argument("--end", required=True, type=float)
    parser.add_argument("--step", default="2s")
    parser.add_argument("--out", required=True)
    parser.add_argument("--queries-json")
    parser.add_argument(
        "--discover-metrics-regex",
        default=r"(?i)(mcs|bler|harq|cqi|rsrp|rsrq|sinr|snr|gtp_tcp|tcp_)",
    )
    parser.add_argument("--discover-metrics-limit", type=int, default=40)
    parser.add_argument("--no-discover-metrics", action="store_true")
    parser.add_argument("--summary-json")
    args = parser.parse_args()

    if args.end <= args.start:
        raise SystemExit("--end must be greater than --start")

    queries = load_queries(args.queries_json)
    if not args.no_discover_metrics:
        queries.extend(
            discover_metric_queries(
                args.prometheus_url,
                args.discover_metrics_regex,
                args.discover_metrics_limit,
            )
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "prometheus_url": args.prometheus_url,
        "start": args.start,
        "end": args.end,
        "step": args.step,
        "queries": [],
        "created_at": time.time(),
    }

    fields = ["query_name", "query", "timestamp", "value", "metric_json", *COMMON_LABELS]
    with out_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()

        for item in queries:
            name = item["name"]
            query = item["query"]
            query_summary = {"name": name, "query": clean_query(query), "rows": 0}
            try:
                payload = query_range(args.prometheus_url, query, args.start, args.end, args.step)
                if payload.get("status") != "success":
                    query_summary["error"] = json.dumps(payload)[:500]
                else:
                    query_summary["rows"] = write_rows(writer, name, query, payload)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                query_summary["error"] = str(exc)
                print(f"warning: query {name!r} failed: {exc}", file=sys.stderr)
            summary["queries"].append(query_summary)

    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    total_rows = sum(int(item.get("rows", 0)) for item in summary["queries"])
    print(f"wrote {total_rows} Prometheus samples to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

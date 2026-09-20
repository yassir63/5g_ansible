#!/usr/bin/env python3
"""Read-only availability check using the same queries as experiment exports."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path

from prometheus_range_export import http_json, load_queries, query_range


def summarize_query(payload: dict) -> dict:
    if payload.get("status") != "success":
        return {"status": "error", "error": payload.get("error", str(payload))}
    if payload.get("data", {}).get("resultType") != "matrix":
        return {"status": "error", "error": "Expected a range-query matrix"}
    series = payload["data"].get("result", [])
    finite_samples = 0
    non_finite_samples = 0
    for item in series:
        for _, value in item.get("values", []):
            if math.isfinite(float(value)):
                finite_samples += 1
            else:
                non_finite_samples += 1
    status = "available" if finite_samples else "empty"
    if non_finite_samples:
        status = "partial" if finite_samples else "non_finite"
    return {
        "status": status,
        "series": len(series),
        "finite_samples": finite_samples,
        "non_finite_samples": non_finite_samples,
        "label_examples": [item.get("metric", {}) for item in series[:3]],
        "warnings": payload.get("warnings", []),
    }


def check_targets(prometheus_url: str) -> dict:
    payload = http_json(f"{prometheus_url.rstrip('/')}/api/v1/targets", {"state": "active"})
    if payload.get("status") != "success":
        raise ValueError(payload.get("error", "Could not read scrape targets"))
    targets = payload.get("data", {}).get("activeTargets", [])
    return {
        "status": "available" if targets else "empty",
        "count": len(targets),
        "targets": [
            {
                "labels": item.get("labels", {}),
                "health": item.get("health", "unknown"),
                "last_error": item.get("lastError", ""),
                "last_scrape": item.get("lastScrape", ""),
            }
            for item in targets
        ],
    }


def check_loki(loki_url: str, selector: str, start: float, end: float) -> dict:
    payload = http_json(
        f"{loki_url.rstrip('/')}/loki/api/v1/query_range",
        {
            "query": selector,
            "start": str(int(start * 1_000_000_000)),
            "end": str(int(end * 1_000_000_000)),
            "limit": 1,
            "direction": "backward",
        },
    )
    if payload.get("status") != "success":
        raise ValueError(payload.get("error", "Loki query failed"))
    if payload.get("data", {}).get("resultType") != "streams":
        raise ValueError("Expected a Loki log-stream result")
    result = payload["data"].get("result", [])
    # Verify ingestion without copying application log content into the report.
    count = sum(len(stream.get("values", [])) for stream in result)
    return {"status": "available" if count else "empty", "selector": selector}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--loki-url")
    parser.add_argument("--loki-selector", default='{namespace=~".+"}')
    parser.add_argument(
        "--queries-json",
        default=str(Path(__file__).resolve().parents[2] / "configs/artifacts/default_prometheus_queries.json"),
    )
    parser.add_argument("--lookback-seconds", type=int, default=300)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.lookback_seconds < 120:
        parser.error("--lookback-seconds must be at least 120 for rate queries")
    queries = load_queries(args.queries_json)
    end = time.time()
    start = end - args.lookback_seconds
    report = {"start": start, "end": end, "queries": []}
    errors = False
    try:
        report["scrape_targets"] = check_targets(args.prometheus_url)
    except (OSError, ValueError, TimeoutError) as exc:
        report["scrape_targets"] = {"status": "error", "error": str(exc)}
        errors = True
    for query in queries:
        try:
            status = summarize_query(query_range(args.prometheus_url, query["query"], start, end, "15s"))
        except (OSError, ValueError, TimeoutError) as exc:
            status = {"status": "error", "error": str(exc)}
        item = {**query, **status}
        report["queries"].append(item)
        errors |= item["status"] == "error"
        print(f"{item['status']:12} {item['name']} ({item.get('series', 0)} series)", flush=True)
    if args.loki_url:
        try:
            report["loki"] = check_loki(args.loki_url, args.loki_selector, start, end)
        except (OSError, ValueError, TimeoutError) as exc:
            report["loki"] = {"status": "error", "error": str(exc)}
        errors |= report["loki"]["status"] == "error"
        print(f"Loki: {report['loki']['status']}")
    report["counts"] = dict(Counter(item["status"] for item in report["queries"]))
    targets = report["scrape_targets"].get("targets", [])
    unhealthy = [item for item in targets if item["health"] != "up"]
    report["unhealthy_targets"] = unhealthy
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nQuery availability: {report['counts']}; unhealthy targets: {len(unhealthy)}")
    print(f"Report: {out}")
    print("Empty results are unknown/conditional, not healthy zeros. Review coverage per node and pod.")
    return 2 if errors or unhealthy else 0


if __name__ == "__main__":
    raise SystemExit(main())

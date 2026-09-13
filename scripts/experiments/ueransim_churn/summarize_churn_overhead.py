#!/usr/bin/env python3
"""Summarize UERANSIM churn Prometheus overhead exports."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


def component_name(row: dict[str, str]) -> str:
    text = " ".join(
        str(row.get(key, "")).lower()
        for key in ["namespace", "pod", "container", "query_name", "__name__"]
    )
    for marker, name in [
        ("ue-mapper", "ue-mapper"),
        ("prometheus", "prometheus"),
        ("ueransim", "ueransim-ue"),
        ("mongodb", "mongodb"),
        ("mongo", "mongodb"),
        ("redis", "redis"),
        ("amf", "amf"),
        ("smf", "smf"),
        ("upf", "upf"),
    ]:
        if marker in text:
            return name
    return "other"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def summarize(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        try:
            value = float(row.get("value", ""))
            timestamp = float(row.get("timestamp", "0"))
        except ValueError:
            continue
        key = (
            row.get("query_name", ""),
            component_name(row),
            row.get("namespace", ""),
            row.get("pod", ""),
            row.get("container", ""),
            row.get("__name__", ""),
        )
        groups[key].append((timestamp, value))

    out = []
    for key, points in groups.items():
        query_name, component, namespace, pod, container, metric_name = key
        values = [value for _, value in points]
        timestamps = [timestamp for timestamp, _ in points]
        out.append(
            {
                "query_name": query_name,
                "component": component,
                "namespace": namespace,
                "pod": pod,
                "container": container,
                "metric_name": metric_name,
                "samples": len(values),
                "first_epoch": min(timestamps),
                "last_epoch": max(timestamps),
                "mean": statistics.fmean(values),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "max": max(values),
            }
        )
    return sorted(out, key=lambda row: (str(row["query_name"]), str(row["component"]), str(row["pod"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Prometheus flat CSV export.")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    out_dir = Path(args.out_dir)
    rows = read_rows(csv_path)
    summary = summarize(rows)

    fields = [
        "query_name",
        "component",
        "namespace",
        "pod",
        "container",
        "metric_name",
        "samples",
        "first_epoch",
        "last_epoch",
        "mean",
        "p50",
        "p95",
        "max",
    ]
    write_csv(out_dir / "overhead_summary.csv", summary, fields)

    focused = [
        row
        for row in summary
        if row["component"] in {"ue-mapper", "amf", "smf", "upf", "ueransim-ue"}
    ]
    write_csv(out_dir / "focused_overhead_summary.csv", focused, fields)

    mapper = [row for row in summary if row["component"] == "ue-mapper"]
    write_csv(out_dir / "ue_mapper_overhead_summary.csv", mapper, fields)

    sniffers = [
        row
        for row in summary
        if "sniffer" in str(row["container"]).lower()
        or str(row["query_name"]).startswith("mapper_sniffer_")
    ]
    write_csv(out_dir / "sniffer_overhead_summary.csv", sniffers, fields)

    notes = {
        "source_csv": str(csv_path),
        "rows_read": len(rows),
        "series_summarized": len(summary),
        "files": [
            "overhead_summary.csv",
            "focused_overhead_summary.csv",
            "ue_mapper_overhead_summary.csv",
            "sniffer_overhead_summary.csv",
        ],
    }
    (out_dir / "overhead_summary.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(f"wrote {len(summary)} overhead summary rows to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

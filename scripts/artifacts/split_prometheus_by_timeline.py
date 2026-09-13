#!/usr/bin/env python3
"""Split a full Prometheus CSV export into scenario/task time-window folders."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from pathlib import Path


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.=-]+", "_", value.strip())
    return value.strip("_") or "window"


def load_windows(path: Path, selected_levels: set[str]) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    windows = []
    for row in data.get("windows", []):
        if row.get("level") in selected_levels:
            windows.append(dict(row))
    return windows


def window_subdir(window: dict) -> Path:
    level = window.get("level", "window")
    if level == "scenario":
        return Path("scenario") / safe_name(window.get("scenario", "scenario"))
    if level == "step":
        return Path("task") / safe_name(
            "__".join([window.get("scenario", ""), window.get("step", "")])
        )
    if level == "direction":
        return Path("task") / safe_name(
            "__".join(
                [
                    window.get("scenario", ""),
                    window.get("step", ""),
                    window.get("direction", ""),
                ]
            )
        )
    return Path(level) / safe_name(window.get("window_id", "window"))


def to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_false(value) -> bool:
    if isinstance(value, bool):
        return value is False
    return str(value).strip().lower() in {"false", "0", "no", "off"}


def open_csv_text(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, newline="", encoding="utf-8")
    return path.open(mode, newline="", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-csv", required=True)
    parser.add_argument("--timeline-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--window-types", default="scenario,step,direction")
    parser.add_argument("--padding-seconds", type=float, default=0.0)
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Write per-window CSVs as gzip-compressed prometheus_timeseries.csv.gz files.",
    )
    args = parser.parse_args()

    full_csv = Path(args.full_csv)
    timeline_json = Path(args.timeline_json)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_levels = {item.strip() for item in args.window_types.split(",") if item.strip()}
    if not full_csv.exists():
        summary = {"error": f"missing Prometheus CSV: {full_csv}", "windows": []}
        (out_dir / "split_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(summary["error"])
        return 0
    if not timeline_json.exists():
        summary = {"error": f"missing timeline JSON: {timeline_json}", "windows": []}
        (out_dir / "split_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(summary["error"])
        return 0

    windows = load_windows(timeline_json, selected_levels)
    skipped_windows = [
        window for window in windows if is_false(window.get("prometheus_enabled", ""))
    ]
    windows = [
        window for window in windows if not is_false(window.get("prometheus_enabled", ""))
    ]
    if not windows:
        summary = {
            "error": "no matching windows",
            "windows": [],
            "skipped_windows": [
                {
                    "window_id": item.get("window_id"),
                    "level": item.get("level"),
                    "name": item.get("name"),
                    "reason": "prometheus_disabled_for_window",
                }
                for item in skipped_windows
            ],
        }
        (out_dir / "split_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(summary["error"])
        return 0

    writers = []
    summary_windows = []
    with open_csv_text(full_csv, "rt") as source_fp:
        reader = csv.DictReader(source_fp)
        if not reader.fieldnames:
            print(f"empty Prometheus CSV: {full_csv}")
            return 0

        for window in windows:
            subdir = out_dir / window_subdir(window)
            subdir.mkdir(parents=True, exist_ok=True)
            csv_path = subdir / (
                "prometheus_timeseries.csv.gz" if args.compress else "prometheus_timeseries.csv"
            )
            fp = open_csv_text(csv_path, "wt")
            writer = csv.DictWriter(fp, fieldnames=reader.fieldnames)
            writer.writeheader()
            start = to_float(window.get("start_epoch")) - args.padding_seconds
            end = to_float(window.get("end_epoch")) + args.padding_seconds
            metadata = {
                "window": window,
                "source_csv": str(full_csv),
                "padding_seconds": args.padding_seconds,
                "filter_start_epoch": start,
                "filter_end_epoch": end,
                "prometheus_csv": str(csv_path),
                "compressed": bool(args.compress),
                "rows": 0,
            }
            (subdir / "window_metadata.json").write_text(
                json.dumps(metadata, indent=2),
                encoding="utf-8",
            )
            writers.append((start, end, writer, fp, metadata, subdir))

        for row in reader:
            timestamp = to_float(row.get("timestamp"), -1.0)
            for start, end, writer, _fp, metadata, _subdir in writers:
                if start <= timestamp <= end:
                    writer.writerow(row)
                    metadata["rows"] += 1

    for _start, _end, _writer, fp, metadata, subdir in writers:
        fp.close()
        (subdir / "window_metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        summary_windows.append(
            {
                "window_id": metadata["window"].get("window_id"),
                "level": metadata["window"].get("level"),
                "scenario": metadata["window"].get("scenario"),
                "step": metadata["window"].get("step"),
                "direction": metadata["window"].get("direction"),
                "rows": metadata["rows"],
                "directory": str(subdir),
                "prometheus_csv": metadata["prometheus_csv"],
            }
        )

    summary = {
        "source_csv": str(full_csv),
        "timeline_json": str(timeline_json),
        "window_types": sorted(selected_levels),
        "padding_seconds": args.padding_seconds,
        "compressed": bool(args.compress),
        "windows": summary_windows,
        "skipped_windows": [
            {
                "window_id": item.get("window_id"),
                "level": item.get("level"),
                "name": item.get("name"),
                "reason": "prometheus_disabled_for_window",
            }
            for item in skipped_windows
        ],
    }
    (out_dir / "split_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    total_rows = sum(item["rows"] for item in summary_windows)
    print(f"split {full_csv} into {len(summary_windows)} windows with {total_rows} copied rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

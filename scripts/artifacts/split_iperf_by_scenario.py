#!/usr/bin/env python3
"""Extract per-UE iperf artifacts into per-scenario result folders."""

from __future__ import annotations

import argparse
import csv
import re
import json
import tarfile
from pathlib import Path


IPERF_RE = re.compile(
    r"(?:^|/)scenario=(?P<scenario>[^/]+)/step=(?P<step>[^/]+)/"
    r"direction=(?P<direction>[^/]+)/(?P<file>iperf_(?P<ue>[^/.]+)\.(?P<kind>json|stderr))$"
)


def safe_part(value: str) -> str:
    value = value.strip().replace("/", "_")
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value) or "unknown"


def write_bytes(out_path: Path, payload: bytes) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)


def iter_tar_members(path: Path):
    with tarfile.open(path, "r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            match = IPERF_RE.search(member.name)
            if not match:
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            yield match.groupdict(), fh.read(), member.name


def iter_plain_files(results_dir: Path):
    for path in results_dir.rglob("iperf_*.json"):
        match = IPERF_RE.search(path.as_posix())
        if match:
            yield match.groupdict(), path.read_bytes(), str(path)
    for path in results_dir.rglob("iperf_*.stderr"):
        match = IPERF_RE.search(path.as_posix())
        if match:
            yield match.groupdict(), path.read_bytes(), str(path)


def copy_record(groups: dict[str, str], payload: bytes, source: str, out_dir: Path) -> dict[str, str]:
    scenario = safe_part(groups["scenario"])
    step = safe_part(groups["step"])
    direction = safe_part(groups["direction"])
    file_name = safe_part(groups["file"])
    target = out_dir / "scenario" / scenario / "iperf" / step / direction / file_name
    write_bytes(target, payload)
    return {
        "scenario": groups["scenario"],
        "step": groups["step"],
        "direction": groups["direction"],
        "ue": groups["ue"],
        "kind": groups["kind"],
        "source": source,
        "path": str(target),
        "bytes": str(len(payload)),
    }


def load_timeline(results_dir: Path) -> list[dict[str, str]]:
    path = results_dir / "timeline_summary.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_scenario_context(results_dir: Path, out_dir: Path, records: list[dict[str, str]]) -> None:
    timeline_rows = load_timeline(results_dir)
    scenarios = {
        row.get("scenario", "")
        for row in timeline_rows
        if row.get("scenario")
    } | {
        record["scenario"]
        for record in records
        if record.get("scenario")
    }

    for scenario in sorted(scenarios):
        scenario_dir = out_dir / "scenario" / safe_part(scenario)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        scenario_timeline = [row for row in timeline_rows if row.get("scenario") == scenario]
        if scenario_timeline:
            fieldnames = sorted({key for row in scenario_timeline for key in row.keys()})
            timeline_csv = scenario_dir / "timeline_summary.csv"
            with timeline_csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(scenario_timeline)
            (scenario_dir / "timeline_summary.json").write_text(
                json.dumps({"scenario": scenario, "windows": scenario_timeline}, indent=2),
                encoding="utf-8",
            )

        scenario_records = [record for record in records if record.get("scenario") == scenario]
        scenario_window = next(
            (row for row in scenario_timeline if row.get("level") == "scenario"),
            {},
        )
        metadata = {
            "scenario": scenario,
            "scenario_title": scenario_window.get("scenario_title", ""),
            "start_local": scenario_window.get("start_local", ""),
            "end_local": scenario_window.get("end_local", ""),
            "start_epoch": scenario_window.get("start_epoch", ""),
            "end_epoch": scenario_window.get("end_epoch", ""),
            "duration_s": scenario_window.get("duration_s", ""),
            "source_results_dir": str(results_dir),
            "prometheus_csv": str(next(iter(sorted(scenario_dir.glob("prometheus_timeseries.csv*"))), "")),
            "timeline_csv": str(scenario_dir / "timeline_summary.csv"),
            "iperf_dir": str(scenario_dir / "iperf"),
            "iperf_artifacts": scenario_records,
        }
        (scenario_dir / "scenario_metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        (scenario_dir / "README.md").write_text(
            "\n".join(
                [
                    f"# Scenario {scenario}",
                    "",
                    "This folder is self-contained for this scenario window.",
                    "",
                    "- `prometheus_timeseries.csv(.gz)`: all exported Prometheus metrics inside this scenario timeline.",
                    "- `timeline_summary.csv/json`: only this scenario's windows, steps, directions, and interference phases.",
                    "- `iperf/`: qhat iperf JSON/stderr logs for this scenario.",
                    "- `scenario_metadata.json`: paths and extracted iperf inventory.",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--out-dir")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    out_dir = Path(args.out_dir) if args.out_dir else results_dir / "by_window"
    records: list[dict[str, str]] = []

    for tgz in sorted(results_dir.glob("qhat*.tgz")):
        for groups, payload, source in iter_tar_members(tgz):
            records.append(copy_record(groups, payload, f"{tgz.name}:{source}", out_dir))

    for groups, payload, source in iter_plain_files(results_dir):
        if "/by_window/" in source:
            continue
        records.append(copy_record(groups, payload, source, out_dir))

    inventory = out_dir / "scenario" / "iperf_inventory.csv"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    with inventory.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = ["scenario", "step", "direction", "ue", "kind", "bytes", "path", "source"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    write_scenario_context(results_dir, out_dir, records)
    print(f"Extracted {len(records)} iperf artifacts into {out_dir / 'scenario'}")
    print(f"Inventory: {inventory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

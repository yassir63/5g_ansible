#!/usr/bin/env python3
"""Build human- and machine-readable timeline summaries for scenario runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


WINDOW_FIELDS = [
    "window_id",
    "level",
    "scenario",
    "scenario_title",
    "step",
    "direction",
    "ue",
    "section_index",
    "runner_type",
    "parallel_streams",
    "traffic_type",
    "ping_count",
    "ping_interval",
    "ping_timeout",
    "ping_size",
    "start_epoch",
    "end_epoch",
    "duration_s",
    "start_local",
    "end_local",
    "timezone",
    "ues",
    "parallel_by_ue",
    "ue_roles",
    "directions",
    "directions_by_ue",
    "interference_node",
    "interference_freq",
    "interference_gain",
    "interference_noise_bandwidth",
    "interference_focus_ue",
    "stress_target",
    "stress_node",
    "stress_workers",
    "stress_namespace",
    "stress_pod_regex",
    "prometheus_enabled",
    "pcap_enabled",
    "artifacts_file",
    "artifact_overrides",
    "scenario_index",
    "step_index",
    "name",
]


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.=-]+", "_", value.strip())
    return value.strip("_") or "window"


def load_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            events.append(
                {
                    "level": "parse_error",
                    "phase": "point",
                    "line_no": line_no,
                    "error": str(exc),
                    "raw": line,
                }
            )
            continue
        event["_line_no"] = line_no
        events.append(event)
    return events


def event_key(event: dict) -> tuple:
    level = event.get("level", "")
    return (
        level,
        event.get("name", ""),
        event.get("scenario", ""),
        event.get("step", ""),
        event.get("direction", ""),
        str(event.get("scenario_index", "")),
        str(event.get("step_index", "")),
    )


def to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_local(epoch: int, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    return datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def json_field(event: dict, field: str, default):
    value = event.get(field, default)
    return value if value is not None else default


def make_window(start: dict, end: dict, tz_name: str) -> dict:
    start_epoch = to_int(start.get("epoch"))
    end_epoch = to_int(end.get("epoch"), start_epoch)
    level = start.get("level", "")
    scenario = start.get("scenario", "")
    step = start.get("step", "")
    direction = start.get("direction", "")
    name = start.get("name", "")
    parts = [level]
    for item in [scenario, step, direction, name]:
        if item:
            parts.append(str(item))
    window_id = safe_name("__".join(parts))
    return {
        "window_id": window_id,
        "level": level,
        "scenario": scenario,
        "scenario_title": start.get("scenario_title") or start.get("title", ""),
        "step": step,
        "direction": direction,
        "ue": "",
        "section_index": to_int(start.get("section_index"), -1),
        "runner_type": start.get("runner_type", ""),
        "parallel_streams": to_int(start.get("parallel_streams"), 0),
        "traffic_type": start.get("traffic_type", "tcp"),
        "ping_count": start.get("ping_count", ""),
        "ping_interval": start.get("ping_interval", ""),
        "ping_timeout": start.get("ping_timeout", ""),
        "ping_size": start.get("ping_size", ""),
        "start_epoch": start_epoch,
        "end_epoch": end_epoch,
        "duration_s": max(0, end_epoch - start_epoch),
        "start_local": to_local(start_epoch, tz_name),
        "end_local": to_local(end_epoch, tz_name),
        "timezone": tz_name,
        "ues": json_field(start, "ues", []),
        "parallel_by_ue": json_field(start, "parallel_by_ue", {}),
        "ue_roles": json_field(start, "ue_roles", {}),
        "directions": json_field(start, "directions", []),
        "directions_by_ue": json_field(start, "directions_by_ue", {}),
        "interference_node": start.get("interference_node", ""),
        "interference_freq": start.get("interference_freq", ""),
        "interference_gain": start.get("interference_gain", ""),
        "interference_noise_bandwidth": start.get("interference_noise_bandwidth", ""),
        "interference_focus_ue": start.get("interference_focus_ue", start.get("interference_target_ue", "")),
        "stress_target": start.get("stress_target", ""),
        "stress_node": start.get("stress_node", ""),
        "stress_workers": start.get("stress_workers", ""),
        "stress_namespace": start.get("stress_namespace", ""),
        "stress_pod_regex": start.get("stress_pod_regex", ""),
        "prometheus_enabled": start.get("prometheus_enabled", ""),
        "pcap_enabled": start.get("pcap_enabled", ""),
        "artifacts_file": start.get("artifacts_file", ""),
        "artifact_overrides": json_field(start, "artifact_overrides", {}),
        "scenario_index": to_int(start.get("scenario_index"), -1),
        "step_index": to_int(start.get("step_index"), -1),
        "name": name,
    }


def pair_windows(events: list[dict], tz_name: str) -> tuple[list[dict], list[dict], list[dict]]:
    starts: dict[tuple, list[dict]] = defaultdict(list)
    windows = []
    unmatched_ends = []

    for event in sorted(events, key=lambda item: (to_int(item.get("epoch")), item.get("_line_no", 0))):
        phase = event.get("phase")
        if phase == "start":
            starts[event_key(event)].append(event)
        elif phase == "end":
            key = event_key(event)
            if starts.get(key):
                start = starts[key].pop()
                windows.append(make_window(start, event, tz_name))
            else:
                unmatched_ends.append(event)

    unmatched_starts = [event for stack in starts.values() for event in stack]
    windows.sort(key=lambda row: (row["start_epoch"], row["end_epoch"], row["level"]))
    return windows, unmatched_starts, unmatched_ends


def expand_iperf_tasks(windows: list[dict]) -> list[dict]:
    tasks = []
    for window in windows:
        if window.get("level") != "direction":
            continue
        traffic_type = str(window.get("traffic_type") or "tcp").lower()
        ues = window.get("ues") or []
        parallel_by_ue = window.get("parallel_by_ue") or {}
        directions_by_ue = window.get("directions_by_ue") or {}
        default_parallel = to_int(window.get("parallel_streams"), 1)
        for ue in ues:
            task = dict(window)
            task["level"] = "ping_task" if traffic_type == "icmp" else "iperf_task"
            task["ue"] = ue
            task["parallel_streams"] = to_int(parallel_by_ue.get(ue), default_parallel)
            if directions_by_ue:
                task["direction"] = directions_by_ue.get(ue, "unknown")
            task["window_id"] = safe_name(
                "__".join(
                    [
                        task["level"],
                        window.get("scenario", ""),
                        window.get("step", ""),
                        task.get("direction", ""),
                        ue,
                        f"P{task['parallel_streams']}" if task["level"] == "iperf_task" else f"count{task.get('ping_count', '')}",
                    ]
                )
            )
            tasks.append(task)
    return tasks


def csv_value(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return value


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=WINDOW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, "")) for field in WINDOW_FIELDS})


def recap_lines(run_id: str, tz_name: str, windows: list[dict], iperf_tasks: list[dict]) -> list[str]:
    lines = [f"Timeline recap for {run_id} ({tz_name})"]
    campaign = next((w for w in windows if w["level"] == "campaign"), None)
    traffic = next((w for w in windows if w["level"] == "traffic_campaign"), None)
    if campaign:
        lines.append(
            f"Campaign: {campaign['start_local']} -> {campaign['end_local']} "
            f"({duration_text(campaign['duration_s'])})"
        )
    if traffic:
        lines.append(
            f"Traffic: {traffic['start_local']} -> {traffic['end_local']} "
            f"({duration_text(traffic['duration_s'])})"
        )

    sections = [w for w in windows if w["level"] == "section"]
    if sections:
        lines.append("Sections:")
        for section in sections:
            title = f" - {section['scenario_title']}" if section.get("scenario_title") else ""
            details = []
            if section.get("runner_type"):
                details.append(f"runner={section.get('runner_type')}")
            if section.get("prometheus_enabled") != "":
                details.append(f"prometheus={section.get('prometheus_enabled')}")
            if section.get("pcap_enabled") != "":
                details.append(f"pcap={section.get('pcap_enabled')}")
            detail_text = f" [{' '.join(details)}]" if details else ""
            lines.append(
                f"- section {section['name']}{title}{detail_text}: "
                f"{section['start_local']} -> {section['end_local']} "
                f"({duration_text(section['duration_s'])})"
            )

    scenarios = [w for w in windows if w["level"] == "scenario"]
    for scenario in scenarios:
        title = f" - {scenario['scenario_title']}" if scenario.get("scenario_title") else ""
        lines.append(
            f"- {scenario['scenario']}{title}: {scenario['start_local']} -> "
            f"{scenario['end_local']} ({duration_text(scenario['duration_s'])})"
        )
        scenario_tasks = [
            t
            for t in iperf_tasks
            if t.get("scenario") == scenario.get("scenario")
        ]
        for task in scenario_tasks:
            if task.get("level") == "ping_task":
                lines.append(
                    f"  - {task['step']} {task['ue']} ICMP ping count={task.get('ping_count', '')}: "
                    f"{task['start_local']} -> {task['end_local']} "
                    f"({duration_text(task['duration_s'])})"
                )
            else:
                lines.append(
                    f"  - {task['step']} {task['ue']} {task['direction']} -P{task['parallel_streams']}: "
                    f"{task['start_local']} -> {task['end_local']} "
                    f"({duration_text(task['duration_s'])})"
                )
        scenario_phases = [
            w
            for w in windows
            if w.get("level") in {"interference_phase", "stress_phase"}
            and w.get("scenario") == scenario.get("scenario")
        ]
        for phase in scenario_phases:
            detail = ""
            if phase.get("interference_node"):
                detail = (
                    f" node={phase.get('interference_node')} "
                    f"freq={phase.get('interference_freq')} "
                    f"gain={phase.get('interference_gain')} "
                    f"bw={phase.get('interference_noise_bandwidth')}"
                )
            elif phase.get("stress_target"):
                detail = (
                    f" target={phase.get('stress_target')} "
                    f"node={phase.get('stress_node')} "
                    f"workers={phase.get('stress_workers')}"
                )
            lines.append(
                f"  - phase {phase['name']}{detail}: "
                f"{phase['start_local']} -> {phase['end_local']} "
                f"({duration_text(phase['duration_s'])})"
            )
    return lines


def write_markdown(path: Path, lines: list[str], summary_json: dict) -> None:
    content = ["# Experiment Timeline", ""]
    content.extend(lines)
    content.extend(
        [
            "",
            "## Output Files",
            "",
            "- `timeline_summary.csv`: paired scenario, step, direction, iperf-task, and ping-task windows.",
            "- `timeline_summary.json`: same data for scripts.",
            "- `timeline_recap.txt`: terminal-friendly recap.",
            "- `by_window/campaign/*/prometheus_timeseries.csv(.gz)`: full campaign metric slices when Prometheus export exists.",
            "- `by_window/section/*/prometheus_timeseries.csv(.gz)`: per-section metric slices for generic experiments.",
            "- `by_window/scenario/*/prometheus_timeseries.csv(.gz)`: per-scenario metric slices when Prometheus export exists.",
            "- `by_window/task/*/prometheus_timeseries.csv(.gz)`: per-step/direction metric slices when Prometheus export exists.",
            "- `by_window/interference_phase/*/prometheus_timeseries.csv(.gz)`: clean/noise/recovery slices for interference scenarios.",
            "- `by_window/stress_phase/*/prometheus_timeseries.csv(.gz)`: clean/stress/recovery slices for stress scenarios.",
            "",
            f"Unmatched starts: {len(summary_json.get('unmatched_starts', []))}",
            f"Unmatched ends: {len(summary_json.get('unmatched_ends', []))}",
            "",
        ]
    )
    path.write_text("\n".join(content), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline-jsonl", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timezone", default="Europe/Paris")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events = load_events(Path(args.timeline_jsonl))
    windows, unmatched_starts, unmatched_ends = pair_windows(events, args.timezone)
    iperf_tasks = expand_iperf_tasks(windows)
    all_rows = windows + iperf_tasks

    run_id = args.run_id or (events[0].get("run_id", "") if events else "")
    summary = {
        "run_id": run_id,
        "timezone": args.timezone,
        "events": events,
        "windows": windows,
        "traffic_tasks": iperf_tasks,
        "iperf_tasks": iperf_tasks,
        "unmatched_starts": unmatched_starts,
        "unmatched_ends": unmatched_ends,
    }

    (out_dir / "timeline_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(out_dir / "timeline_summary.csv", all_rows)
    lines = recap_lines(run_id, args.timezone, windows, iperf_tasks)
    (out_dir / "timeline_recap.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_markdown(out_dir / "timeline_summary.md", lines, summary)
    print("\n".join(lines))
    if unmatched_starts or unmatched_ends:
        print(f"warning: unmatched starts={len(unmatched_starts)} unmatched ends={len(unmatched_ends)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

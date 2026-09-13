#!/usr/bin/env python3
"""Correlate UERANSIM churn evidence across core, sniffers, and UE mapper."""

from __future__ import annotations

import argparse
import csv
import json
import re
import tarfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LOG_TS_RE = re.compile(r"^(?P<timestamp>\S+)\s+(?P<message>.*)$")
GATE_RE = re.compile(
    r"^(?P<timestamp>\S+)\s+wave=(?P<wave>\d+)\s+"
    r"(?P<event>attach release token published|scale-down observed)$"
)
AMF_REGISTRATION_RE = re.compile(r"\[imsi-(\d+)\]\s+Registration complete")
SMF_SESSION_RE = re.compile(
    r"UE SUPI\[imsi-(?P<imsi>\d+)\].*?IPv4\[(?P<ue_ip>[^\]]*)\]"
)
SMF_SNIFFER_RE = re.compile(
    r"teid:(?P<teid>[0-9a-fA-F]+)\s+->\s+dir=(?P<direction>UL|DL)"
    r"\s+ip=(?P<ue_ip>\S+)\s+imsi=(?P<imsi>\d+)"
)
AMF_SNIFFER_RE = re.compile(
    r"ran_ue_id=(?P<ran_ue_id>\d+)\s+UL=(?P<ul>[0-9a-fA-F]+)"
    r"\s+DL=(?P<dl>[0-9a-fA-F]+)"
)


def parse_timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_gate_windows(path: Path) -> list[dict[str, object]]:
    waves: dict[int, dict[str, object]] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = GATE_RE.match(raw.strip())
        if not match:
            continue
        timestamp = parse_timestamp(match.group("timestamp"))
        if timestamp is None:
            continue
        wave = int(match.group("wave"))
        record = waves.setdefault(wave, {"wave": wave})
        if match.group("event").startswith("attach release"):
            record["start_epoch"] = timestamp
            record["start_time"] = match.group("timestamp")
        else:
            record["end_epoch"] = timestamp
            record["end_time"] = match.group("timestamp")

    complete = [
        record
        for record in waves.values()
        if "start_epoch" in record and "end_epoch" in record
    ]
    return sorted(complete, key=lambda record: float(record["start_epoch"]))


def classify_member(name: str) -> str | None:
    if name.endswith("/amf-sniffer.log"):
        return "amf_sniffer"
    if name.endswith("/smf-sniffer.log"):
        return "smf_sniffer"
    if name.endswith("/amf.log") and "/open5gs-amf-" in name:
        return "amf"
    if name.endswith("/smf.log") and "/open5gs-smf" in name:
        return "smf"
    return None


def iter_logs(path: Path):
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            source = classify_member(member.name)
            if source is None or not member.isfile():
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            for raw in stream:
                line = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).strip()
                match = LOG_TS_RE.match(line)
                if not match:
                    continue
                timestamp = parse_timestamp(match.group("timestamp"))
                if timestamp is None:
                    continue
                yield source, timestamp, match.group("timestamp"), match.group("message")


def wave_for_timestamp(
    timestamp: float, windows: list[dict[str, object]]
) -> int | None:
    for record in windows:
        if float(record["start_epoch"]) <= timestamp <= float(record["end_epoch"]):
            return int(record["wave"])
    return None


def first_seen(store: dict, key, timestamp: float, timestamp_text: str) -> None:
    current = store.get(key)
    if current is None or timestamp < current[0]:
        store[key] = (timestamp, timestamp_text)


def read_mapper_samples(
    path: Path, windows: list[dict[str, object]]
) -> tuple[
    set[tuple[int, str]],
    dict[tuple[int, str], str],
    dict[int, set[str]],
]:
    observed: set[tuple[int, str]] = set()
    first: dict[tuple[int, str], tuple[float, str]] = {}
    samples: dict[float, dict[str, tuple[str, str, str]]] = defaultdict(dict)
    sample_labels: dict[float, str] = {}
    if not path.exists():
        return observed, {}, {}

    counts_path = path.with_name("ue_mapper_inventory_counts.csv")
    if counts_path.exists():
        with counts_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                timestamp = float(row["sample_epoch"])
                samples[timestamp]
                sample_labels[timestamp] = str(row.get("sample_utc") or "")

    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            imsi = "".join(char for char in str(row.get("imsi") or "") if char.isdigit())
            if not imsi:
                continue
            timestamp = float(row["sample_epoch"])
            sample_labels[timestamp] = str(row.get("sample_utc") or "")
            samples[timestamp][imsi] = (
                str(row.get("ul_teid") or ""),
                str(row.get("dl_teid") or ""),
                str(row.get("ran_ue_id") or ""),
            )

    stale_by_wave: dict[int, set[str]] = {}
    sample_times = sorted(samples)
    for window in windows:
        wave = int(window["wave"])
        start = float(window["start_epoch"])
        end = float(window["end_epoch"])
        baseline_times = [timestamp for timestamp in sample_times if timestamp < start]
        baseline = samples[baseline_times[-1]] if baseline_times else {}
        stale_by_wave[wave] = set(baseline)

        for timestamp in sample_times:
            if not start <= timestamp <= end:
                continue
            for imsi, signature in samples[timestamp].items():
                if imsi in baseline and baseline[imsi] == signature:
                    continue
                key = (wave, imsi)
                observed.add(key)
                first_seen(
                    first,
                    key,
                    timestamp,
                    sample_labels.get(timestamp)
                    or datetime.fromtimestamp(timestamp).isoformat(),
                )

    return (
        observed,
        {key: value[1] for key, value in first.items()},
        stale_by_wave,
    )


def diagnosis_for(row: dict[str, object], sniffer_logs_present: bool) -> str:
    if row["mapper_observed"]:
        return "complete"
    if not row["amf_registration_complete"]:
        return "ue_or_amf_registration"
    if not row["smf_session_created"]:
        return "core_pdu_session"
    if not sniffer_logs_present:
        return "sniffer_logs_missing"
    if not row["smf_sniffer_observed"]:
        return "smf_sniffer_capture_or_parser"
    if not row["amf_sniffer_observed"]:
        return "amf_sniffer_capture_or_parser"
    return "redis_or_mapper_pairing"


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--imsi-prefix", default="001010000100")
    parser.add_argument("--ordinal-width", type=int, default=3)
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    gate_log = results_dir / "synchronized_attach_gate.log"
    pod_logs = results_dir / "pod_logs.tgz"
    mapper_csv = results_dir / "ue_mapper_inventory_samples.csv"
    if not gate_log.exists():
        raise SystemExit(f"missing {gate_log}")
    if not pod_logs.exists():
        raise SystemExit(f"missing {pod_logs}")

    windows = read_gate_windows(gate_log)
    if not windows:
        raise SystemExit("no complete synchronized attach windows found")

    amf_registrations: dict[tuple[int, str], tuple[float, str]] = {}
    smf_sessions: dict[tuple[int, str], tuple[float, str]] = {}
    smf_session_ips: dict[tuple[int, str], str] = {}
    smf_sniffer: dict[tuple[int, str], tuple[float, str]] = {}
    smf_teids: dict[tuple[int, str], set[str]] = defaultdict(set)
    amf_sniffer_events: dict[int, list[tuple[float, str, set[str]]]] = defaultdict(list)
    source_lines: dict[str, int] = defaultdict(int)

    for source, timestamp, timestamp_text, message in iter_logs(pod_logs):
        wave = wave_for_timestamp(timestamp, windows)
        if wave is None:
            continue
        source_lines[source] += 1

        if source == "amf":
            match = AMF_REGISTRATION_RE.search(message)
            if match:
                first_seen(
                    amf_registrations,
                    (wave, match.group(1)),
                    timestamp,
                    timestamp_text,
                )
        elif source == "smf":
            match = SMF_SESSION_RE.search(message)
            if match:
                key = (wave, match.group("imsi"))
                first_seen(smf_sessions, key, timestamp, timestamp_text)
                smf_session_ips[key] = match.group("ue_ip")
        elif source == "smf_sniffer":
            match = SMF_SNIFFER_RE.search(message)
            if match:
                key = (wave, match.group("imsi"))
                first_seen(smf_sniffer, key, timestamp, timestamp_text)
                smf_teids[key].add(match.group("teid").lower().zfill(8))
        elif source == "amf_sniffer":
            match = AMF_SNIFFER_RE.search(message)
            if match:
                teids = {
                    match.group("ul").lower().zfill(8),
                    match.group("dl").lower().zfill(8),
                }
                amf_sniffer_events[wave].append((timestamp, timestamp_text, teids))

    amf_sniffer: dict[tuple[int, str], tuple[float, str]] = {}
    for key, teids in smf_teids.items():
        wave, _ = key
        for timestamp, timestamp_text, observed_teids in amf_sniffer_events[wave]:
            if teids & observed_teids:
                first_seen(amf_sniffer, key, timestamp, timestamp_text)

    mapper_observed, mapper_first, stale_mapper = read_mapper_samples(mapper_csv, windows)
    sniffer_logs_present = bool(
        source_lines.get("amf_sniffer") and source_lines.get("smf_sniffer")
    )

    detail_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    diagnoses: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for window in windows:
        wave = int(window["wave"])
        wave_rows: list[dict[str, object]] = []
        for ordinal in range(wave):
            imsi = f"{args.imsi_prefix}{ordinal:0{args.ordinal_width}d}"
            key = (wave, imsi)
            row: dict[str, object] = {
                "wave": wave,
                "ordinal": ordinal,
                "imsi": imsi,
                "amf_registration_complete": key in amf_registrations,
                "smf_session_created": key in smf_sessions,
                "smf_sniffer_observed": key in smf_sniffer,
                "amf_sniffer_observed": key in amf_sniffer,
                "mapper_observed": key in mapper_observed,
                "mapper_stale_at_release": imsi in stale_mapper.get(wave, set()),
                "ue_ip": smf_session_ips.get(key, ""),
                "sniffer_teids": " ".join(sorted(smf_teids.get(key, set()))),
                "amf_registration_time": amf_registrations.get(key, ("", ""))[1],
                "smf_session_time": smf_sessions.get(key, ("", ""))[1],
                "smf_sniffer_time": smf_sniffer.get(key, ("", ""))[1],
                "amf_sniffer_time": amf_sniffer.get(key, ("", ""))[1],
                "mapper_time": mapper_first.get(key, ""),
            }
            row["diagnosis"] = diagnosis_for(row, sniffer_logs_present)
            diagnoses[wave][str(row["diagnosis"])] += 1
            detail_rows.append(row)
            wave_rows.append(row)

        summary_rows.append(
            {
                "wave": wave,
                "expected": wave,
                "amf_registration_complete": sum(
                    bool(row["amf_registration_complete"]) for row in wave_rows
                ),
                "smf_session_created": sum(
                    bool(row["smf_session_created"]) for row in wave_rows
                ),
                "smf_sniffer_observed": sum(
                    bool(row["smf_sniffer_observed"]) for row in wave_rows
                ),
                "amf_sniffer_observed": sum(
                    bool(row["amf_sniffer_observed"]) for row in wave_rows
                ),
                "mapper_observed": sum(bool(row["mapper_observed"]) for row in wave_rows),
            }
        )

    detail_fields = [
        "wave",
        "ordinal",
        "imsi",
        "amf_registration_complete",
        "smf_session_created",
        "smf_sniffer_observed",
        "amf_sniffer_observed",
        "mapper_observed",
        "mapper_stale_at_release",
        "diagnosis",
        "ue_ip",
        "sniffer_teids",
        "amf_registration_time",
        "smf_session_time",
        "smf_sniffer_time",
        "amf_sniffer_time",
        "mapper_time",
    ]
    summary_fields = [
        "wave",
        "expected",
        "amf_registration_complete",
        "smf_session_created",
        "smf_sniffer_observed",
        "amf_sniffer_observed",
        "mapper_observed",
    ]
    write_csv(results_dir / "churn_stage_correlation.csv", detail_rows, detail_fields)
    write_csv(results_dir / "churn_stage_summary.csv", summary_rows, summary_fields)

    report = {
        "sniffer_logs_present": sniffer_logs_present,
        "source_lines_in_wave_windows": dict(sorted(source_lines.items())),
        "waves": summary_rows,
        "diagnoses": {
            str(wave): dict(sorted(counts.items()))
            for wave, counts in sorted(diagnoses.items())
        },
    }
    (results_dir / "churn_diagnosis.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    markdown = [
        "# UERANSIM Churn Stage Diagnosis",
        "",
        f"Sniffer logs present: **{'yes' if sniffer_logs_present else 'no'}**",
        "",
        "| Wave | Expected | AMF registration | SMF session | SMF sniffer | AMF sniffer | Mapper |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        markdown.append(
            "| {wave} | {expected} | {amf_registration_complete} | "
            "{smf_session_created} | {smf_sniffer_observed} | "
            "{amf_sniffer_observed} | {mapper_observed} |".format(**row)
        )
    markdown.extend(["", "## First Missing Stage", ""])
    for wave in sorted(diagnoses):
        values = ", ".join(
            f"`{name}`={count}" for name, count in sorted(diagnoses[wave].items())
        )
        markdown.append(f"- Wave {wave}: {values}")
    markdown.extend(
        [
            "",
            "Interpretation:",
            "- `ue_or_amf_registration`: Open5GS AMF never logged registration completion.",
            "- `core_pdu_session`: registration completed, but no SMF UE session was logged.",
            "- `smf_sniffer_capture_or_parser`: SMF created the session, but its sniffer emitted no IMSI/TEID evidence.",
            "- `amf_sniffer_capture_or_parser`: SMF sniffer saw IMSI/TEID, but no matching NGAP TEID pair was emitted.",
            "- `redis_or_mapper_pairing`: both sniffers emitted compatible evidence, but the mapper inventory never exposed the IMSI.",
        ]
    )
    (results_dir / "churn_diagnosis.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    print(f"wrote churn correlation for {len(detail_rows)} expected UE-wave rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

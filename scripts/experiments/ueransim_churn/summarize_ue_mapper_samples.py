#!/usr/bin/env python3
"""Extract sampled UE mapper inventory snapshots into CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path


SAMPLE_RE = re.compile(r"/samples/(?P<stamp>\d{8}T\d{6}Z)/inventory_ues_limit_\d+\.txt$")


def parse_sample_timestamp(stamp: str) -> tuple[str, int]:
    dt = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z"), int(dt.timestamp())


def json_part(raw: str) -> dict:
    marker = "\nHTTP_STATUS="
    if marker in raw:
        raw = raw.split(marker, 1)[0]
    raw = raw.strip()
    if not raw:
        return {}
    return json.loads(raw)


def iter_inventory_samples(path: Path):
    if not path.exists():
        return
    with tarfile.open(path, "r:gz") as tf:
        for member in tf.getmembers():
            match = SAMPLE_RE.search(member.name)
            if not match or not member.isfile():
                continue
            fp = tf.extractfile(member)
            if fp is None:
                continue
            raw = fp.read().decode("utf-8", errors="replace")
            try:
                payload = json_part(raw)
            except json.JSONDecodeError:
                continue
            yield match.group("stamp"), payload


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots-tgz", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    snapshots = Path(args.snapshots_tgz)
    out_dir = Path(args.out_dir)

    inventory_rows: list[dict[str, object]] = []
    count_rows: list[dict[str, object]] = []

    for stamp, payload in iter_inventory_samples(snapshots) or []:
        sample_utc, sample_epoch = parse_sample_timestamp(stamp)
        ues = payload.get("ues") or []
        count = payload.get("count", len(ues))
        count_rows.append(
            {
                "sample_utc": sample_utc,
                "sample_epoch": sample_epoch,
                "connected_ues": count,
            }
        )
        for ue in ues:
            inventory_rows.append(
                {
                    "sample_utc": sample_utc,
                    "sample_epoch": sample_epoch,
                    "connected_ues": count,
                    "imsi": ue.get("imsi"),
                    "ran_ue_id": ue.get("ran_ue_id"),
                    "ue_ip": ue.get("ue_ip"),
                    "sst": ue.get("sst"),
                    "sd": ue.get("sd"),
                    "slice_id": ue.get("slice_id"),
                    "ul_teid": ue.get("ul_teid"),
                    "dl_teid": ue.get("dl_teid"),
                    "teid_args": ue.get("teid_args"),
                    "source": ue.get("source"),
                }
            )

    inventory_fields = [
        "sample_utc",
        "sample_epoch",
        "connected_ues",
        "imsi",
        "ran_ue_id",
        "ue_ip",
        "sst",
        "sd",
        "slice_id",
        "ul_teid",
        "dl_teid",
        "teid_args",
        "source",
    ]
    count_fields = ["sample_utc", "sample_epoch", "connected_ues"]

    inventory_rows.sort(key=lambda row: (int(row["sample_epoch"]), str(row.get("imsi") or "")))
    count_rows.sort(key=lambda row: int(row["sample_epoch"]))

    write_csv(out_dir / "ue_mapper_inventory_samples.csv", inventory_rows, inventory_fields)
    write_csv(out_dir / "ue_mapper_inventory_counts.csv", count_rows, count_fields)
    print(f"wrote {len(inventory_rows)} UE mapper inventory rows from {len(count_rows)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

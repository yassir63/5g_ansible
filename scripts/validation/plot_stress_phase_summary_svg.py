#!/usr/bin/env python3
"""Render a compact per-phase summary SVG for one UE in a stress scenario."""

from __future__ import annotations

import argparse
import csv
import html
import math
from pathlib import Path


LAT_SERIES = [
    ("gNB direct core median (ms)", "#1f77b4"),
    ("UPF direct core median (ms)", "#d62728"),
    ("same-packet gNB core median (ms)", "#2ca02c"),
    ("same-packet UPF core median (ms)", "#9467bd"),
]

RATE_SERIES = [
    ("gNB direct event rate (Hz)", "#1f77b4"),
    ("UPF direct event rate (Hz)", "#d62728"),
    ("same-packet pair rate (Hz)", "#2ca02c"),
]

PHASES = ["clean", "stress", "recovery"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-csv", required=True, type=Path)
    parser.add_argument("--imsi", required=True)
    parser.add_argument("--ue-ip", required=True)
    parser.add_argument("--slice", required=True, dest="slice_name")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--title", default="")
    return parser.parse_args()


def load_summary(path: Path, *, imsi: str, ue_ip: str, slice_name: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("imsi") != imsi or row.get("ue_ip") != ue_ip or row.get("slice") != slice_name:
                continue
            metric = row["metric"]
            out[metric] = {}
            for phase in PHASES:
                value = row.get(f"{phase}_median", "")
                out[metric][phase] = float(value) if value else float("nan")
    return out


def fmt_tick(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    if value >= 1:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".")


def main() -> None:
    args = parse_args()
    data = load_summary(args.summary_csv, imsi=args.imsi, ue_ip=args.ue_ip, slice_name=args.slice_name)

    width = 1100
    height = 700
    left = 88
    right = 30
    top = 58
    bottom = 62
    gap = 48
    lat_h = 330
    rate_h = 170
    plot_w = width - left - right
    lat_y = top
    rate_y = top + lat_h + gap

    lat_vals = [
        data[metric][phase]
        for metric, _color in LAT_SERIES
        for phase in PHASES
        if metric in data and not math.isnan(data[metric][phase])
    ]
    rate_vals = [
        data[metric][phase]
        for metric, _color in RATE_SERIES
        for phase in PHASES
        if metric in data and not math.isnan(data[metric][phase])
    ]
    lat_min = min(lat_vals) if lat_vals else 0.05
    lat_max = max(lat_vals) if lat_vals else 1000.0
    lat_min = max(0.05, lat_min * 0.8)
    lat_max = max(lat_max * 1.15, lat_min * 10)
    rate_max = max(rate_vals) if rate_vals else 1.0
    rate_max = max(1.0, rate_max * 1.15)

    def y_lat(v: float) -> float:
        frac = (math.log10(v) - math.log10(lat_min)) / (math.log10(lat_max) - math.log10(lat_min))
        return lat_y + lat_h - frac * lat_h

    def y_rate(v: float) -> float:
        frac = v / rate_max
        return rate_y + rate_h - frac * rate_h

    title = args.title or f"UPF stress phase summary for UE {args.imsi} ({args.ue_ip}, slice {args.slice_name})"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#1f2933}",
        ".axis{stroke:#334e68;stroke-width:1}",
        ".grid{stroke:#d9e2ec;stroke-width:1}",
        ".bar{shape-rendering:geometricPrecision}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width/2:.1f}" y="28" text-anchor="middle" font-size="20" font-weight="700">{html.escape(title)}</text>',
    ]

    # Background groups.
    group_w = plot_w / len(PHASES)
    group_fill = ["#eef6ff", "#fff1f0", "#f2fbf2"]
    for i, phase in enumerate(PHASES):
        x0 = left + i * group_w
        lines.append(f'<rect x="{x0:.2f}" y="{lat_y:.2f}" width="{group_w:.2f}" height="{lat_h:.2f}" fill="{group_fill[i]}"/>')
        lines.append(f'<rect x="{x0:.2f}" y="{rate_y:.2f}" width="{group_w:.2f}" height="{rate_h:.2f}" fill="{group_fill[i]}"/>')
        lines.append(f'<text x="{x0 + group_w/2:.2f}" y="{lat_y - 12:.2f}" text-anchor="middle" font-size="13" font-weight="700">{phase.capitalize()}</text>')

    # Latency grid/axis.
    lat_ticks = [0.1, 1, 10, 100, 1000]
    lat_ticks = [tick for tick in lat_ticks if lat_min <= tick <= lat_max]
    for tick in lat_ticks:
        y = y_lat(tick)
        lines.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}"/>')
        lines.append(f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="11">{fmt_tick(tick)}</text>')
    lines.append(f'<line class="axis" x1="{left}" y1="{lat_y}" x2="{left}" y2="{lat_y + lat_h}"/>')
    lines.append(f'<line class="axis" x1="{left}" y1="{lat_y + lat_h}" x2="{left + plot_w}" y2="{lat_y + lat_h}"/>')
    lines.append(f'<text x="24" y="{lat_y + lat_h/2:.2f}" transform="rotate(-90 24 {lat_y + lat_h/2:.2f})" text-anchor="middle" font-size="13">Latency median (ms, log scale)</text>')

    # Rate grid/axis.
    for i in range(6):
        frac = i / 5.0
        val = rate_max * frac
        y = y_rate(val)
        lines.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}"/>')
        lines.append(f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="11">{fmt_tick(val)}</text>')
    lines.append(f'<line class="axis" x1="{left}" y1="{rate_y}" x2="{left}" y2="{rate_y + rate_h}"/>')
    lines.append(f'<line class="axis" x1="{left}" y1="{rate_y + rate_h}" x2="{left + plot_w}" y2="{rate_y + rate_h}"/>')
    lines.append(f'<text x="24" y="{rate_y + rate_h/2:.2f}" transform="rotate(-90 24 {rate_y + rate_h/2:.2f})" text-anchor="middle" font-size="13">Event / pair rate (Hz)</text>')

    # Latency bars.
    bar_gap = 10
    lat_bar_w = (group_w * 0.72 - bar_gap * (len(LAT_SERIES) - 1)) / len(LAT_SERIES)
    for i, phase in enumerate(PHASES):
        cx = left + i * group_w + group_w / 2
        start_x = cx - ((lat_bar_w * len(LAT_SERIES)) + bar_gap * (len(LAT_SERIES) - 1)) / 2
        for j, (metric, color) in enumerate(LAT_SERIES):
            value = data.get(metric, {}).get(phase, float("nan"))
            if math.isnan(value) or value <= 0:
                continue
            x = start_x + j * (lat_bar_w + bar_gap)
            y = y_lat(value)
            h = lat_y + lat_h - y
            lines.append(f'<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{lat_bar_w:.2f}" height="{h:.2f}" fill="{color}"/>')
            lines.append(f'<text x="{x + lat_bar_w/2:.2f}" y="{y - 6:.2f}" text-anchor="middle" font-size="10">{fmt_tick(value)}</text>')

    # Rate bars.
    rate_bar_w = (group_w * 0.56 - bar_gap * (len(RATE_SERIES) - 1)) / len(RATE_SERIES)
    for i, phase in enumerate(PHASES):
        cx = left + i * group_w + group_w / 2
        start_x = cx - ((rate_bar_w * len(RATE_SERIES)) + bar_gap * (len(RATE_SERIES) - 1)) / 2
        for j, (metric, color) in enumerate(RATE_SERIES):
            value = data.get(metric, {}).get(phase, float("nan"))
            if math.isnan(value) or value < 0:
                continue
            x = start_x + j * (rate_bar_w + bar_gap)
            y = y_rate(value)
            h = rate_y + rate_h - y
            lines.append(f'<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{rate_bar_w:.2f}" height="{h:.2f}" fill="{color}"/>')
            lines.append(f'<text x="{x + rate_bar_w/2:.2f}" y="{y - 6:.2f}" text-anchor="middle" font-size="10">{fmt_tick(value)}</text>')

    # Legends.
    legend_x = left
    legend_y = height - 36
    legend_items = LAT_SERIES + RATE_SERIES
    for idx, (metric, color) in enumerate(legend_items):
        x = legend_x + (idx % 3) * 320
        y = legend_y + (idx // 3) * 18
        lines.append(f'<rect x="{x:.2f}" y="{y - 10:.2f}" width="12" height="12" fill="{color}"/>')
        lines.append(f'<text x="{x + 18:.2f}" y="{y:.2f}" font-size="12">{html.escape(metric)}</text>')

    lines.append("</svg>")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

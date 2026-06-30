#!/usr/bin/env python3
"""Plot one-UE stress-phase latency and event-rate signals as a standalone SVG."""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import math
from pathlib import Path


PHASES = [
    ("clean_before_stress", "Clean", "#eef6ff"),
    ("stress_on", "Stress", "#fff1f0"),
    ("recovery_after_stress", "Recovery", "#f2fbf2"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--scenario-prefix", required=True)
    parser.add_argument("--imsi", required=True)
    parser.add_argument("--ue-ip", required=True)
    parser.add_argument("--slice", required=True, dest="slice_name")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--title", default="")
    return parser.parse_args()


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def read_rows(path: Path) -> list[dict[str, str]]:
    with open_text(path) as handle:
        return list(csv.DictReader(handle))


def find_csv(results_dir: Path, scenario_prefix: str, phase_suffix: str) -> Path:
    root = results_dir / "by_window" / "stress_phase"
    folder = root / f"stress_phase__{scenario_prefix}{phase_suffix}"
    gz = folder / "prometheus_timeseries.csv.gz"
    plain = folder / "prometheus_timeseries.csv"
    if gz.exists():
        return gz
    if plain.exists():
        return plain
    raise FileNotFoundError(f"Missing CSV for {phase_suffix}: {folder}")


def numeric(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except ValueError:
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def collect_series(rows: list[dict[str, str]], *, imsi: str, ue_ip: str, slice_name: str, query_name: str, extra: dict[str, str]) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    for row in rows:
        if row.get("query_name") != query_name:
            continue
        if row.get("imsi") != imsi or row.get("ue_ip") != ue_ip or row.get("slice") != slice_name:
            continue
        if any(row.get(k) != v for k, v in extra.items()):
            continue
        ts = numeric(row.get("timestamp"))
        val = numeric(row.get("value"))
        if ts is None or val is None:
            continue
        points.append((int(ts), val))
    points.sort()
    return points


def path_from_points(
    points: list[tuple[float, float]],
    xmap,
    ymap,
) -> str:
    if not points:
        return ""
    parts = []
    for idx, (x, y) in enumerate(points):
        cmd = "M" if idx == 0 else "L"
        parts.append(f"{cmd}{xmap(x):.2f},{ymap(y):.2f}")
    return " ".join(parts)


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

    metric_specs = [
        ("gNB direct core median", "direct_median_1s_ms", {"probe_role": "gnb", "mode": "core"}, "#1f77b4", "lat"),
        ("UPF direct core median", "direct_median_1s_ms", {"probe_role": "upf", "mode": "core"}, "#d62728", "lat"),
        ("same-packet gNB core", "same_packet_gnb_rtt_latest_ms", {"mode": "core", "direction": "uplink"}, "#2ca02c", "lat"),
        ("same-packet UPF core", "same_packet_upf_rtt_latest_ms", {"mode": "core", "direction": "uplink"}, "#9467bd", "lat"),
        ("gNB direct event rate", "direct_event_rate_hz_5s", {"probe_role": "gnb", "mode": "core"}, "#1f77b4", "rate"),
        ("UPF direct event rate", "direct_event_rate_hz_5s", {"probe_role": "upf", "mode": "core"}, "#d62728", "rate"),
        ("same-packet pair rate", "same_packet_pair_rate_hz_5s", {"mode": "core", "direction": "uplink"}, "#2ca02c", "rate"),
    ]

    all_series: dict[str, list[tuple[float, float]]] = {}
    phase_spans: list[tuple[str, str, float, float, str]] = []
    offset = 0.0

    for phase_suffix, phase_label, phase_color in PHASES:
        rows = read_rows(find_csv(args.results_dir, args.scenario_prefix, phase_suffix))
        phase_start = None
        phase_end = None
        for label, query_name, extra, _color, _group in metric_specs:
            pts = collect_series(
                rows,
                imsi=args.imsi,
                ue_ip=args.ue_ip,
                slice_name=args.slice_name,
                query_name=query_name,
                extra=extra,
            )
            shifted = []
            for ts, val in pts:
                if phase_start is None or ts < phase_start:
                    phase_start = ts
                if phase_end is None or ts > phase_end:
                    phase_end = ts
                shifted.append((0.0, val))  # placeholder, updated below
            all_series.setdefault(label, []).append((phase_start, shifted))  # type: ignore[arg-type]

        if phase_start is None or phase_end is None:
            continue

        phase_len = float(max(1, phase_end - phase_start))
        phase_spans.append((phase_suffix, phase_label, offset, offset + phase_len, phase_color))

        for label, query_name, extra, _color, _group in metric_specs:
            pts = collect_series(
                rows,
                imsi=args.imsi,
                ue_ip=args.ue_ip,
                slice_name=args.slice_name,
                query_name=query_name,
                extra=extra,
            )
            shifted = [(offset + (ts - phase_start), val) for ts, val in pts]
            all_series.setdefault(label, [])
            all_series[label] = [item for item in all_series[label] if not (isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], list))]
            all_series[label].extend(shifted)

        offset += phase_len

    width = 1200
    height = 760
    margin_left = 90
    margin_right = 24
    margin_top = 60
    margin_bottom = 62
    panel_gap = 42
    panel1_h = 360
    panel2_h = 190
    plot_w = width - margin_left - margin_right
    panel1_y = margin_top
    panel2_y = panel1_y + panel1_h + panel_gap

    total_x = max((end for _s, _l, _start, end, _c in phase_spans), default=1.0)
    if total_x <= 0:
        total_x = 1.0

    latency_vals = []
    rate_vals = []
    for label, _query, _extra, _color, group in metric_specs:
        vals = [y for _x, y in all_series.get(label, []) if y > 0]
        if group == "lat":
            latency_vals.extend(vals)
        else:
            rate_vals.extend(vals)
    lat_min = min(latency_vals) if latency_vals else 0.05
    lat_max = max(latency_vals) if latency_vals else 1000.0
    lat_min = max(0.05, lat_min * 0.8)
    lat_max = max(lat_min * 10.0, lat_max * 1.15)
    rate_max = max(rate_vals) if rate_vals else 1.0
    rate_max = max(1.0, rate_max * 1.1)

    def xmap(x: float) -> float:
        return margin_left + (x / total_x) * plot_w

    def ymap_lat(y: float) -> float:
        y = max(lat_min, min(lat_max, y))
        frac = (math.log10(y) - math.log10(lat_min)) / (math.log10(lat_max) - math.log10(lat_min))
        return panel1_y + panel1_h - frac * panel1_h

    def ymap_rate(y: float) -> float:
        frac = max(0.0, min(1.0, y / rate_max))
        return panel2_y + panel2_h - frac * panel2_h

    title = args.title or f"UPF stress phases for UE {args.imsi} ({args.ue_ip}, slice {args.slice_name})"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#1f2933}",
        ".axis{stroke:#334e68;stroke-width:1}",
        ".grid{stroke:#d9e2ec;stroke-width:1}",
        ".legend text{font-size:12px}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width/2:.1f}" y="28" text-anchor="middle" font-size="20" font-weight="700">{html.escape(title)}</text>',
    ]

    # Phase backgrounds and labels.
    for _suffix, label, x0, x1, color in phase_spans:
        xs = xmap(x0)
        xe = xmap(x1)
        lines.append(f'<rect x="{xs:.2f}" y="{panel1_y:.2f}" width="{(xe-xs):.2f}" height="{panel1_h:.2f}" fill="{color}"/>')
        lines.append(f'<rect x="{xs:.2f}" y="{panel2_y:.2f}" width="{(xe-xs):.2f}" height="{panel2_h:.2f}" fill="{color}"/>')
        lines.append(f'<text x="{(xs+xe)/2:.2f}" y="{panel1_y-12:.2f}" text-anchor="middle" font-size="13" font-weight="700">{html.escape(label)}</text>')

    # Grid + axes latency
    lat_ticks = [0.1, 1, 10, 100, 1000]
    lat_ticks = [v for v in lat_ticks if lat_min <= v <= lat_max]
    for tick in lat_ticks:
        y = ymap_lat(tick)
        lines.append(f'<line class="grid" x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_w}" y2="{y:.2f}"/>')
        lines.append(f'<text x="{margin_left-8}" y="{y+4:.2f}" text-anchor="end" font-size="11">{fmt_tick(tick)}</text>')
    lines.append(f'<line class="axis" x1="{margin_left}" y1="{panel1_y}" x2="{margin_left}" y2="{panel1_y + panel1_h}"/>')
    lines.append(f'<line class="axis" x1="{margin_left}" y1="{panel1_y + panel1_h}" x2="{margin_left + plot_w}" y2="{panel1_y + panel1_h}"/>')
    lines.append(f'<text x="24" y="{panel1_y + panel1_h/2:.2f}" transform="rotate(-90 24 {panel1_y + panel1_h/2:.2f})" text-anchor="middle" font-size="13">Latency (ms, log scale)</text>')

    # Grid + axes rate
    for i in range(6):
        frac = i / 5.0
        val = rate_max * frac
        y = ymap_rate(val)
        lines.append(f'<line class="grid" x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_w}" y2="{y:.2f}"/>')
        lines.append(f'<text x="{margin_left-8}" y="{y+4:.2f}" text-anchor="end" font-size="11">{fmt_tick(val)}</text>')
    lines.append(f'<line class="axis" x1="{margin_left}" y1="{panel2_y}" x2="{margin_left}" y2="{panel2_y + panel2_h}"/>')
    lines.append(f'<line class="axis" x1="{margin_left}" y1="{panel2_y + panel2_h}" x2="{margin_left + plot_w}" y2="{panel2_y + panel2_h}"/>')
    lines.append(f'<text x="24" y="{panel2_y + panel2_h/2:.2f}" transform="rotate(-90 24 {panel2_y + panel2_h/2:.2f})" text-anchor="middle" font-size="13">Event rate (Hz)</text>')

    # X ticks / phase boundaries
    for _suffix, _label, x0, x1, _color in phase_spans:
        xs = xmap(x0)
        xe = xmap(x1)
        lines.append(f'<line class="axis" x1="{xs:.2f}" y1="{panel2_y + panel2_h}" x2="{xs:.2f}" y2="{panel2_y + panel2_h + 6}"/>')
        lines.append(f'<line class="axis" x1="{xe:.2f}" y1="{panel2_y + panel2_h}" x2="{xe:.2f}" y2="{panel2_y + panel2_h + 6}"/>')
    lines.append(f'<text x="{margin_left + plot_w/2:.2f}" y="{height-16:.2f}" text-anchor="middle" font-size="13">Scenario time (concatenated clean / stress / recovery windows)</text>')

    # Series
    for label, _query, _extra, color, group in metric_specs:
        points = all_series.get(label, [])
        if not points:
            continue
        path = path_from_points(points, xmap, ymap_lat if group == "lat" else ymap_rate)
        if not path:
            continue
        lines.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>')

    # Legends
    latency_legend = [
        ("gNB direct core median", "#1f77b4"),
        ("UPF direct core median", "#d62728"),
        ("same-packet gNB core", "#2ca02c"),
        ("same-packet UPF core", "#9467bd"),
    ]
    rate_legend = [
        ("gNB direct event rate", "#1f77b4"),
        ("UPF direct event rate", "#d62728"),
        ("same-packet pair rate", "#2ca02c"),
    ]
    lx = margin_left + 6
    ly = panel1_y + 18
    for idx, (label, color) in enumerate(latency_legend):
        yy = ly + idx * 18
        lines.append(f'<line x1="{lx}" y1="{yy}" x2="{lx+16}" y2="{yy}" stroke="{color}" stroke-width="2.6"/>')
        lines.append(f'<text x="{lx+22}" y="{yy+4}" font-size="12">{html.escape(label)}</text>')
    rx = margin_left + 6
    ry = panel2_y + 18
    for idx, (label, color) in enumerate(rate_legend):
        yy = ry + idx * 18
        lines.append(f'<line x1="{rx}" y1="{yy}" x2="{rx+16}" y2="{yy}" stroke="{color}" stroke-width="2.6"/>')
        lines.append(f'<text x="{rx+22}" y="{yy+4}" font-size="12">{html.escape(label)}</text>')

    lines.append("</svg>")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

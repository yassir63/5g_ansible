#!/usr/bin/env python3
"""Create paper-ready validation evidence figures.

The validation folder should contain proof figures, not scenario exploration.
This script builds the figures needed to support correctness and overhead
claims in the paper:

1. container CPU usage,
2. container memory usage,
3. passive TC eBPF baseline vs the latency probe,
4. controlled netem delay observed by the probe,
5. ICMP ping samples vs probe samples over time,
6. TCP probe mean latency vs pcap-reconstructed TCP mean RTT over time,
   with raw sanity traces,
7. TCP probe-vs-pcap mean-only RTT over time,
8. TCP probe latest samples vs nearest-in-time pcap-reconstructed packet RTT
   samples over time,
9. distribution-oriented CDF and box-plot figures for paper use,
10. median-focused TCP correctness CDFs and TCP/ICMP median deltas,
11. paired TCP correctness scatter/similarity summaries,
12. packet-rate budgets that translate BPF runtime into saturation risk.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import html
import json
import math
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable


COLORS = [
    "#2f6f9f",
    "#d9822b",
    "#4f7f38",
    "#8b4aa8",
    "#aa3d3d",
    "#607d8b",
    "#c44569",
    "#2d6a4f",
]

MONITOR_CONTAINER_RE = re.compile(
    r"(ebpf-latency-probe|latency|pairer|exporter|kopf|prometheus|grafana|monitoring-manager|thanos)",
    re.IGNORECASE,
)

PARALLEL_TCP_ICMP_SCENARIOS = {
    "v05_tcp_icmp_parallel_median",
    "v06_tcp_icmp_parallel_mtu_ping",
}


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8", errors="replace")
    return path.open("r", newline="", encoding="utf-8", errors="replace")


def iter_csv(path: Path | None):
    if path is None or not path.exists():
        return
    with open_text(path) as fp:
        yield from csv.DictReader(fp)


def read_csv(path: Path | None) -> list[dict[str, str]]:
    return list(iter_csv(path))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def first_existing(base: Path, names: Iterable[str]) -> Path | None:
    for name in names:
        path = base / name
        if path.exists():
            return path
    return None


def number(value: object, default: float | None = None) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return parsed


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * q))
    return ordered[max(0, min(index, len(ordered) - 1))]


def median(values: list[float]) -> float:
    return percentile(values, 0.50)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "mean": float("nan"), "p50": float("nan"), "p95": float("nan"), "p99": float("nan"), "max": float("nan")}
    return {
        "count": float(len(values)),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
    }


def fmt(value: object, digits: int = 3) -> str:
    parsed = number(value)
    if parsed is None:
        return ""
    return f"{parsed:.{digits}f}"


def pearson_corr(xs: list[float], ys: list[float]) -> float:
    pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 2:
        return float("nan")
    clean_xs = [x for x, _y in pairs]
    clean_ys = [y for _x, y in pairs]
    mx = mean(clean_xs)
    my = mean(clean_ys)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den_x = math.sqrt(sum((x - mx) ** 2 for x in clean_xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in clean_ys))
    if den_x <= 0 or den_y <= 0:
        return float("nan")
    return num / (den_x * den_y)


def average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    idx = 0
    while idx < len(ordered):
        end = idx + 1
        while end < len(ordered) and ordered[end][1] == ordered[idx][1]:
            end += 1
        rank = (idx + 1 + end) / 2.0
        for original_idx, _value in ordered[idx:end]:
            ranks[original_idx] = rank
        idx = end
    return ranks


def spearman_corr(xs: list[float], ys: list[float]) -> float:
    pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 2:
        return float("nan")
    clean_xs = [x for x, _y in pairs]
    clean_ys = [y for _x, y in pairs]
    return pearson_corr(average_ranks(clean_xs), average_ranks(clean_ys))


def ks_distance(xs: list[float], ys: list[float]) -> float:
    xs = sorted(x for x in xs if math.isfinite(x))
    ys = sorted(y for y in ys if math.isfinite(y))
    if not xs or not ys:
        return float("nan")
    values = sorted(set(xs + ys))
    i = 0
    j = 0
    best = 0.0
    for value in values:
        while i < len(xs) and xs[i] <= value:
            i += 1
        while j < len(ys) and ys[j] <= value:
            j += 1
        best = max(best, abs(i / len(xs) - j / len(ys)))
    return best


def empirical_quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = max(0.0, min(1.0, q)) * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def wasserstein_1d_ms(xs: list[float], ys: list[float], samples: int = 1000) -> float:
    xs = sorted(x for x in xs if math.isfinite(x))
    ys = sorted(y for y in ys if math.isfinite(y))
    if not xs or not ys:
        return float("nan")
    count = max(1, min(samples, max(len(xs), len(ys))))
    total = 0.0
    for idx in range(count):
        q = (idx + 0.5) / count
        total += abs(empirical_quantile(xs, q) - empirical_quantile(ys, q))
    return total / count


def load_prometheus(results_dir: Path) -> list[dict[str, str]]:
    return read_csv(first_existing(results_dir, ["prometheus_timeseries.csv", "prometheus_timeseries.csv.gz"]))


def load_windows(results_dir: Path) -> list[dict[str, str]]:
    return read_csv(results_dir / "timeline_summary.csv")


def direction_windows(windows: list[dict[str, str]], scenario: str | None = None) -> list[dict[str, str]]:
    rows = [row for row in windows if row.get("level") == "direction"]
    if scenario:
        rows = [row for row in rows if row.get("scenario") == scenario]
    return rows


def step_label(step: str, direction: str = "") -> str:
    low = step.lower()
    if "no_netem" in low:
        base = "Reference"
    elif "upf_netem" in low:
        base = "UPF netem"
    elif "ran_netem" in low:
        base = "RAN netem"
    elif "tc_pass" in low:
        base = "TC pass"
    elif "baseline" in low:
        base = "Baseline"
    else:
        base = step.replace("qhat01_p1_", "").replace("qhat01_qhat02_p1_", "").replace("_", " ")
    return f"{base} {direction.upper()}".strip()


def direction_mode(direction: str) -> str:
    if direction == "dl":
        return "ran"
    if direction == "ul":
        return "core"
    if direction in {"ue_to_upf", "upf_to_ue"}:
        return "ran"
    return ""


def ping_size_labels(win: dict[str, str]) -> tuple[str, str, str]:
    raw = str(win.get("ping_size") or "").strip()
    parsed = number(raw)
    if parsed is None:
        return raw, "", "ping size unknown"
    payload = str(int(parsed))
    ipv4_packet = str(int(parsed) + 28)
    return payload, ipv4_packet, f"ping -s {payload}, IPv4 ~= {ipv4_packet} B"


def probe_role_to_pcap_role(probe_role: str) -> str:
    if probe_role == "gnb":
        return "ran"
    return probe_role


def pcap_role_to_probe_role(role: str) -> str:
    if role == "ran":
        return "gnb"
    return role


def parse_pcap_name(path: str) -> tuple[str, str, str, str]:
    name = Path(path).name
    name = name.replace("_n3.pcap.gz", "").replace("_n3.pcap", "")
    name = name.replace("_tcpdump.log", "")
    parts = name.split("__")
    scenario = parts[0] if len(parts) > 0 else ""
    step = parts[1] if len(parts) > 1 else ""
    direction = parts[2] if len(parts) > 2 else ""
    role = parts[3].split("_", 1)[0] if len(parts) > 3 else ""
    return scenario, step, direction, role


def metric_json(row: dict[str, str]) -> dict[str, object]:
    raw = row.get("metric_json") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def metric_kind(row: dict[str, str]) -> str:
    return str(metric_json(row).get("kind") or row.get("proto") or row.get("protocol") or "").lower()


def row_kind(row: dict[str, str]) -> str:
    return str(row.get("kind") or metric_kind(row)).lower()


def is_kind(row: dict[str, str], expected: str) -> bool:
    kind = row_kind(row)
    if not kind:
        return True
    return expected.lower() in kind


def shorten_label(namespace: str, pod: str, container: str) -> str:
    pod_low = pod.lower()
    container_low = container.lower()
    if "oai-gnb" in pod_low and "ebpf-latency-probe" in container_low:
        return f"gNB {container}"
    if "upf1" in pod_low and "ebpf-latency-probe" in container_low:
        return f"UPF1 {container}"
    if "upf2" in pod_low and "ebpf-latency-probe" in container_low:
        return f"UPF2 {container}"
    if "kopf" in pod_low:
        return "Kopf controller"
    if "prometheus" in pod_low and container:
        return f"Prometheus {container}"
    if "grafana" in pod_low:
        return "Grafana"
    if container:
        return f"{pod}/{container}"[:48]
    return f"{namespace}/{pod}"[:48]


def primary_ue_ip(results_dir: Path, prom: list[dict[str, str]], windows: list[dict[str, str]]) -> str:
    for meta in sorted((results_dir / "upf_ping_logs").rglob("ping_*.meta")):
        details = {}
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                details[key.strip()] = value.strip()
        if details.get("ue") == "qhat01" and details.get("ue_ip"):
            return details["ue_ip"]

    controlled = direction_windows(windows, "v03_controlled_delay")
    counts: dict[str, int] = defaultdict(int)
    for row in prom:
        if row.get("query_name") != "direct_mean_ms_5s":
            continue
        ue_ip = row.get("ue_ip") or ""
        ts = number(row.get("timestamp"))
        value = number(row.get("value"))
        if not ue_ip or ts is None or value is None:
            continue
        if any(number(win.get("start_epoch"), -1) <= ts <= number(win.get("end_epoch"), -2) for win in controlled):
            counts[ue_ip] += 1
    return max(counts, key=counts.get) if counts else ""


def cleanup_old_figures(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("fig*.svg", "fig*.txt"):
        for path in out_dir.glob(pattern):
            path.unlink()
    stale_csvs = [
        "bpftool_overhead_comparison.csv",
        "iperf_summary.csv",
        "latency_probe_window_summary.csv",
        "pcap_tcp_rtt_summary.csv",
        "resource_overhead_summary.csv",
    ]
    for name in stale_csvs:
        path = out_dir / name
        if path.exists():
            path.unlink()


def svg_grouped_bars(
    path: Path,
    title: str,
    ylabel: str,
    categories: list[str],
    series: list[str],
    values: dict[tuple[str, str], float],
    *,
    log_scale: bool = False,
    height: int = 560,
    value_digits: int = 2,
) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    finite = [v for v in values.values() if math.isfinite(v)]
    if not categories or not series or not finite:
        path.with_suffix(".txt").write_text("No data available for this figure.\n", encoding="utf-8")
        return False

    width = max(920, 120 + 100 * len(categories))
    ml, mr, mt, mb = 76, 30, 56, 150
    cw, ch = width - ml - mr, height - mt - mb

    if log_scale:
        floor = max(min(v for v in finite if v > 0) / 2, 0.001)

        def scaled(v: float) -> float:
            return math.log10(max(v, floor))

        y_min = math.floor(min(scaled(v) for v in finite))
        y_max = math.ceil(max(scaled(v) for v in finite))
        if y_min == y_max:
            y_max += 1
        ticks = [10**i for i in range(int(y_min), int(y_max) + 1)]

        def y_pos(v: float) -> float:
            return mt + ch - ((scaled(v) - y_min) / (y_max - y_min)) * ch

    else:
        y_min = 0.0
        y_max = max(finite) * 1.20
        if y_max <= 0:
            y_max = 1.0
        ticks = [y_max * i / 5 for i in range(6)]

        def y_pos(v: float) -> float:
            return mt + ch - ((v - y_min) / (y_max - y_min)) * ch

    group_w = cw / len(categories)
    bar_w = max(5, min(24, (group_w * 0.78) / max(1, len(series))))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,Helvetica,sans-serif;font-size:12px;fill:#1f2933}.title{font-size:18px;font-weight:700}.axis{stroke:#1f2933;stroke-width:1}.grid{stroke:#d8dee4;stroke-width:1}.value{font-size:10px}</style>",
        f'<text class="title" x="{width/2:.1f}" y="30" text-anchor="middle">{html.escape(title)}</text>',
        f'<text x="22" y="{mt + ch/2:.1f}" transform="rotate(-90 22 {mt + ch/2:.1f})" text-anchor="middle">{html.escape(ylabel)}</text>',
        f'<line class="axis" x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ch}"/>',
        f'<line class="axis" x1="{ml}" y1="{mt+ch}" x2="{ml+cw}" y2="{mt+ch}"/>',
    ]
    for tick in ticks:
        y = y_pos(tick)
        label = f"{tick:g}" if log_scale else f"{tick:.1f}"
        parts.append(f'<line class="grid" x1="{ml}" y1="{y:.1f}" x2="{ml+cw}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end">{html.escape(label)}</text>')

    legend_x = ml
    for si, name in enumerate(series):
        x = legend_x + si * 135
        parts.append(f'<rect x="{x}" y="{height-26}" width="12" height="12" fill="{COLORS[si % len(COLORS)]}"/>')
        parts.append(f'<text x="{x+18}" y="{height-16}">{html.escape(name)}</text>')

    for ci, category in enumerate(categories):
        gx = ml + ci * group_w
        center = gx + group_w / 2
        parts.append(
            f'<text x="{center:.1f}" y="{mt+ch+18}" text-anchor="end" transform="rotate(-38 {center:.1f} {mt+ch+18})">{html.escape(category)}</text>'
        )
        start = center - (bar_w * len(series)) / 2
        for si, name in enumerate(series):
            value = values.get((category, name))
            if value is None or not math.isfinite(value):
                continue
            y = y_pos(value)
            x = start + si * bar_w
            h = mt + ch - y
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w-1:.1f}" height="{h:.1f}" fill="{COLORS[si % len(COLORS)]}"/>')
            parts.append(
                f'<text class="value" x="{x + bar_w/2:.1f}" y="{y-4:.1f}" text-anchor="middle">{value:.{value_digits}f}</text>'
            )

    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def svg_timeseries_panels(
    path: Path,
    title: str,
    ylabel: str,
    panels: list[dict[str, object]],
    *,
    width: int = 1080,
    panel_height: int = 230,
    xlabel: str = "seconds from window start",
    y_range: tuple[float, float] | None = None,
) -> bool:
    panels = [panel for panel in panels if panel.get("series")]
    if not panels:
        path.with_suffix(".txt").write_text("No time-series data available for this figure.\n", encoding="utf-8")
        return False

    ml, mr, mt, mb = 72, 34, 58, 50
    gap = 42
    height = mt + mb + len(panels) * panel_height + (len(panels) - 1) * gap
    cw = width - ml - mr
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,Helvetica,sans-serif;font-size:12px;fill:#1f2933}.title{font-size:18px;font-weight:700}.axis{stroke:#1f2933;stroke-width:1}.grid{stroke:#d8dee4;stroke-width:1}.band{fill:#f1f5f9}.legend{font-size:12px}</style>",
        f'<text class="title" x="{width/2:.1f}" y="30" text-anchor="middle">{html.escape(title)}</text>',
    ]

    for pi, panel in enumerate(panels):
        top = mt + pi * (panel_height + gap)
        series = panel.get("series", [])
        bands = panel.get("bands", [])
        xs: list[float] = []
        ys: list[float] = []
        for item in series:  # type: ignore[assignment]
            for x, y in item.get("points", []):
                xs.append(float(x))
                ys.append(float(y))
        for band in bands:  # type: ignore[assignment]
            xs.extend([float(band[0]), float(band[1])])
        if not xs or not ys:
            continue
        x_min, x_max = min(xs), max(xs)
        if x_min == x_max:
            x_max += 1
        if y_range is not None:
            y_min, y_max = y_range
        else:
            y_min = 0.0
            y_max = max(ys) * 1.18
            if y_max <= 0:
                y_max = 1.0
        if y_min == y_max:
            y_max = y_min + 1.0

        def xp(x: float) -> float:
            return ml + ((x - x_min) / (x_max - x_min)) * cw

        def yp(y: float) -> float:
            return top + panel_height - ((y - y_min) / (y_max - y_min)) * panel_height

        for band in bands:  # type: ignore[assignment]
            b0, b1, label = float(band[0]), float(band[1]), str(band[2])
            x0, x1 = xp(b0), xp(b1)
            parts.append(f'<rect class="band" x="{x0:.1f}" y="{top}" width="{max(1, x1-x0):.1f}" height="{panel_height}"/>')
            parts.append(f'<text x="{(x0+x1)/2:.1f}" y="{top+14}" text-anchor="middle" font-size="10">{html.escape(label)}</text>')

        parts.append(f'<text x="{ml}" y="{top-10}" font-weight="700">{html.escape(str(panel.get("title", "")))}</text>')
        parts.append(f'<line class="axis" x1="{ml}" y1="{top}" x2="{ml}" y2="{top+panel_height}"/>')
        parts.append(f'<line class="axis" x1="{ml}" y1="{top+panel_height}" x2="{ml+cw}" y2="{top+panel_height}"/>')
        for idx in range(5):
            value = y_max * idx / 4
            if y_range is not None:
                value = y_min + (y_max - y_min) * idx / 4
            y = yp(value)
            parts.append(f'<line class="grid" x1="{ml}" y1="{y:.1f}" x2="{ml+cw}" y2="{y:.1f}"/>')
            parts.append(f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end">{value:.1f}</text>')
        for idx in range(5):
            x_value = x_min + (x_max - x_min) * idx / 4
            x = xp(x_value)
            x_span = abs(x_max - x_min)
            if x_span < 2:
                x_label = f"{x_value:.2f}"
            elif x_span < 20:
                x_label = f"{x_value:.1f}"
            else:
                x_label = f"{x_value:.0f}"
            parts.append(f'<text x="{x:.1f}" y="{top+panel_height+18}" text-anchor="middle">{x_label}</text>')
        if pi == len(panels) - 1:
            parts.append(f'<text x="{ml+cw/2:.1f}" y="{top+panel_height+40}" text-anchor="middle">{html.escape(xlabel)}</text>')
        parts.append(
            f'<text x="20" y="{top + panel_height/2:.1f}" transform="rotate(-90 20 {top + panel_height/2:.1f})" text-anchor="middle">{html.escape(ylabel)}</text>'
        )

        for si, item in enumerate(series):  # type: ignore[assignment]
            color = str(item.get("color") or COLORS[si % len(COLORS)])
            points = [(float(x), float(y)) for x, y in item.get("points", [])]
            if not points:
                continue
            style = item.get("style", "line")
            if style in {"line", "linepoints"} and len(points) > 1:
                poly = " ".join(f"{xp(x):.1f},{yp(y):.1f}" for x, y in points)
                parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{poly}"/>')
            if style in {"points", "linepoints"} or len(points) <= 80:
                stride = max(1, len(points) // 140)
                for x, y in points[::stride]:
                    parts.append(f'<circle cx="{xp(x):.1f}" cy="{yp(y):.1f}" r="2.7" fill="{color}" opacity="0.78"/>')
            lx = ml + si * 160
            ly = top + panel_height - 8
            parts.append(f'<rect x="{lx}" y="{ly-10}" width="12" height="12" fill="{color}"/>')
            parts.append(f'<text class="legend" x="{lx+18}" y="{ly}">{html.escape(str(item.get("name", "")))}</text>')

    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def svg_scatter(
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    points: list[dict[str, object]],
) -> bool:
    finite = [(number(row.get("x")), number(row.get("y")), str(row.get("category", ""))) for row in points]
    finite = [(x, y, cat) for x, y, cat in finite if x is not None and y is not None]
    if not finite:
        path.with_suffix(".txt").write_text("No matched probe/pcap points available for this figure.\n", encoding="utf-8")
        return False

    width, height = 820, 720
    ml, mr, mt, mb = 82, 36, 56, 82
    cw, ch = width - ml - mr, height - mt - mb
    max_value = max(max(x, y) for x, y, _cat in finite)
    axis_max = max(1.0, max_value * 1.08)
    categories = sorted(set(cat for _x, _y, cat in finite))
    color_by_cat = {cat: COLORS[idx % len(COLORS)] for idx, cat in enumerate(categories)}

    def xp(value: float) -> float:
        return ml + (value / axis_max) * cw

    def yp(value: float) -> float:
        return mt + ch - (value / axis_max) * ch

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,Helvetica,sans-serif;font-size:12px;fill:#1f2933}.title{font-size:18px;font-weight:700}.axis{stroke:#1f2933;stroke-width:1}.grid{stroke:#d8dee4;stroke-width:1}.diag{stroke:#64748b;stroke-dasharray:5 5;stroke-width:1.3}</style>",
        f'<text class="title" x="{width/2:.1f}" y="30" text-anchor="middle">{html.escape(title)}</text>',
        f'<line class="axis" x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ch}"/>',
        f'<line class="axis" x1="{ml}" y1="{mt+ch}" x2="{ml+cw}" y2="{mt+ch}"/>',
        f'<line class="diag" x1="{xp(0):.1f}" y1="{yp(0):.1f}" x2="{xp(axis_max):.1f}" y2="{yp(axis_max):.1f}"/>',
    ]
    for idx in range(6):
        value = axis_max * idx / 5
        x = xp(value)
        y = yp(value)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{mt+ch}"/>')
        parts.append(f'<line class="grid" x1="{ml}" y1="{y:.1f}" x2="{ml+cw}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{x:.1f}" y="{mt+ch+18}" text-anchor="middle">{value:.0f}</text>')
        parts.append(f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end">{value:.0f}</text>')
    parts.append(f'<text x="{ml+cw/2:.1f}" y="{height-28}" text-anchor="middle">{html.escape(xlabel)}</text>')
    parts.append(f'<text x="22" y="{mt+ch/2:.1f}" transform="rotate(-90 22 {mt+ch/2:.1f})" text-anchor="middle">{html.escape(ylabel)}</text>')

    for x, y, cat in finite:
        color = color_by_cat[cat]
        parts.append(f'<circle cx="{xp(x):.1f}" cy="{yp(y):.1f}" r="3.1" fill="{color}" opacity="0.65"/>')
    for idx, cat in enumerate(categories[:8]):
        x = ml + (idx % 4) * 175
        y = height - 58 + (idx // 4) * 18
        parts.append(f'<rect x="{x}" y="{y-10}" width="12" height="12" fill="{color_by_cat[cat]}"/>')
        parts.append(f'<text x="{x+18}" y="{y}">{html.escape(cat[:22])}</text>')

    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def svg_scatter_panels(
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    panels: list[dict[str, object]],
    *,
    width: int = 960,
    panel_height: int = 360,
) -> bool:
    panels = [panel for panel in panels if panel.get("points")]
    if not panels:
        path.with_suffix(".txt").write_text("No paired scatter points available for this figure.\n", encoding="utf-8")
        return False

    ml, mr, mt, mb = 82, 40, 58, 64
    gap = 50
    height = mt + mb + len(panels) * panel_height + (len(panels) - 1) * gap
    cw = width - ml - mr
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,Helvetica,sans-serif;font-size:12px;fill:#1f2933}.title{font-size:18px;font-weight:700}.axis{stroke:#1f2933;stroke-width:1}.grid{stroke:#d8dee4;stroke-width:1}.diag{stroke:#64748b;stroke-dasharray:5 5;stroke-width:1.3}.panel{font-size:13px;font-weight:700}.note{font-size:11px;fill:#334155}</style>",
        f'<text class="title" x="{width/2:.1f}" y="30" text-anchor="middle">{html.escape(title)}</text>',
    ]

    for pi, panel in enumerate(panels):
        top = mt + pi * (panel_height + gap)
        raw_points = panel.get("points", [])
        points = []
        for row in raw_points:  # type: ignore[assignment]
            x = number(row.get("x"))
            y = number(row.get("y"))
            if x is not None and y is not None:
                points.append((x, y, str(row.get("category", ""))))
        if not points:
            continue
        max_value = max(max(x, y) for x, y, _cat in points)
        axis_max = max(1.0 if max_value > 0.75 else 0.1, max_value * 1.08)
        tick_digits = 3 if axis_max < 1 else (1 if axis_max < 10 else 0)
        categories = sorted(set(cat for _x, _y, cat in points))
        color_by_cat = {cat: COLORS[idx % len(COLORS)] for idx, cat in enumerate(categories)}

        def xp(value: float) -> float:
            return ml + (value / axis_max) * cw

        def yp(value: float) -> float:
            return top + panel_height - (value / axis_max) * panel_height

        parts.append(f'<text class="panel" x="{ml}" y="{top-12}">{html.escape(str(panel.get("title", "")))}</text>')
        parts.append(f'<line class="axis" x1="{ml}" y1="{top}" x2="{ml}" y2="{top+panel_height}"/>')
        parts.append(f'<line class="axis" x1="{ml}" y1="{top+panel_height}" x2="{ml+cw}" y2="{top+panel_height}"/>')
        parts.append(f'<line class="diag" x1="{xp(0):.1f}" y1="{yp(0):.1f}" x2="{xp(axis_max):.1f}" y2="{yp(axis_max):.1f}"/>')
        for idx in range(6):
            value = axis_max * idx / 5
            x = xp(value)
            y = yp(value)
            label = f"{value:.{tick_digits}f}"
            parts.append(f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+panel_height}"/>')
            parts.append(f'<line class="grid" x1="{ml}" y1="{y:.1f}" x2="{ml+cw}" y2="{y:.1f}"/>')
            parts.append(f'<text x="{x:.1f}" y="{top+panel_height+18}" text-anchor="middle">{label}</text>')
            parts.append(f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end">{label}</text>')

        for x, y, cat in points:
            color = color_by_cat[cat]
            parts.append(f'<circle cx="{xp(x):.1f}" cy="{yp(y):.1f}" r="3.2" fill="{color}" opacity="0.68"/>')

        for idx, cat in enumerate(categories[:5]):
            lx = ml + idx * 148
            ly = top + panel_height - 8
            parts.append(f'<rect x="{lx}" y="{ly-10}" width="12" height="12" fill="{color_by_cat[cat]}"/>')
            parts.append(f'<text x="{lx+18}" y="{ly}">{html.escape(cat[:18])}</text>')

        for idx, line in enumerate(panel.get("annotation", [])):  # type: ignore[assignment]
            parts.append(f'<text class="note" x="{ml+cw-260}" y="{top+18+idx*16}">{html.escape(str(line))}</text>')

        if pi == len(panels) - 1:
            parts.append(f'<text x="{ml+cw/2:.1f}" y="{top+panel_height+46}" text-anchor="middle">{html.escape(xlabel)}</text>')
        parts.append(
            f'<text x="22" y="{top+panel_height/2:.1f}" transform="rotate(-90 22 {top+panel_height/2:.1f})" text-anchor="middle">{html.escape(ylabel)}</text>'
        )

    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def svg_boxplots(
    path: Path,
    title: str,
    ylabel: str,
    boxes: list[dict[str, object]],
    *,
    width: int | None = None,
    height: int = 620,
) -> bool:
    clean: list[dict[str, object]] = []
    for box in boxes:
        values = [v for v in (number(item) for item in box.get("values", [])) if v is not None]
        if len(values) < 2:
            continue
        clean.append({**box, "values": values})
    if not clean:
        path.with_suffix(".txt").write_text("No distribution data available for this figure.\n", encoding="utf-8")
        return False

    width = width or max(960, 120 + len(clean) * 82)
    ml, mr, mt, mb = 76, 32, 58, 160
    cw, ch = width - ml - mr, height - mt - mb
    whisker_values = []
    for box in clean:
        values = box["values"]  # type: ignore[assignment]
        whisker_values.extend([percentile(values, 0.05), percentile(values, 0.95)])
    y_min = min(0.0, min(whisker_values))
    y_max = max(whisker_values) * 1.16
    if y_max <= y_min:
        y_max = y_min + 1.0

    def xp(index: int) -> float:
        return ml + (index + 0.5) * (cw / len(clean))

    def yp(value: float) -> float:
        return mt + ch - ((value - y_min) / (y_max - y_min)) * ch

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,Helvetica,sans-serif;font-size:12px;fill:#1f2933}.title{font-size:18px;font-weight:700}.axis{stroke:#1f2933;stroke-width:1}.grid{stroke:#d8dee4;stroke-width:1}.median{stroke:#111827;stroke-width:2}.whisker{stroke:#475569;stroke-width:1.5}</style>",
        f'<text class="title" x="{width/2:.1f}" y="30" text-anchor="middle">{html.escape(title)}</text>',
        f'<text x="22" y="{mt + ch/2:.1f}" transform="rotate(-90 22 {mt + ch/2:.1f})" text-anchor="middle">{html.escape(ylabel)}</text>',
        f'<line class="axis" x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ch}"/>',
        f'<line class="axis" x1="{ml}" y1="{mt+ch}" x2="{ml+cw}" y2="{mt+ch}"/>',
    ]
    for idx in range(6):
        value = y_min + (y_max - y_min) * idx / 5
        y = yp(value)
        parts.append(f'<line class="grid" x1="{ml}" y1="{y:.1f}" x2="{ml+cw}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end">{value:.1f}</text>')

    box_w = max(22, min(50, (cw / len(clean)) * 0.55))
    for idx, box in enumerate(clean):
        values = sorted(box["values"])  # type: ignore[arg-type]
        label = str(box.get("label", ""))
        color = str(box.get("color") or COLORS[idx % len(COLORS)])
        p05 = percentile(values, 0.05)
        q1 = percentile(values, 0.25)
        med = percentile(values, 0.50)
        q3 = percentile(values, 0.75)
        p95 = percentile(values, 0.95)
        mean = sum(values) / len(values)
        x = xp(idx)
        left, right = x - box_w / 2, x + box_w / 2
        parts.append(f'<line class="whisker" x1="{x:.1f}" y1="{yp(p05):.1f}" x2="{x:.1f}" y2="{yp(p95):.1f}"/>')
        parts.append(f'<line class="whisker" x1="{left:.1f}" y1="{yp(p05):.1f}" x2="{right:.1f}" y2="{yp(p05):.1f}"/>')
        parts.append(f'<line class="whisker" x1="{left:.1f}" y1="{yp(p95):.1f}" x2="{right:.1f}" y2="{yp(p95):.1f}"/>')
        parts.append(f'<rect x="{left:.1f}" y="{yp(q3):.1f}" width="{box_w:.1f}" height="{max(1, yp(q1)-yp(q3)):.1f}" fill="{color}" opacity="0.55" stroke="{color}" stroke-width="1.5"/>')
        parts.append(f'<line class="median" x1="{left:.1f}" y1="{yp(med):.1f}" x2="{right:.1f}" y2="{yp(med):.1f}"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{yp(mean):.1f}" r="3.0" fill="{color}" stroke="#111827" stroke-width="0.8"/>')
        parts.append(
            f'<text x="{x:.1f}" y="{mt+ch+20}" text-anchor="end" transform="rotate(-38 {x:.1f} {mt+ch+20})">{html.escape(label)}</text>'
        )

    parts.append(f'<text x="{ml}" y="{height-24}">Boxes show IQR; line is median; dot is mean; whiskers are p5-p95.</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def svg_message(path: Path, title: str, lines: list[str]) -> None:
    width, height = 920, 360
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,Helvetica,sans-serif;fill:#1f2933}.title{font-size:18px;font-weight:700}.body{font-size:14px}</style>',
        f'<text class="title" x="46" y="54">{html.escape(title)}</text>',
    ]
    for idx, line in enumerate(lines):
        parts.append(f'<text class="body" x="46" y="{96 + idx * 28}">{html.escape(line)}</text>')
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def downsample_points(points: list[tuple[float, float]], max_points: int = 700) -> list[tuple[float, float]]:
    if len(points) <= max_points:
        return points
    stride = max(1, math.ceil(len(points) / max_points))
    sampled = points[::stride]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def cdf_points(values: list[float], max_points: int = 500) -> list[tuple[float, float]]:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return []
    n = len(ordered)
    points = [(value, (idx + 1) / n) for idx, value in enumerate(ordered)]
    if ordered[0] > 0:
        points.insert(0, (0.0, 0.0))
    return downsample_points(points, max_points=max_points)


def nearest_actual_pairs(
    pcap_points: list[tuple[float, float]],
    probe_points: list[tuple[float, float]],
    *,
    max_delta_s: float = 0.75,
) -> list[dict[str, float]]:
    if not pcap_points or not probe_points:
        return []
    pcap_points = sorted(pcap_points)
    probe_points = sorted(probe_points)
    pcap_ts = [item[0] for item in pcap_points]
    pairs: list[dict[str, float]] = []
    for probe_ts, probe_value in probe_points:
        idx = bisect.bisect_left(pcap_ts, probe_ts)
        candidates = []
        if idx > 0:
            candidates.append(pcap_points[idx - 1])
        if idx < len(pcap_points):
            candidates.append(pcap_points[idx])
        if not candidates:
            continue
        pcap_ts_match, pcap_value = min(candidates, key=lambda item: abs(item[0] - probe_ts))
        delta = abs(pcap_ts_match - probe_ts)
        if delta <= max_delta_s:
            pairs.append(
                {
                    "timestamp": probe_ts,
                    "pcap_timestamp": pcap_ts_match,
                    "pcap_rtt_ms": pcap_value,
                    "probe_rtt_ms": probe_value,
                    "time_delta_ms": delta * 1000.0,
                }
            )
    return pairs


def create_resource_figures(out_dir: Path, prom: list[dict[str, str]]) -> list[str]:
    notes: list[str] = []
    available_queries = {row.get("query_name") for row in prom}
    cpu_query = "container_cpu_cores_30s_any" if "container_cpu_cores_30s_any" in available_queries else "container_cpu_cores_30s"
    mem_query = "container_memory_mib_any" if "container_memory_mib_any" in available_queries else "container_memory_mib"

    def summarize_query(query: str) -> list[dict[str, object]]:
        grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for row in prom:
            if row.get("query_name") != query:
                continue
            value = number(row.get("value"))
            namespace = row.get("namespace") or ""
            pod = row.get("pod") or row.get("instance") or ""
            container = row.get("container") or ""
            text = f"{namespace} {pod} {container}"
            if value is None or not container or container == "POD":
                continue
            if namespace not in {"open5gs", "monitoring"}:
                continue
            if not MONITOR_CONTAINER_RE.search(text):
                continue
            grouped[(namespace, pod, container)].append(value)
        rows: list[dict[str, object]] = []
        for (namespace, pod, container), vals in grouped.items():
            stats = summarize(vals)
            if stats["count"] < 5:
                continue
            rows.append(
                {
                    "namespace": namespace,
                    "pod": pod,
                    "container": container,
                    "label": shorten_label(namespace, pod, container),
                    "count": int(stats["count"]),
                    "mean": stats["mean"],
                    "p95": stats["p95"],
                    "max": stats["max"],
                    "values": vals,
                }
            )
        return sorted(rows, key=lambda item: (str(item["label"]), -float(item["mean"])))

    cpu_rows = summarize_query(cpu_query)
    mem_rows = summarize_query(mem_query)
    summary_rows = []
    for metric, rows in (("cpu_cores", cpu_rows), ("memory_mib", mem_rows)):
        for row in rows:
            summary_rows.append({"metric": metric, **row})
    write_csv(
        out_dir / "resource_container_usage_summary.csv",
        summary_rows,
        ["metric", "namespace", "pod", "container", "label", "count", "mean", "p95", "max"],
    )

    if cpu_rows:
        rows = sorted(cpu_rows, key=lambda row: number(row["p95"], 0) or 0, reverse=True)[:12]
        categories = [str(row["label"]) for row in rows]
        values = {}
        for row in rows:
            values[(str(row["label"]), "mean")] = float(row["mean"])
            values[(str(row["label"]), "p95")] = float(row["p95"])
        if svg_grouped_bars(out_dir / "fig01_container_cpu_usage.svg", "Monitoring container CPU usage", "CPU cores", categories, ["mean", "p95"], values, height=620, value_digits=3):
            notes.append("fig01_container_cpu_usage.svg: mean and p95 CPU usage for monitoring/control containers.")
        boxes = [{"label": str(row["label"]), "values": row["values"]} for row in rows]
        if svg_boxplots(out_dir / "fig08_container_cpu_boxplot.svg", "Monitoring container CPU distribution", "CPU cores", boxes, height=650):
            notes.append("fig08_container_cpu_boxplot.svg: CPU usage distributions for monitoring/control containers.")

    if mem_rows:
        rows = sorted(mem_rows, key=lambda row: number(row["p95"], 0) or 0, reverse=True)[:12]
        categories = [str(row["label"]) for row in rows]
        values = {}
        for row in rows:
            values[(str(row["label"]), "mean")] = float(row["mean"])
            values[(str(row["label"]), "p95")] = float(row["p95"])
        if svg_grouped_bars(out_dir / "fig02_container_memory_usage.svg", "Monitoring container memory usage", "Memory (MiB)", categories, ["mean", "p95"], values, height=620, value_digits=1):
            notes.append("fig02_container_memory_usage.svg: mean and p95 memory usage for monitoring/control containers.")
        boxes = [{"label": str(row["label"]), "values": row["values"]} for row in rows]
        if svg_boxplots(out_dir / "fig09_container_memory_boxplot.svg", "Monitoring container memory distribution", "Memory (MiB)", boxes, height=650):
            notes.append("fig09_container_memory_boxplot.svg: memory working-set distributions for monitoring/control containers.")
    return notes


def create_bpf_figure(results_dir: Path, out_dir: Path) -> list[str]:
    rows = read_csv(results_dir / "bpftool_overhead_summary.csv")
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        if row.get("prog_type") != "sched_cls":
            continue
        name = row.get("prog_name") or ""
        if name not in {"tc_pass_base", "tc_gtp_teid_ingress", "tc_gtp_teid_egress"}:
            continue
        ns = number(row.get("ns_per_run"))
        runs = number(row.get("run_cnt_delta"))
        if ns is None or runs is None or runs <= 0:
            continue
        grouped[name].append((ns, runs))

    labels = {
        "tc_pass_base": "TC pass baseline",
        "tc_gtp_teid_ingress": "Latency probe ingress",
        "tc_gtp_teid_egress": "Latency probe egress",
    }
    summary_rows: list[dict[str, object]] = []
    weighted_by_name: dict[str, float] = {}
    for name in ["tc_pass_base", "tc_gtp_teid_ingress", "tc_gtp_teid_egress"]:
        vals = grouped.get(name, [])
        if not vals:
            continue
        total_runs = sum(cnt for _ns, cnt in vals)
        weighted = sum(ns * cnt for ns, cnt in vals) / total_runs
        weighted_by_name[name] = weighted
        summary_rows.append(
            {
                "program": name,
                "label": labels[name],
                "windows": len(vals),
                "weighted_ns_per_run": f"{weighted:.3f}",
                "weighted_us_per_run": f"{weighted / 1000:.6f}",
                "total_runs": int(total_runs),
            }
        )
    baseline = weighted_by_name.get("tc_pass_base")
    for row in summary_rows:
        weighted = number(row.get("weighted_ns_per_run"))
        if baseline and weighted is not None:
            row["ratio_vs_tc_pass_base"] = f"{weighted / baseline:.2f}"
            row["extra_ns_vs_tc_pass_base"] = f"{weighted - baseline:.3f}"
        else:
            row["ratio_vs_tc_pass_base"] = ""
            row["extra_ns_vs_tc_pass_base"] = ""
    write_csv(
        out_dir / "bpf_baseline_vs_probe_summary.csv",
        summary_rows,
        ["program", "label", "windows", "weighted_ns_per_run", "weighted_us_per_run", "total_runs", "ratio_vs_tc_pass_base", "extra_ns_vs_tc_pass_base"],
    )

    budget_rows: list[dict[str, object]] = []
    budget_profiles = [
        ("latency_probe_ingress", "Latency probe ingress", weighted_by_name.get("tc_gtp_teid_ingress")),
        ("latency_probe_egress", "Latency probe egress", weighted_by_name.get("tc_gtp_teid_egress")),
    ]
    ingress = weighted_by_name.get("tc_gtp_teid_ingress")
    egress = weighted_by_name.get("tc_gtp_teid_egress")
    if ingress is not None and egress is not None:
        budget_profiles.append(("latency_probe_ingress_plus_egress", "Latency probe ingress+egress", ingress + egress))
    for program, label, ns_per_packet in budget_profiles:
        if ns_per_packet is None or ns_per_packet <= 0:
            continue
        for core_fraction in (0.10, 0.50, 0.80, 1.00):
            pps = (core_fraction * 1_000_000_000.0) / ns_per_packet
            budget_rows.append(
                {
                    "program": program,
                    "label": label,
                    "ns_per_packet_path_run": f"{ns_per_packet:.3f}",
                    "cpu_core_fraction": f"{core_fraction:.2f}",
                    "packet_rate_pps": f"{pps:.1f}",
                    "throughput_mbps_at_1500B": f"{pps * 1500 * 8 / 1_000_000:.3f}",
                    "throughput_mbps_at_64B": f"{pps * 64 * 8 / 1_000_000:.3f}",
                }
            )
    write_csv(
        out_dir / "bpf_packet_rate_budget_summary.csv",
        budget_rows,
        [
            "program",
            "label",
            "ns_per_packet_path_run",
            "cpu_core_fraction",
            "packet_rate_pps",
            "throughput_mbps_at_1500B",
            "throughput_mbps_at_64B",
        ],
    )

    categories = [str(row["label"]) for row in summary_rows]
    values = {(str(row["label"]), "ns/run"): number(row["weighted_ns_per_run"], 0.0) or 0.0 for row in summary_rows}
    notes = []
    if svg_grouped_bars(out_dir / "fig03_bpf_baseline_vs_probe.svg", "Passive TC baseline vs latency probe", "Weighted ns/run", categories, ["ns/run"], values, log_scale=True, height=540, value_digits=1):
        notes.append("fig03_bpf_baseline_vs_probe.svg: TC_ACT_OK baseline compared with the latency probe packet path.")
    if budget_rows:
        budget_categories = []
        budget_values: dict[tuple[str, str], float] = {}
        for label in ["Latency probe ingress", "Latency probe egress", "Latency probe ingress+egress"]:
            rows_for_label = [row for row in budget_rows if row.get("label") == label]
            if not rows_for_label:
                continue
            budget_categories.append(label)
            for row in rows_for_label:
                fraction = number(row.get("cpu_core_fraction"), 0) or 0
                if fraction in {0.10, 0.50, 0.80}:
                    budget_values[(label, f"{int(fraction * 100)}% core")] = number(row.get("packet_rate_pps"), 0.0) or 0.0
        if svg_grouped_bars(
            out_dir / "fig19_bpf_packet_rate_budget.svg",
            "Packet-rate budget before eBPF CPU becomes visible",
            "Packet rate (pps)",
            budget_categories,
            ["10% core", "50% core", "80% core"],
            budget_values,
            log_scale=True,
            height=600,
            value_digits=0,
        ):
            notes.append("fig19_bpf_packet_rate_budget.svg: packet-rate thresholds where measured eBPF cost consumes 10%, 50%, and 80% of one CPU core.")
    return notes


def create_netem_figure(results_dir: Path, out_dir: Path, prom: list[dict[str, str]], windows: list[dict[str, str]], ue_ip: str) -> list[str]:
    controlled = direction_windows(windows, "v03_controlled_delay")
    if not controlled:
        return ["No controlled-delay windows were found; netem detection figure was not created."]
    series_specs = [
        ("gNB core/UL", "gnb", "core", COLORS[0]),
        ("gNB ran/DL", "gnb", "ran", COLORS[1]),
        ("UPF core/UL", "upf", "core", COLORS[2]),
        ("UPF ran/DL", "upf", "ran", COLORS[3]),
    ]
    summary_groups: dict[tuple[str, str, str, str, str], list[float]] = defaultdict(list)
    for name, role, mode, _color in series_specs:
        for row in prom:
            if row.get("query_name") != "direct_mean_ms_5s":
                continue
            if ue_ip and row.get("ue_ip") != ue_ip:
                continue
            if row.get("probe_role") != role or row.get("mode") != mode:
                continue
            ts = number(row.get("timestamp"))
            value = number(row.get("value"))
            if ts is None or value is None:
                continue
            for win in controlled:
                start = number(win.get("start_epoch"), -1) or -1
                end = number(win.get("end_epoch"), -2) or -2
                if start <= ts <= end:
                    summary_groups[(win.get("step", ""), win.get("direction", ""), role, mode, name)].append(value)
                    break

    summary_rows = []
    stats_by_key: dict[tuple[str, str, str, str], dict[str, float]] = {}
    values_by_key: dict[tuple[str, str, str, str], list[float]] = {}
    for (step, direction, role, mode, name), vals in sorted(summary_groups.items()):
        stats = summarize(vals)
        stats_by_key[(step, direction, role, mode)] = stats
        values_by_key[(step, direction, role, mode)] = vals
        summary_rows.append(
            {
                "step": step,
                "direction": direction,
                "series": name,
                "probe_role": role,
                "mode": mode,
                "count": int(stats["count"]),
                "mean_ms": f"{stats['mean']:.3f}",
                "p50_ms": f"{stats['p50']:.3f}",
                "p95_ms": f"{stats['p95']:.3f}",
                "max_ms": f"{stats['max']:.3f}",
            }
        )
    write_csv(out_dir / "netem_detection_probe_summary.csv", summary_rows, ["step", "direction", "series", "probe_role", "mode", "count", "mean_ms", "p50_ms", "p95_ms", "max_ms"])

    pcap_stats_by_key: dict[tuple[str, str, str], dict[str, float]] = {}
    pcap_groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in iter_csv(results_dir / "pcap_tcp_rtt.csv"):
        value = number(row.get("rtt_ms"))
        payload_len = number(row.get("payload_len"), 0)
        if value is None or payload_len is None or payload_len <= 0:
            continue
        if ue_ip and ue_ip not in {row.get("data_src"), row.get("data_dst"), row.get("ack_src"), row.get("ack_dst")}:
            continue
        scenario, step, direction, role = parse_pcap_name(row.get("pcap_file", ""))
        if scenario != "v03_controlled_delay" or role not in {"ran", "upf"}:
            continue
        pcap_groups[(step, direction, role)].append(value)
    for key, values in pcap_groups.items():
        pcap_stats_by_key[key] = summarize(values)

    def resolve_stats(case: dict[str, object]) -> tuple[dict[str, float] | None, dict[str, float] | None, str]:
        ref_key = case["reference"]  # type: ignore[index]
        obs_key = case["observed"]  # type: ignore[index]
        ref_stats = stats_by_key.get(ref_key)  # type: ignore[arg-type]
        obs_stats = stats_by_key.get(obs_key)  # type: ignore[arg-type]
        if ref_stats and obs_stats:
            return ref_stats, obs_stats, "probe"

        fallback = case.get("pcap_fallback")
        if not fallback:
            return ref_stats, obs_stats, "probe"
        ref_step, obs_step, direction, role = fallback  # type: ignore[misc]
        ref_stats = pcap_stats_by_key.get((ref_step, direction, role))
        obs_stats = pcap_stats_by_key.get((obs_step, direction, role))
        return ref_stats, obs_stats, "pcap"

    def resolve_values(case: dict[str, object]) -> tuple[list[float], list[float], str]:
        ref_key = case["reference"]  # type: ignore[index]
        obs_key = case["observed"]  # type: ignore[index]
        ref_values = values_by_key.get(ref_key, [])  # type: ignore[arg-type]
        obs_values = values_by_key.get(obs_key, [])  # type: ignore[arg-type]
        if ref_values and obs_values:
            return ref_values, obs_values, "probe"

        fallback = case.get("pcap_fallback")
        if not fallback:
            return ref_values, obs_values, "probe"
        ref_step, obs_step, direction, role = fallback  # type: ignore[misc]
        return pcap_groups.get((ref_step, direction, role), []), pcap_groups.get((obs_step, direction, role), []), "pcap"

    def render_delay_cases(
        *,
        cases: list[dict[str, object]],
        summary_name: str,
        figure_name: str,
        title: str,
        note: str,
    ) -> list[str]:
        delay_rows: list[dict[str, object]] = []
        categories: list[str] = []
        values: dict[tuple[str, str], float] = {}
        for case in cases:
            ref_stats, obs_stats, source = resolve_stats(case)
            if not ref_stats or not obs_stats:
                continue
            ref = ref_stats["p50"]
            observed = obs_stats["p50"]
            expected = ref + float(case["expected_extra_ms"])
            label = str(case["label"])
            reference = case["reference"]  # type: ignore[assignment]
            observed_key = case["observed"]  # type: ignore[assignment]
            categories.append(label)
            values[(label, "reference")] = ref
            values[(label, "expected")] = expected
            values[(label, "observed")] = observed
            delay_rows.append(
                {
                    "case": label,
                    "reference_step": reference[0],
                    "observed_step": observed_key[0],
                    "direction": observed_key[1],
                    "probe_role": observed_key[2],
                    "mode": observed_key[3],
                    "source": source,
                    "reference_p50_ms": f"{ref:.3f}",
                    "expected_p50_ms": f"{expected:.3f}",
                    "observed_p50_ms": f"{observed:.3f}",
                    "observed_minus_reference_ms": f"{observed - ref:.3f}",
                    "observed_minus_expected_ms": f"{observed - expected:.3f}",
                    "note": case["note"],
                }
            )

        write_csv(
            out_dir / summary_name,
            delay_rows,
            [
                "case",
                "reference_step",
                "observed_step",
                "direction",
                "probe_role",
                "mode",
                "source",
                "reference_p50_ms",
                "expected_p50_ms",
                "observed_p50_ms",
                "observed_minus_reference_ms",
                "observed_minus_expected_ms",
                "note",
            ],
        )

        notes = []
        if svg_grouped_bars(
            out_dir / figure_name,
            title,
            "Latency p50 (ms)",
            categories,
            ["reference", "expected", "observed"],
            values,
            height=560,
            value_digits=1,
        ):
            notes.append(note)
        return notes

    component_local_cases = [
        {
            "label": "RAN DL gNB/core",
            "reference": ("qhat01_p1_no_netem_reference", "dl", "gnb", "core"),
            "observed": ("qhat01_p1_ran_netem_200ms", "dl", "gnb", "core"),
            "expected_extra_ms": 200.0,
            "note": "gNB probe / core ACK-side view",
        },
        {
            "label": "RAN UL gNB/core",
            "reference": ("qhat01_p1_no_netem_reference", "ul", "gnb", "core"),
            "observed": ("qhat01_p1_ran_netem_200ms", "ul", "gnb", "core"),
            "expected_extra_ms": 200.0,
            "note": "gNB probe / core mode",
        },
        {
            "label": "UPF DL UPF/ran",
            "reference": ("qhat01_p1_no_netem_reference", "dl", "upf", "ran"),
            "observed": ("qhat01_p1_upf_netem_200ms", "dl", "upf", "ran"),
            "pcap_fallback": ("qhat01_p1_no_netem_reference", "qhat01_p1_upf_netem_200ms", "dl", "upf"),
            "expected_extra_ms": 200.0,
            "note": "UPF probe / ran mode; falls back to UPF pcap view if UPF probe series is absent",
        },
        {
            "label": "UPF UL UPF/core",
            "reference": ("qhat01_p1_no_netem_reference", "ul", "upf", "core"),
            "observed": ("qhat01_p1_upf_netem_200ms", "ul", "upf", "core"),
            "pcap_fallback": ("qhat01_p1_no_netem_reference", "qhat01_p1_upf_netem_200ms", "ul", "upf"),
            "expected_extra_ms": 200.0,
            "note": "UPF probe / core mode; falls back to UPF pcap view if UPF probe series is absent",
        },
    ]

    cross_boundary_cases = [
        {
            "label": "RAN DL UPF/ran",
            "reference": ("qhat01_p1_no_netem_reference", "dl", "upf", "ran"),
            "observed": ("qhat01_p1_ran_netem_200ms", "dl", "upf", "ran"),
            "expected_extra_ms": 200.0,
            "note": "UPF probe / ran mode; one delayed RAN crossing",
        },
        {
            "label": "RAN UL gNB/core",
            "reference": ("qhat01_p1_no_netem_reference", "ul", "gnb", "core"),
            "observed": ("qhat01_p1_ran_netem_200ms", "ul", "gnb", "core"),
            "expected_extra_ms": 200.0,
            "note": "gNB probe / core mode; one delayed RAN crossing",
        },
        {
            "label": "UPF DL gNB/core",
            "reference": ("qhat01_p1_no_netem_reference", "dl", "gnb", "core"),
            "observed": ("qhat01_p1_upf_netem_200ms", "dl", "gnb", "core"),
            "expected_extra_ms": 400.0,
            "note": "gNB probe / core mode; delayed UPF boundary crossed twice",
        },
        {
            "label": "UPF UL gNB/core",
            "reference": ("qhat01_p1_no_netem_reference", "ul", "gnb", "core"),
            "observed": ("qhat01_p1_upf_netem_200ms", "ul", "gnb", "core"),
            "expected_extra_ms": 400.0,
            "note": "gNB probe / core mode; delayed UPF boundary crossed twice",
        },
    ]

    notes = []
    notes.extend(
        render_delay_cases(
            cases=component_local_cases,
            summary_name="controlled_delay_pickup_summary.csv",
            figure_name="fig04_controlled_delay_pickup.svg",
            title="Controlled-delay pickup from component-local views",
            note="fig04_controlled_delay_pickup.svg: reference p50, expected p50 after IFB/netem delay, and observed component-local p50.",
        )
    )
    notes.extend(
        render_delay_cases(
            cases=cross_boundary_cases,
            summary_name="controlled_delay_cross_boundary_summary.csv",
            figure_name="fig04b_controlled_delay_cross_boundary.svg",
            title="Controlled-delay pickup from cross-boundary views",
            note="fig04b_controlled_delay_cross_boundary.svg: reference p50, expected p50, and observed p50 from views that expose one- vs two-crossing delay effects.",
        )
    )

    box_rows: list[dict[str, object]] = []
    boxes: list[dict[str, object]] = []
    for case in cross_boundary_cases:
        ref_values, obs_values, source = resolve_values(case)
        if not ref_values or not obs_values:
            continue
        label = str(case["label"]).replace(" gNB/core", "").replace(" UPF/ran", "").replace(" UPF/core", "")
        for phase, values in (("reference", ref_values), ("netem", obs_values)):
            stats = summarize(values)
            box_label = f"{label} {phase}"
            boxes.append({"label": box_label, "values": values})
            box_rows.append(
                {
                    "case": str(case["label"]),
                    "phase": phase,
                    "source": source,
                    "count": int(stats["count"]),
                    "mean_ms": fmt(stats["mean"]),
                    "p50_ms": fmt(stats["p50"]),
                    "p95_ms": fmt(stats["p95"]),
                    "p99_ms": fmt(stats["p99"]),
                    "max_ms": fmt(stats["max"]),
                }
            )
    write_csv(
        out_dir / "controlled_delay_boxplot_summary.csv",
        box_rows,
        ["case", "phase", "source", "count", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"],
    )
    if svg_boxplots(
        out_dir / "fig10_controlled_delay_boxplot.svg",
        "Controlled-delay latency distributions",
        "Latency (ms)",
        boxes,
        height=650,
    ):
        notes.append("fig10_controlled_delay_boxplot.svg: controlled-delay reference and netem latency distributions using probe-backed cross-boundary views.")

    increase_rows: list[dict[str, object]] = []
    increase_boxes: list[dict[str, object]] = []
    median_pickup_categories: list[str] = []
    median_pickup_values: dict[tuple[str, str], float] = {}
    for case in component_local_cases:
        ref_values, obs_values, source = resolve_values(case)
        if not ref_values or not obs_values:
            continue
        ref_p50 = median(ref_values)
        increases = [value - ref_p50 for value in obs_values]
        stats = summarize(increases)
        label = " ".join(str(case["label"]).split()[:2])
        increase_boxes.append({"label": label, "values": increases})
        median_pickup_categories.append(label)
        median_pickup_values[(label, "expected")] = float(case["expected_extra_ms"])
        median_pickup_values[(label, "observed p50")] = stats["p50"]
        increase_rows.append(
            {
                "case": str(case["label"]),
                "source": source,
                "reference_p50_ms": fmt(ref_p50),
                "expected_increase_ms": fmt(case["expected_extra_ms"]),
                "observed_increase_mean_ms": fmt(stats["mean"]),
                "observed_increase_p50_ms": fmt(stats["p50"]),
                "observed_increase_p95_ms": fmt(stats["p95"]),
                "observed_increase_p99_ms": fmt(stats["p99"]),
            }
        )
    write_csv(
        out_dir / "controlled_delay_increase_boxplot_summary.csv",
        increase_rows,
        [
            "case",
            "source",
            "reference_p50_ms",
            "expected_increase_ms",
            "observed_increase_mean_ms",
            "observed_increase_p50_ms",
            "observed_increase_p95_ms",
            "observed_increase_p99_ms",
        ],
    )
    if svg_boxplots(
        out_dir / "fig17_controlled_delay_increase_boxplot.svg",
        "Controlled-delay pickup as latency increase",
        "Observed increase over reference p50 (ms)",
        increase_boxes,
        height=600,
    ):
        notes.append("fig17_controlled_delay_increase_boxplot.svg: compact box plot of observed latency increase over the reference p50 for component-local validation views.")
    if svg_grouped_bars(
        out_dir / "fig18_controlled_delay_median_pickup.svg",
        "Controlled-delay median pickup",
        "Latency increase over reference p50 (ms)",
        median_pickup_categories,
        ["expected", "observed p50"],
        median_pickup_values,
        height=540,
        value_digits=1,
    ):
        notes.append("fig18_controlled_delay_median_pickup.svg: clean median-only controlled-delay pickup plot that is robust to pcap tail outliers.")
    return notes


def create_icmp_figure(results_dir: Path, out_dir: Path, prom: list[dict[str, str]], windows: list[dict[str, str]], ue_ip: str) -> list[str]:
    ping_rows = read_csv(results_dir / "ping_rtt.csv")
    ping_window_keys = {
        (row.get("scenario", ""), row.get("step", ""), row.get("direction", ""))
        for row in ping_rows
        if row.get("scenario") and row.get("step") and row.get("direction")
    }
    icmp_windows = [
        win
        for win in direction_windows(windows)
        if (win.get("scenario", ""), win.get("step", ""), win.get("direction", "")) in ping_window_keys
        and not (win.get("scenario") in PARALLEL_TCP_ICMP_SCENARIOS and win.get("direction") != "dl")
    ]
    available_queries = {row.get("query_name") for row in prom}
    probe_query = next(
        (
            query
            for query in (
                "direct_median_1s_ms_by_kind",
                "direct_median_1s_ms",
                "direct_p50_ms_5s_by_kind",
                "direct_p50_ms_5s",
                "direct_latest_ms",
            )
            if query in available_queries
        ),
        "direct_latest_ms",
    )
    probe_label = "probe ICMP median" if "median" in probe_query else ("probe ICMP p50" if "p50" in probe_query else "probe ICMP latest")
    panels = []
    output_rows: list[dict[str, object]] = []
    for win in icmp_windows:
        direction = win.get("direction", "")
        start = number(win.get("start_epoch"), 0) or 0
        end = number(win.get("end_epoch"), start) or start
        ping_points = []
        ping_abs_times = []
        for row in ping_rows:
            if row.get("scenario") != win.get("scenario") or row.get("step") != win.get("step") or row.get("direction") != direction:
                continue
            ts = number(row.get("epoch"))
            value = number(row.get("rtt_ms"))
            if ts is None or value is None:
                continue
            ping_abs_times.append(ts)
            ping_points.append((ts - start, value))
            output_rows.append({"direction": direction, "source": "ping", "timestamp": f"{ts:.6f}", "seconds": f"{ts-start:.3f}", "latency_ms": f"{value:.6f}"})

        ping_timeout = number(win.get("ping_timeout"), 2) or 2
        probe_start = min(ping_abs_times) if ping_abs_times else start
        probe_end = (max(ping_abs_times) + ping_timeout + 0.5) if ping_abs_times else end
        probe_points = []
        for row in prom:
            if row.get("query_name") != probe_query:
                continue
            ts = number(row.get("timestamp"))
            value = number(row.get("value"))
            if ts is None or value is None or ts < probe_start or ts > probe_end:
                continue
            if ue_ip and row.get("ue_ip") != ue_ip:
                continue
            if row.get("probe_role") != "gnb" or row.get("mode") != direction_mode(direction):
                continue
            kind = metric_kind(row)
            if kind and "icmp" not in kind:
                continue
            probe_points.append((ts - start, value))
            output_rows.append({"direction": direction, "source": probe_query, "timestamp": f"{ts:.6f}", "seconds": f"{ts-start:.3f}", "latency_ms": f"{value:.6f}"})
        if ping_points or probe_points:
            panels.append(
                {
                    "title": f"{win.get('scenario', '')} {direction.replace('_', ' ').upper()}",
                    "series": [
                        {"name": "ping output", "points": sorted(ping_points), "style": "linepoints", "color": COLORS[0]},
                        {"name": probe_label, "points": sorted(probe_points), "style": "linepoints", "color": COLORS[1]},
                    ],
                    "bands": [],
                }
            )

    write_csv(out_dir / "icmp_ping_vs_probe_timeseries.csv", output_rows, ["direction", "source", "timestamp", "seconds", "latency_ms"])
    notes = []
    if svg_timeseries_panels(out_dir / "fig05_icmp_ping_vs_probe_timeseries.svg", "ICMP validation over time: ping output vs probe", "Latency (ms)", panels):
        notes.append(f"fig05_icmp_ping_vs_probe_timeseries.svg: connected per-ping RTT samples overlaid with connected ICMP probe samples over time using {probe_query}.")
    return notes


def maybe_reconstruct_pcap_rtt(results_dir: Path) -> list[str]:
    csv_path = results_dir / "pcap_tcp_rtt.csv"
    if read_csv(csv_path):
        return []
    pcap_dir = results_dir / "pcaps"
    pcaps = []
    for pattern in ("*.pcap", "*.pcapng", "*.pcap.gz", "*.pcapng.gz"):
        pcaps.extend(pcap_dir.glob(pattern))
    if not pcaps:
        return ["No pcaps were fetched, so TCP pcap RTT comparison was skipped."]
    if not shutil.which("tshark"):
        return ["pcap_tcp_rtt.csv is empty and local tshark is unavailable; TCP pcap RTT comparison was skipped."]
    extractor = Path(__file__).with_name("pcap_tcp_rtt_extract.py")
    proc = subprocess.run(
        [sys.executable, str(extractor), "--pcap-dir", str(pcap_dir), "--out", str(csv_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        suffix = f" Last message: {detail[-1]}" if detail else ""
        return [f"pcap RTT reconstruction failed with exit code {proc.returncode}.{suffix}"]
    return ["pcap_tcp_rtt.csv was locally regenerated from fetched pcaps before plotting."]


def create_tcp_pcap_figure(results_dir: Path, out_dir: Path, prom: list[dict[str, str]], windows: list[dict[str, str]], ue_ip: str) -> list[str]:
    notes = maybe_reconstruct_pcap_rtt(results_dir)
    pcap_path = results_dir / "pcap_tcp_rtt.csv"
    if not pcap_path.exists():
        svg_message(
            out_dir / "fig06_tcp_probe_vs_pcap_rtt_mean_timeseries.svg",
            "TCP probe-vs-pcap RTT mean over time unavailable",
            [
                "pcap_tcp_rtt.csv is missing.",
                "Run create_validation_paper_figures.py locally on a machine with tshark, or run pcap_tcp_rtt_extract.py first.",
                "The validation pcaps are still usable if they were fetched into the pcaps/ directory.",
            ],
        )
        svg_message(
            out_dir / "fig06b_tcp_probe_vs_pcap_rtt_mean_only.svg",
            "TCP probe-vs-pcap RTT mean-only view unavailable",
            [
                "pcap_tcp_rtt.csv is missing.",
                "Run create_validation_paper_figures.py locally on a machine with tshark, or run pcap_tcp_rtt_extract.py first.",
                "The validation pcaps are still usable if they were fetched into the pcaps/ directory.",
            ],
        )
        svg_message(
            out_dir / "fig07_tcp_probe_vs_pcap_rtt_actual_timeseries.svg",
            "TCP probe-vs-pcap RTT actual values unavailable",
            [
                "pcap_tcp_rtt.csv is missing.",
                "Run create_validation_paper_figures.py locally on a machine with tshark, or run pcap_tcp_rtt_extract.py first.",
                "The validation pcaps are still usable if they were fetched into the pcaps/ directory.",
            ],
        )
        return notes or ["fig06_tcp_probe_vs_pcap_rtt_mean_timeseries.svg is a placeholder because pcap_tcp_rtt.csv is missing."]

    bin_seconds = 1
    tcp_windows = [
        row
        for row in direction_windows(windows)
        if row.get("scenario") in {"v01_candidate_signal_baseline", "v03_controlled_delay", *PARALLEL_TCP_ICMP_SCENARIOS}
        and row.get("direction") in {"dl", "ul"}
    ]
    window_by_key = {(row.get("scenario", ""), row.get("step", ""), row.get("direction", "")): row for row in tcp_windows}
    available_queries = {row.get("query_name") for row in prom}
    probe_mean_query = "direct_mean_ms_5s_by_kind" if "direct_mean_ms_5s_by_kind" in available_queries else "direct_mean_ms_5s"
    probe_median_query = next(
        (query for query in ("direct_median_1s_ms_by_kind", "direct_median_1s_ms") if query in available_queries),
        None,
    )
    probe_hist_p50_query = next(
        (query for query in ("direct_p50_ms_5s_by_kind", "direct_p50_ms_5s") if query in available_queries),
        None,
    )
    probe_extra_p50_query = probe_hist_p50_query if probe_median_query and probe_hist_p50_query else None
    probe_extra_p50_label = "probe histogram p50 RTT"
    probe_extra_p50_short_label = "probe p50"
    if probe_median_query:
        probe_p50_query = probe_median_query
        probe_p50_label = "probe exact 1s median RTT"
        probe_p50_short_label = "probe median"
    elif probe_hist_p50_query:
        probe_p50_query = probe_hist_p50_query
        probe_p50_label = "probe exported p50 RTT"
        probe_p50_short_label = "probe p50"
    else:
        probe_p50_query = "direct_p50_ms_5s"
        probe_p50_label = "probe exported p50 RTT"
        probe_p50_short_label = "probe p50"
    tcp_probe_series_label = "TCP probe median" if "median_1s" in probe_p50_query else "TCP probe p50"
    icmp_probe_series_label = "ICMP probe median" if "median_1s" in probe_p50_query else "ICMP probe p50"
    event_rate_query = "direct_event_rate_hz_5s_by_kind" if "direct_event_rate_hz_5s_by_kind" in available_queries else "direct_event_rate_hz_5s"

    pcap_capture_seconds_by_step: dict[tuple[str, str], float] = {}
    timeline_json = results_dir / "timeline_summary.json"
    if timeline_json.exists():
        try:
            timeline = json.loads(timeline_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            timeline = {}
        for event in timeline.get("events", []) if isinstance(timeline, dict) else []:
            if event.get("level") == "step" and event.get("phase") == "start":
                seconds = number(event.get("pcap_capture_seconds"))
                if seconds:
                    pcap_capture_seconds_by_step[(event.get("scenario", ""), event.get("step", ""))] = seconds
    pcap_packet_rate_by_group: dict[tuple[str, str, str, str], float] = {}
    for log_path in (results_dir / "pcaps").glob("*_tcpdump.log"):
        text = log_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"([0-9]+)\s+packets captured", text)
        if not match:
            continue
        scenario, step, direction, role = parse_pcap_name(str(log_path))
        seconds = pcap_capture_seconds_by_step.get((scenario, step), 0.0)
        if seconds <= 0:
            continue
        pcap_packet_rate_by_group[(scenario, step, direction, role)] = int(match.group(1)) / seconds

    pcap_bins: dict[tuple[str, str, str, str, int], list[float]] = defaultdict(list)
    pcap_actual_by_group: dict[tuple[str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in iter_csv(pcap_path):
        value = number(row.get("rtt_ms"))
        ts = number(row.get("timestamp"))
        payload_len = number(row.get("payload_len"), 0)
        if value is None or ts is None or payload_len is None or payload_len <= 0:
            continue
        if ue_ip and ue_ip not in {row.get("data_src"), row.get("data_dst"), row.get("ack_src"), row.get("ack_dst")}:
            continue
        scenario, step, direction, role = parse_pcap_name(row.get("pcap_file", ""))
        if (scenario, step, direction) not in window_by_key or role not in {"ran", "upf"}:
            continue
        pcap_actual_by_group[(scenario, step, direction, role)].append((ts, value))
        pcap_bins[(scenario, step, direction, role, int(ts // bin_seconds) * bin_seconds)].append(value)

    probe_bins: dict[tuple[str, str, str, str, int], list[float]] = defaultdict(list)
    probe_p50_bins: dict[tuple[str, str, str, str, int], list[float]] = defaultdict(list)
    probe_extra_p50_bins: dict[tuple[str, str, str, str, int], list[float]] = defaultdict(list)
    probe_actual_by_group: dict[tuple[str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
    event_rate_by_group: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for row in prom:
        if row.get("query_name") != probe_mean_query:
            continue
        if not is_kind(row, "tcp"):
            continue
        if ue_ip and row.get("ue_ip") != ue_ip:
            continue
        ts = number(row.get("timestamp"))
        value = number(row.get("value"))
        if ts is None or value is None:
            continue
        probe_role = row.get("probe_role") or ""
        role = probe_role_to_pcap_role(probe_role)
        if role not in {"ran", "upf"}:
            continue
        for win in tcp_windows:
            start = number(win.get("start_epoch"), -1) or -1
            end = number(win.get("end_epoch"), -2) or -2
            direction = win.get("direction", "")
            if start <= ts <= end and row.get("mode") == direction_mode(direction):
                key = (
                    win.get("scenario", ""),
                    win.get("step", ""),
                    direction,
                    role,
                    int(ts // bin_seconds) * bin_seconds,
                )
                probe_bins[key].append(value)
                break

    for row in prom:
        if row.get("query_name") != probe_p50_query:
            continue
        if not is_kind(row, "tcp"):
            continue
        if ue_ip and row.get("ue_ip") != ue_ip:
            continue
        ts = number(row.get("timestamp"))
        value = number(row.get("value"))
        if ts is None or value is None:
            continue
        probe_role = row.get("probe_role") or ""
        role = probe_role_to_pcap_role(probe_role)
        if role not in {"ran", "upf"}:
            continue
        for win in tcp_windows:
            start = number(win.get("start_epoch"), -1) or -1
            end = number(win.get("end_epoch"), -2) or -2
            direction = win.get("direction", "")
            if start <= ts <= end and row.get("mode") == direction_mode(direction):
                key = (
                    win.get("scenario", ""),
                    win.get("step", ""),
                    direction,
                    role,
                    int(ts // bin_seconds) * bin_seconds,
                )
                probe_p50_bins[key].append(value)
                break

    if probe_extra_p50_query:
        for row in prom:
            if row.get("query_name") != probe_extra_p50_query:
                continue
            if not is_kind(row, "tcp"):
                continue
            if ue_ip and row.get("ue_ip") != ue_ip:
                continue
            ts = number(row.get("timestamp"))
            value = number(row.get("value"))
            if ts is None or value is None:
                continue
            probe_role = row.get("probe_role") or ""
            role = probe_role_to_pcap_role(probe_role)
            if role not in {"ran", "upf"}:
                continue
            for win in tcp_windows:
                start = number(win.get("start_epoch"), -1) or -1
                end = number(win.get("end_epoch"), -2) or -2
                direction = win.get("direction", "")
                if start <= ts <= end and row.get("mode") == direction_mode(direction):
                    key = (
                        win.get("scenario", ""),
                        win.get("step", ""),
                        direction,
                        role,
                        int(ts // bin_seconds) * bin_seconds,
                    )
                    probe_extra_p50_bins[key].append(value)
                    break

    for row in prom:
        if row.get("query_name") != event_rate_query:
            continue
        if not is_kind(row, "tcp"):
            continue
        if ue_ip and row.get("ue_ip") != ue_ip:
            continue
        ts = number(row.get("timestamp"))
        value = number(row.get("value"))
        if ts is None or value is None:
            continue
        role = probe_role_to_pcap_role(row.get("probe_role") or "")
        if role not in {"ran", "upf"}:
            continue
        for win in tcp_windows:
            start = number(win.get("start_epoch"), -1) or -1
            end = number(win.get("end_epoch"), -2) or -2
            direction = win.get("direction", "")
            if start <= ts <= end and row.get("mode") == direction_mode(direction):
                event_rate_by_group[(win.get("scenario", ""), win.get("step", ""), direction, role)].append(value)
                break

    for row in prom:
        if row.get("query_name") != "direct_latest_ms":
            continue
        if not is_kind(row, "tcp"):
            continue
        if ue_ip and row.get("ue_ip") != ue_ip:
            continue
        ts = number(row.get("timestamp"))
        value = number(row.get("value"))
        if ts is None or value is None:
            continue
        role = probe_role_to_pcap_role(row.get("probe_role") or "")
        if role not in {"ran", "upf"}:
            continue
        for win in tcp_windows:
            start = number(win.get("start_epoch"), -1) or -1
            end = number(win.get("end_epoch"), -2) or -2
            direction = win.get("direction", "")
            if start <= ts <= end and row.get("mode") == direction_mode(direction):
                probe_actual_by_group[(win.get("scenario", ""), win.get("step", ""), direction, role)].append((ts, value))
                break

    matched_rows: list[dict[str, object]] = []
    timeseries_by_group: dict[tuple[str, str, str, str], list[dict[str, float]]] = defaultdict(list)
    for key, pcap_values in sorted(pcap_bins.items()):
        probe_values = probe_bins.get(key)
        if not probe_values:
            continue
        scenario, step, direction, role, bin_epoch = key
        pcap_mean = sum(pcap_values) / len(pcap_values)
        probe_mean = sum(probe_values) / len(probe_values)
        pcap_p50 = median(pcap_values)
        probe_p50_values = probe_p50_bins.get(key) or probe_values
        probe_p50 = median(probe_p50_values)
        probe_extra_p50_values = probe_extra_p50_bins.get(key, [])
        probe_extra_p50 = median(probe_extra_p50_values) if probe_extra_p50_values else float("nan")
        category = f"{step_label(step, direction)} {role.upper()}"
        matched_rows.append(
            {
                "scenario": scenario,
                "step": step,
                "direction": direction,
                "role": role,
                "bin_epoch": bin_epoch,
                "pcap_mean_ms": f"{pcap_mean:.6f}",
                "probe_mean_ms": f"{probe_mean:.6f}",
                "mean_diff_ms": f"{probe_mean - pcap_mean:.6f}",
                "pcap_p50_ms": f"{pcap_p50:.6f}",
                "probe_p50_ms": f"{probe_p50:.6f}",
                "p50_diff_ms": f"{probe_p50 - pcap_p50:.6f}",
                "category": category,
                "pcap_samples": len(pcap_values),
                "probe_samples": len(probe_values),
                "probe_p50_samples": len(probe_p50_values),
                "probe_p50_source": probe_p50_query,
                "probe_extra_p50_ms": f"{probe_extra_p50:.6f}" if math.isfinite(probe_extra_p50) else "",
                "probe_extra_p50_samples": len(probe_extra_p50_values),
                "probe_extra_p50_source": probe_extra_p50_query or "",
            }
        )
        timeseries_by_group[(scenario, step, direction, role)].append(
            {
                "bin_epoch": float(bin_epoch),
                "pcap_mean_ms": pcap_mean,
                "probe_mean_ms": probe_mean,
                "pcap_p50_ms": pcap_p50,
                "probe_p50_ms": probe_p50,
                "probe_extra_p50_ms": probe_extra_p50,
            }
        )

    write_csv(
        out_dir / "tcp_probe_vs_pcap_rtt_matched.csv",
        matched_rows,
        [
            "scenario",
            "step",
            "direction",
            "role",
            "bin_epoch",
            "pcap_mean_ms",
            "probe_mean_ms",
            "mean_diff_ms",
            "pcap_p50_ms",
            "probe_p50_ms",
            "p50_diff_ms",
            "category",
            "pcap_samples",
            "probe_samples",
            "probe_p50_samples",
            "probe_p50_source",
            "probe_extra_p50_ms",
            "probe_extra_p50_samples",
            "probe_extra_p50_source",
        ],
    )

    if not matched_rows:
        svg_message(
            out_dir / "fig06_tcp_probe_vs_pcap_rtt_mean_timeseries.svg",
            "TCP probe-vs-pcap RTT mean over time unavailable",
            [
                "pcap_tcp_rtt.csv exists, but no bins matched the selected UE, role, direction, and Prometheus probe windows.",
                "Check that the pcaps and Prometheus export belong to the same validation run.",
                "Also check that the UE IP in pcap_tcp_rtt.csv matches the validation UE IP.",
            ],
        )
        svg_message(
            out_dir / "fig06b_tcp_probe_vs_pcap_rtt_mean_only.svg",
            "TCP probe-vs-pcap RTT mean-only view unavailable",
            [
                "pcap_tcp_rtt.csv exists, but no bins matched the selected UE, role, direction, and Prometheus probe windows.",
                "Check that the pcaps and Prometheus export belong to the same validation run.",
                "Also check that the UE IP in pcap_tcp_rtt.csv matches the validation UE IP.",
            ],
        )
        svg_message(
            out_dir / "fig07_tcp_probe_vs_pcap_rtt_actual_timeseries.svg",
            "TCP probe-vs-pcap RTT actual values unavailable",
            [
                "pcap_tcp_rtt.csv exists, but no probe/pcap windows matched.",
                "Check that the pcaps and Prometheus export belong to the same validation run.",
                "Also check that the UE IP in pcap_tcp_rtt.csv matches the validation UE IP.",
            ],
        )
        notes.append("fig06_tcp_probe_vs_pcap_rtt_mean_timeseries.svg is a placeholder because no probe/pcap bins matched.")
        return notes

    preferred_groups: list[tuple[str, str, str, str]] = []
    preferences = [
        ("v01_candidate_signal_baseline", "baseline", "dl", "ran"),
        ("v01_candidate_signal_baseline", "baseline", "ul", "ran"),
        ("v03_controlled_delay", "no_netem", "dl", "ran"),
        ("v03_controlled_delay", "no_netem", "ul", "ran"),
        ("v03_controlled_delay", "ran_netem", "dl", "upf"),
        ("v03_controlled_delay", "upf_netem", "ul", "upf"),
    ]
    for scenario, step_part, direction, role in preferences:
        for key in sorted(timeseries_by_group):
            if key in preferred_groups:
                continue
            key_scenario, key_step, key_direction, key_role = key
            if key_scenario == scenario and step_part in key_step and key_direction == direction and key_role == role:
                preferred_groups.append(key)
                break
    if not preferred_groups:
        preferred_groups = sorted(timeseries_by_group, key=lambda key: len(timeseries_by_group[key]), reverse=True)[:6]

    latest_summary_rows: list[dict[str, object]] = []
    for key in sorted(probe_actual_by_group):
        scenario, step, direction, role = key
        values = [value for _ts, value in probe_actual_by_group[key]]
        if not values:
            continue
        stats = summarize(values)
        latest_summary_rows.append(
            {
                "scenario": scenario,
                "step": step,
                "direction": direction,
                "role": role,
                "count": int(stats["count"]),
                "mean_ms": fmt(stats["mean"]),
                "p50_ms": fmt(stats["p50"]),
                "p95_ms": fmt(stats["p95"]),
                "max_ms": fmt(stats["max"]),
            }
        )
    write_csv(
        out_dir / "tcp_probe_latest_sanity_summary.csv",
        latest_summary_rows,
        ["scenario", "step", "direction", "role", "count", "mean_ms", "p50_ms", "p95_ms", "max_ms"],
    )

    pcap_summary_rows: list[dict[str, object]] = []
    for key in sorted(pcap_actual_by_group):
        scenario, step, direction, role = key
        values = [value for _ts, value in pcap_actual_by_group[key]]
        if not values:
            continue
        stats = summarize(values)
        pcap_summary_rows.append(
            {
                "scenario": scenario,
                "step": step,
                "direction": direction,
                "role": role,
                "count": int(stats["count"]),
                "mean_ms": fmt(stats["mean"]),
                "p50_ms": fmt(stats["p50"]),
                "p95_ms": fmt(stats["p95"]),
                "max_ms": fmt(stats["max"]),
            }
        )
    write_csv(
        out_dir / "tcp_pcap_packet_sanity_summary.csv",
        pcap_summary_rows,
        ["scenario", "step", "direction", "role", "count", "mean_ms", "p50_ms", "p95_ms", "max_ms"],
    )

    panels = []
    mean_only_panels = []
    for key in preferred_groups[:6]:
        scenario, step, direction, role = key
        points = sorted(timeseries_by_group[key], key=lambda item: item["bin_epoch"])
        window = window_by_key.get((scenario, step, direction), {})
        start = number(window.get("start_epoch"), points[0]["bin_epoch"]) or points[0]["bin_epoch"]
        mean_series = [
            {
                "name": "pcap mean RTT",
                "points": [(item["bin_epoch"] - start, item["pcap_mean_ms"]) for item in points],
                "style": "linepoints",
                "color": COLORS[0],
            },
            {
                "name": "probe mean RTT",
                "points": [(item["bin_epoch"] - start, item["probe_mean_ms"]) for item in points],
                "style": "linepoints",
                "color": COLORS[1],
            },
        ]
        pcap_packet_points = downsample_points(
            [(ts - start, value) for ts, value in sorted(pcap_actual_by_group.get(key, []))],
            max_points=260,
        )
        latest_points = downsample_points(
            [(ts - start, value) for ts, value in sorted(probe_actual_by_group.get(key, []))],
            max_points=260,
        )
        mean_only_panels.append(
            {
                "title": f"{step_label(step, direction)} {role.upper()}",
                "series": mean_series,
                "bands": [],
            }
        )
        panels.append(
            {
                "title": f"{step_label(step, direction)} {role.upper()}",
                "series": [
                    *mean_series,
                    {
                        "name": "pcap packet RTT",
                        "points": pcap_packet_points,
                        "style": "points",
                        "color": COLORS[3],
                    },
                    {
                        "name": "probe latest RTT",
                        "points": latest_points,
                        "style": "linepoints",
                        "color": COLORS[2],
                    },
                ],
                "bands": [],
            }
        )

    if svg_timeseries_panels(
        out_dir / "fig06_tcp_probe_vs_pcap_rtt_mean_timeseries.svg",
        "TCP validation over time: pcap-reconstructed RTT vs probe RTT",
        "RTT (ms)",
        panels,
        panel_height=230,
    ):
        notes.append("fig06_tcp_probe_vs_pcap_rtt_mean_timeseries.svg: binned mean probe RTT and pcap-reconstructed mean TCP RTT over time, with raw pcap packet RTT and raw probe latest RTT overlaid as sanity traces.")

    if svg_timeseries_panels(
        out_dir / "fig06b_tcp_probe_vs_pcap_rtt_mean_only.svg",
        "TCP validation over time: pcap mean RTT vs probe mean RTT",
        "Mean RTT (ms)",
        mean_only_panels,
        panel_height=230,
    ):
        notes.append("fig06b_tcp_probe_vs_pcap_rtt_mean_only.svg: binned mean probe RTT and pcap-reconstructed mean TCP RTT over time without raw sanity overlays.")

    cdf_panels = []
    error_series = []
    tail_rows: list[dict[str, object]] = []
    tail_categories: list[str] = []
    tail_values: dict[tuple[str, str], float] = {}
    for key in preferred_groups[:6]:
        scenario, step, direction, role = key
        points = sorted(timeseries_by_group[key], key=lambda item: item["bin_epoch"])
        label = f"{step_label(step, direction)} {role.upper()}"
        pcap_values = [item["pcap_mean_ms"] for item in points]
        probe_values = [item["probe_mean_ms"] for item in points]
        errors = [abs(item["probe_mean_ms"] - item["pcap_mean_ms"]) for item in points]
        if pcap_values and probe_values:
            cdf_panels.append(
                {
                    "title": label,
                    "series": [
                        {"name": "pcap mean RTT", "points": cdf_points(pcap_values), "style": "line", "color": COLORS[0]},
                        {"name": "probe mean RTT", "points": cdf_points(probe_values), "style": "line", "color": COLORS[1]},
                    ],
                    "bands": [],
                }
            )
        if errors:
            error_series.append({"name": label[:30], "points": cdf_points(errors), "style": "line", "color": COLORS[len(error_series) % len(COLORS)]})
        pcap_stats = summarize(pcap_values)
        probe_stats = summarize(probe_values)
        if pcap_stats["count"] and probe_stats["count"]:
            tail_categories.append(label)
            tail_values[(label, "pcap p50")] = pcap_stats["p50"]
            tail_values[(label, probe_p50_short_label)] = probe_stats["p50"]
            tail_values[(label, "pcap p95")] = pcap_stats["p95"]
            tail_values[(label, "probe p95")] = probe_stats["p95"]
            tail_rows.append(
                {
                    "scenario": scenario,
                    "step": step,
                    "direction": direction,
                    "role": role,
                    "pcap_p50_ms": fmt(pcap_stats["p50"]),
                    "probe_p50_ms": fmt(probe_stats["p50"]),
                    "pcap_p95_ms": fmt(pcap_stats["p95"]),
                    "probe_p95_ms": fmt(probe_stats["p95"]),
                    "pcap_p99_ms": fmt(pcap_stats["p99"]),
                    "probe_p99_ms": fmt(probe_stats["p99"]),
                    "mean_abs_error_ms": fmt(sum(errors) / len(errors) if errors else float("nan")),
                    "p95_abs_error_ms": fmt(percentile(errors, 0.95) if errors else float("nan")),
                }
            )

    write_csv(
        out_dir / "tcp_probe_pcap_tail_summary.csv",
        tail_rows,
        [
            "scenario",
            "step",
            "direction",
            "role",
            "pcap_p50_ms",
            "probe_p50_ms",
            "pcap_p95_ms",
            "probe_p95_ms",
            "pcap_p99_ms",
            "probe_p99_ms",
            "mean_abs_error_ms",
            "p95_abs_error_ms",
        ],
    )
    if svg_timeseries_panels(
        out_dir / "diagnostic_tcp_probe_vs_pcap_mean_cdf.svg",
        "Diagnostic TCP correctness CDF: pcap mean RTT vs probe mean RTT",
        "CDF",
        cdf_panels,
        panel_height=230,
        xlabel="Latency (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append("diagnostic_tcp_probe_vs_pcap_mean_cdf.svg: broader diagnostic CDFs of binned pcap-reconstructed mean RTT and probe mean RTT.")

    if svg_timeseries_panels(
        out_dir / "diagnostic_tcp_probe_pcap_mean_error_cdf.svg",
        "Diagnostic TCP correctness error CDF: mean RTT",
        "CDF",
        [{"title": "Absolute error between matched 5 s means", "series": error_series, "bands": []}],
        panel_height=360,
        xlabel="Absolute error |probe - pcap| (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append("diagnostic_tcp_probe_pcap_mean_error_cdf.svg: broader diagnostic CDF of absolute error between matched probe and pcap 5 s mean RTTs.")

    if svg_grouped_bars(
        out_dir / "diagnostic_tcp_probe_pcap_mean_tail_bars.svg",
        "Diagnostic TCP correctness p50/p95 of 5 s mean RTT",
        "RTT (ms)",
        tail_categories,
        ["pcap p50", probe_p50_short_label, "pcap p95", "probe p95"],
        tail_values,
        height=700,
        value_digits=1,
    ):
        notes.append("diagnostic_tcp_probe_pcap_mean_tail_bars.svg: broader diagnostic p50 and p95 comparison between pcap reconstruction and probe means.")

    corr_window_size = 2048
    buffer_rows: list[dict[str, object]] = []
    buffer_categories: list[str] = []
    buffer_values: dict[tuple[str, str], float] = {}
    for key in preferred_groups[:6]:
        scenario, step, direction, role = key
        points = sorted(timeseries_by_group[key], key=lambda item: item["bin_epoch"])
        latencies = [item["probe_p50_ms"] for item in points if math.isfinite(item["probe_p50_ms"])]
        rates = event_rate_by_group.get(key, [])
        if not latencies:
            continue
        p50_latency = median(latencies)
        p95_latency = percentile(latencies, 0.95)
        p95_rate = percentile(rates, 0.95) if rates else float("nan")
        pcap_pps = pcap_packet_rate_by_group.get(key, float("nan"))
        safe_pps_at_p50 = corr_window_size / (p50_latency / 1000.0) if p50_latency > 0 else float("inf")
        safe_pps_at_p95 = corr_window_size / (p95_latency / 1000.0) if p95_latency > 0 else float("inf")
        margin = safe_pps_at_p95 / p95_rate if p95_rate and math.isfinite(p95_rate) and p95_rate > 0 else float("nan")
        pcap_margin = safe_pps_at_p95 / pcap_pps if math.isfinite(pcap_pps) and pcap_pps > 0 else float("nan")
        label = f"{step_label(step, direction)} {role.upper()}"
        buffer_rows.append(
            {
                "scenario": scenario,
                "step": step,
                "direction": direction,
                "role": role,
                "correlation_window_packets": corr_window_size,
                "probe_p50_latency_ms": fmt(p50_latency),
                "probe_p95_latency_ms": fmt(p95_latency),
                "safe_packet_rate_at_p50_latency_pps": fmt(safe_pps_at_p50, 1),
                "safe_packet_rate_at_p95_latency_pps": fmt(safe_pps_at_p95, 1),
                "observed_pcap_capture_packet_rate_pps": fmt(pcap_pps, 1),
                "observed_p95_latency_event_rate_hz": fmt(p95_rate, 1),
                "safe_p95_rate_to_observed_p95_event_rate_ratio": fmt(margin, 2),
                "safe_p95_rate_to_observed_pcap_packet_rate_ratio": fmt(pcap_margin, 2),
                "formula": "packet_rate_pps * latency_seconds < correlation_window_packets",
            }
        )
        observed_rate = pcap_pps if math.isfinite(pcap_pps) and pcap_pps > 0 else p95_rate
        observed_label = "observed pcap pps" if math.isfinite(pcap_pps) and pcap_pps > 0 else "observed p95 event/s"
        if math.isfinite(safe_pps_at_p95) and math.isfinite(observed_rate) and observed_rate > 0:
            buffer_categories.append(label)
            buffer_values[(label, observed_label)] = observed_rate
            buffer_values[(label, "buffer budget pps")] = safe_pps_at_p95
    write_csv(
        out_dir / "tcp_correlation_buffer_budget_summary.csv",
        buffer_rows,
        [
            "scenario",
            "step",
            "direction",
            "role",
            "correlation_window_packets",
            "probe_p50_latency_ms",
            "probe_p95_latency_ms",
            "safe_packet_rate_at_p50_latency_pps",
            "safe_packet_rate_at_p95_latency_pps",
            "observed_pcap_capture_packet_rate_pps",
            "observed_p95_latency_event_rate_hz",
            "safe_p95_rate_to_observed_p95_event_rate_ratio",
            "safe_p95_rate_to_observed_pcap_packet_rate_ratio",
            "formula",
        ],
    )
    if buffer_categories and svg_grouped_bars(
        out_dir / "fig23_tcp_correlation_buffer_budget.svg",
        "TCP correlation-window budget: packet_rate x latency < buffer",
        "Rate (pps or latency events/s)",
        buffer_categories,
        ["observed pcap pps", "observed p95 event/s", "buffer budget pps"],
        buffer_values,
        log_scale=True,
        height=720,
        value_digits=0,
    ):
        notes.append("fig23_tcp_correlation_buffer_budget.svg: checks the correlation-window rule packet_rate_pps x latency_seconds < 2048 against observed pcap packet rates when tcpdump counts are available.")

    clean_values: dict[str, dict[str, list[float]]] = {
        "dl": {"pcap": [], "probe": [], "error": []},
        "ul": {"pcap": [], "probe": [], "error": []},
    }
    clean_median_values: dict[str, dict[str, list[float]]] = {
        "dl": {"pcap_median": [], "probe_median": [], "probe_hist_p50": [], "error": []},
        "ul": {"pcap_median": [], "probe_median": [], "probe_hist_p50": [], "error": []},
    }
    clean_median_pairs: dict[str, list[dict[str, object]]] = {"dl": [], "ul": []}
    clean_raw_values: dict[str, dict[str, list[float]]] = {
        "dl": {"pcap_packet": [], "probe_latest": []},
        "ul": {"pcap_packet": [], "probe_latest": []},
    }
    clean_tail_categories: list[str] = []
    clean_tail_values: dict[tuple[str, str], float] = {}
    clean_tail_rows: list[dict[str, object]] = []
    for key in sorted(timeseries_by_group):
        scenario, step, direction, role = key
        if direction not in clean_values or role != "ran":
            continue
        if scenario not in {"v01_candidate_signal_baseline", "v03_controlled_delay"}:
            continue
        if "baseline" not in step and "no_netem" not in step:
            continue
        points = sorted(timeseries_by_group[key], key=lambda item: item["bin_epoch"])
        pcap_values = [item["pcap_mean_ms"] for item in points]
        probe_values = [item["probe_mean_ms"] for item in points]
        errors = [abs(item["probe_mean_ms"] - item["pcap_mean_ms"]) for item in points]
        pcap_medians = [item["pcap_p50_ms"] for item in points]
        probe_medians = [item["probe_p50_ms"] for item in points]
        probe_extra_p50s = [
            item["probe_extra_p50_ms"]
            for item in points
            if math.isfinite(item.get("probe_extra_p50_ms", float("nan")))
        ]
        median_errors = [abs(item["probe_p50_ms"] - item["pcap_p50_ms"]) for item in points]
        clean_values[direction]["pcap"].extend(pcap_values)
        clean_values[direction]["probe"].extend(probe_values)
        clean_values[direction]["error"].extend(errors)
        clean_median_values[direction]["pcap_median"].extend(pcap_medians)
        clean_median_values[direction]["probe_median"].extend(probe_medians)
        clean_median_values[direction]["probe_hist_p50"].extend(probe_extra_p50s)
        clean_median_values[direction]["error"].extend(median_errors)
        clean_raw_values[direction]["pcap_packet"].extend(value for _ts, value in pcap_actual_by_group.get(key, []))
        clean_raw_values[direction]["probe_latest"].extend(value for _ts, value in probe_actual_by_group.get(key, []))

        label = "Baseline" if "baseline" in step else "Reference"
        for item in points:
            pcap_median = item["pcap_p50_ms"]
            probe_median = item["probe_p50_ms"]
            probe_extra_p50 = item.get("probe_extra_p50_ms", float("nan"))
            if not (math.isfinite(pcap_median) and math.isfinite(probe_median)):
                continue
            delta = probe_median - pcap_median
            clean_median_pairs[direction].append(
                {
                    "scenario": scenario,
                    "step": step,
                    "direction": direction,
                    "role": role,
                    "bin_epoch": int(item["bin_epoch"]),
                    "category": label,
                    "pcap_1s_median_ms": pcap_median,
                    "probe_exported_p50_ms": probe_median,
                    "delta_probe_minus_pcap_ms": delta,
                    "abs_error_ms": abs(delta),
                    "probe_export_source": probe_p50_query,
                    "probe_hist_p50_ms": probe_extra_p50 if math.isfinite(probe_extra_p50) else "",
                    "probe_hist_p50_source": probe_extra_p50_query or "",
                }
            )

        label = f"{label} {direction.upper()}"
        pcap_stats = summarize(pcap_values)
        probe_stats = summarize(probe_values)
        clean_tail_categories.append(label)
        clean_tail_values[(label, "pcap mean p50")] = pcap_stats["p50"]
        clean_tail_values[(label, "probe mean p50")] = probe_stats["p50"]
        clean_tail_values[(label, "pcap mean p95")] = pcap_stats["p95"]
        clean_tail_values[(label, "probe mean p95")] = probe_stats["p95"]
        clean_tail_rows.append(
            {
                "scenario": scenario,
                "step": step,
                "direction": direction,
                "pcap_p50_ms": fmt(pcap_stats["p50"]),
                "probe_p50_ms": fmt(probe_stats["p50"]),
                "pcap_p95_ms": fmt(pcap_stats["p95"]),
                "probe_p95_ms": fmt(probe_stats["p95"]),
                "mean_abs_error_ms": fmt(sum(errors) / len(errors) if errors else float("nan")),
                "p95_abs_error_ms": fmt(percentile(errors, 0.95) if errors else float("nan")),
            }
        )

    write_csv(
        out_dir / "tcp_clean_correctness_summary.csv",
        clean_tail_rows,
        ["scenario", "step", "direction", "pcap_p50_ms", "probe_p50_ms", "pcap_p95_ms", "probe_p95_ms", "mean_abs_error_ms", "p95_abs_error_ms"],
    )

    clean_cdf_panels = []
    for direction, title in (("dl", "Downlink"), ("ul", "Uplink")):
        pcap_values = clean_values[direction]["pcap"]
        probe_values = clean_values[direction]["probe"]
        if pcap_values and probe_values:
            clean_cdf_panels.append(
                {
                    "title": f"{title} mean RTT correctness windows",
                    "series": [
                        {"name": "pcap mean RTT", "points": cdf_points(pcap_values), "style": "line", "color": COLORS[0]},
                        {"name": "probe mean RTT", "points": cdf_points(probe_values), "style": "line", "color": COLORS[1]},
                    ],
                    "bands": [],
                }
            )
    clean_cdf_title = "TCP correctness CDF: clean mean RTT validation windows"
    for figure_name in ("fig11_tcp_probe_vs_pcap_cdf.svg", "fig14_tcp_correctness_clean_cdf.svg"):
        if svg_timeseries_panels(
            out_dir / figure_name,
            clean_cdf_title,
            "CDF",
            clean_cdf_panels,
            panel_height=300,
            xlabel="Mean RTT per 5 s bin (ms)",
            y_range=(0.0, 1.0),
        ):
            notes.append(f"{figure_name}: cleaner mean-based CDF of probe-vs-pcap RTT for baseline/reference windows only, split by direction.")

    clean_error_series = []
    for direction, title in (("dl", "Downlink"), ("ul", "Uplink")):
        errors = clean_values[direction]["error"]
        if errors:
            clean_error_series.append({"name": title, "points": cdf_points(errors), "style": "line", "color": COLORS[len(clean_error_series)]})
    if svg_timeseries_panels(
        out_dir / "fig12_tcp_probe_pcap_error_cdf.svg",
        "TCP correctness error CDF: clean mean RTT validation windows",
        "CDF",
        [{"title": "Absolute error between matched 5 s mean RTT values", "series": clean_error_series, "bands": []}],
        panel_height=360,
        xlabel="Absolute error |probe mean - pcap mean| (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append("fig12_tcp_probe_pcap_error_cdf.svg: cleaner mean-based CDF of absolute probe-vs-pcap error for baseline/reference windows only.")

    if svg_timeseries_panels(
        out_dir / "fig15_tcp_correctness_clean_error_cdf.svg",
        "TCP correctness error CDF: clean mean RTT validation windows",
        "CDF",
        [{"title": "Absolute error between matched 5 s mean RTT values", "series": clean_error_series, "bands": []}],
        panel_height=360,
        xlabel="Absolute error |probe mean - pcap mean| (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append("fig15_tcp_correctness_clean_error_cdf.svg: cleaner mean-based CDF of absolute probe-vs-pcap error for baseline/reference windows only.")

    clean_tail_title = "TCP correctness p50/p95 of 5 s mean RTT: clean validation windows"
    for figure_name in ("fig13_tcp_probe_pcap_tail_bars.svg", "fig16_tcp_correctness_clean_tail_bars.svg"):
        if svg_grouped_bars(
            out_dir / figure_name,
            clean_tail_title,
            "RTT (ms)",
            clean_tail_categories,
            ["pcap mean p50", "probe mean p50", "pcap mean p95", "probe mean p95"],
            clean_tail_values,
            height=620,
            value_digits=1,
        ):
            notes.append(f"{figure_name}: compact mean-based p50/p95 comparison for baseline/reference TCP correctness windows.")

    combined_cdf_rows: list[dict[str, object]] = []
    combined_cdf_panels = []
    median_error_series = []
    for direction, title in (("dl", "Downlink"), ("ul", "Uplink")):
        raw = clean_raw_values[direction]
        med = clean_median_values[direction]
        series = []
        for source, label, color in (
            ("pcap_packet", "pcap packet RTT", COLORS[3]),
            ("probe_latest", "probe latest RTT", COLORS[2]),
        ):
            values = raw[source]
            stats = summarize(values)
            if values:
                series.append({"name": label, "points": cdf_points(values, max_points=1200), "style": "line", "color": color})
            combined_cdf_rows.append(
                {
                    "direction": direction,
                    "source": source,
                    "count": int(stats["count"]),
                    "p50_ms": fmt(stats["p50"]),
                    "p95_ms": fmt(stats["p95"]),
                    "p99_ms": fmt(stats["p99"]),
                    "max_ms": fmt(stats["max"]),
                }
            )
        median_sources = [
            ("pcap_median", "pcap 1s median RTT", COLORS[0]),
            ("probe_median", probe_p50_label, COLORS[1]),
        ]
        for source, label, color in median_sources:
            values = med[source]
            stats = summarize(values)
            if values:
                series.append({"name": label, "points": cdf_points(values, max_points=1200), "style": "line", "color": color})
            combined_cdf_rows.append(
                {
                    "direction": direction,
                    "source": source,
                    "count": int(stats["count"]),
                    "p50_ms": fmt(stats["p50"]),
                    "p95_ms": fmt(stats["p95"]),
                    "p99_ms": fmt(stats["p99"]),
                    "max_ms": fmt(stats["max"]),
                }
            )
        if series:
            combined_cdf_panels.append({"title": f"{title} clean correctness windows", "series": series, "bands": []})
        if med["error"]:
            median_error_series.append({"name": title, "points": cdf_points(med["error"], max_points=1200), "style": "line", "color": COLORS[len(median_error_series)]})
    write_csv(
        out_dir / "tcp_correctness_combined_cdf_summary.csv",
        combined_cdf_rows,
        ["direction", "source", "count", "p50_ms", "p95_ms", "p99_ms", "max_ms"],
    )
    if svg_timeseries_panels(
        out_dir / "fig20_tcp_correctness_combined_cdf.svg",
        "TCP correctness CDF: pcap packets, pcap medians, and probe medians",
        "CDF",
        combined_cdf_panels,
        panel_height=330,
        xlabel="RTT (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append("fig20_tcp_correctness_combined_cdf.svg: combined clean-window CDF with raw pcap packets, raw probe latest values, 1s pcap medians, and exported probe median values. Histogram-p50 probe series are intentionally omitted from this figure.")
    if svg_timeseries_panels(
        out_dir / "fig21_tcp_correctness_median_error_cdf.svg",
        "TCP correctness median error CDF",
        "CDF",
        [{"title": f"Absolute error between 1s pcap median and {probe_p50_label}", "series": median_error_series, "bands": []}],
        panel_height=360,
        xlabel=f"Absolute error |{probe_p50_short_label} - pcap median| (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append("fig21_tcp_correctness_median_error_cdf.svg: CDF of absolute error between 1s pcap median RTT and exported probe median/p50 RTT.")

    paired_rows = [row for direction in ("dl", "ul") for row in clean_median_pairs[direction]]
    write_csv(
        out_dir / "tcp_correctness_median_paired_points.csv",
        paired_rows,
        [
            "scenario",
            "step",
            "direction",
            "role",
            "bin_epoch",
            "category",
            "pcap_1s_median_ms",
            "probe_exported_p50_ms",
            "delta_probe_minus_pcap_ms",
            "abs_error_ms",
            "probe_export_source",
            "probe_hist_p50_ms",
            "probe_hist_p50_source",
        ],
    )

    similarity_rows: list[dict[str, object]] = []
    scatter_panels: list[dict[str, object]] = []
    for direction, title in (("dl", "Downlink"), ("ul", "Uplink")):
        points = clean_median_pairs[direction]
        xs = [float(row["pcap_1s_median_ms"]) for row in points]
        ys = [float(row["probe_exported_p50_ms"]) for row in points]
        deltas = [y - x for x, y in zip(xs, ys)]
        errors = [abs(delta) for delta in deltas]
        if not xs or not ys:
            continue
        mae = mean(errors)
        rmse = math.sqrt(mean([error * error for error in errors]))
        pearson = pearson_corr(xs, ys)
        spearman = spearman_corr(xs, ys)
        ks = ks_distance(xs, ys)
        wasserstein = wasserstein_1d_ms(xs, ys)
        within_0_1 = 100.0 * sum(1 for error in errors if error <= 0.1) / len(errors)
        within_1 = 100.0 * sum(1 for error in errors if error <= 1.0) / len(errors)
        within_5 = 100.0 * sum(1 for error in errors if error <= 5.0) / len(errors)
        similarity_rows.append(
            {
                "direction": direction,
                "count": len(xs),
                "pcap_median_of_1s_medians_ms": fmt(median(xs)),
                "probe_median_of_exported_p50_ms": fmt(median(ys)),
                "median_bias_probe_minus_pcap_ms": fmt(median(deltas)),
                "mean_bias_probe_minus_pcap_ms": fmt(mean(deltas)),
                "mean_abs_error_ms": fmt(mae),
                "p95_abs_error_ms": fmt(percentile(errors, 0.95)),
                "rmse_ms": fmt(rmse),
                "pearson_r": fmt(pearson, 4),
                "spearman_r": fmt(spearman, 4),
                "ks_distance": fmt(ks, 4),
                "wasserstein_ms": fmt(wasserstein),
                "within_0_1ms_pct": fmt(within_0_1, 1),
                "within_1ms_pct": fmt(within_1, 1),
                "within_5ms_pct": fmt(within_5, 1),
                "probe_export_source": probe_p50_query,
            }
        )
        run_summary_lines = (
            [
                "For this run, downlink has:",
                f"pcap median RTT around {median(xs):.3f} ms;",
                f"probe median around {median(ys):.3f} ms;",
                f"median bias {median(deltas):.3f} ms; within 5 ms={within_5:.1f}%",
            ]
            if direction == "dl"
            else [
                f"Median pcap={median(xs):.3f} ms; probe={median(ys):.3f} ms",
                f"Median bias={median(deltas):.3f} ms; within 5 ms={within_5:.1f}%",
            ]
        )
        scatter_panels.append(
            {
                "title": f"{title}: pcap 1s median vs {probe_p50_label}",
                "points": [
                    {
                        "x": row["pcap_1s_median_ms"],
                        "y": row["probe_exported_p50_ms"],
                        "category": row["category"],
                    }
                    for row in points
                ],
                "annotation": [
                    f"n={len(xs)}  MAE={mae:.3f} ms",
                    f"bias={mean(deltas):.3f} ms  RMSE={rmse:.3f} ms",
                    f"Pearson={pearson:.3f}  Spearman={spearman:.3f}",
                    f"KS={ks:.3f}  W1={wasserstein:.3f} ms",
                    *run_summary_lines,
                ],
            }
        )
    write_csv(
        out_dir / "tcp_correctness_median_similarity_summary.csv",
        similarity_rows,
        [
            "direction",
            "count",
            "pcap_median_of_1s_medians_ms",
            "probe_median_of_exported_p50_ms",
            "median_bias_probe_minus_pcap_ms",
            "mean_bias_probe_minus_pcap_ms",
            "mean_abs_error_ms",
            "p95_abs_error_ms",
            "rmse_ms",
            "pearson_r",
            "spearman_r",
            "ks_distance",
            "wasserstein_ms",
            "within_0_1ms_pct",
            "within_1ms_pct",
            "within_5ms_pct",
            "probe_export_source",
        ],
    )
    if svg_scatter_panels(
        out_dir / "fig24_tcp_correctness_median_scatter.svg",
        "TCP correctness scatter: pcap median vs probe median/p50",
        "pcap 1s median RTT (ms)",
        f"{probe_p50_label} (ms)",
        scatter_panels,
    ):
        notes.append("fig24_tcp_correctness_median_scatter.svg: paired pcap 1s median vs exported probe median/p50 with y=x reference and similarity statistics.")

    actual_panels = []
    actual_rows: list[dict[str, object]] = []
    for key in preferred_groups[:6]:
        scenario, step, direction, role = key
        window = window_by_key.get((scenario, step, direction), {})
        pcap_points = sorted(pcap_actual_by_group.get(key, []))
        probe_points = sorted(probe_actual_by_group.get(key, []))
        if not pcap_points and not probe_points:
            continue
        pairs = nearest_actual_pairs(pcap_points, probe_points)
        if not pairs:
            continue
        start = number(window.get("start_epoch"), pairs[0]["timestamp"])
        if start is None:
            start = pairs[0]["timestamp"]
        selected_pcap = [(item["timestamp"] - start, item["pcap_rtt_ms"]) for item in pairs]
        selected_probe = [(item["timestamp"] - start, item["probe_rtt_ms"]) for item in pairs]
        for item in pairs:
            seconds = item["timestamp"] - start
            actual_rows.append(
                {
                    "scenario": scenario,
                    "step": step,
                    "direction": direction,
                    "role": role,
                    "timestamp": f"{item['timestamp']:.6f}",
                    "seconds": f"{seconds:.6f}",
                    "pcap_timestamp": f"{item['pcap_timestamp']:.6f}",
                    "pcap_rtt_ms": f"{item['pcap_rtt_ms']:.6f}",
                    "probe_rtt_ms": f"{item['probe_rtt_ms']:.6f}",
                    "diff_ms": f"{item['probe_rtt_ms'] - item['pcap_rtt_ms']:.6f}",
                    "time_delta_ms": f"{item['time_delta_ms']:.3f}",
                }
            )
        actual_panels.append(
            {
                "title": f"{step_label(step, direction)} {role.upper()}",
                "series": [
                    {
                        "name": "pcap packet RTT",
                        "points": selected_pcap,
                        "style": "points",
                        "color": COLORS[0],
                    },
                    {
                        "name": "probe latest RTT",
                        "points": selected_probe,
                        "style": "linepoints",
                        "color": COLORS[1],
                    },
                ],
                "bands": [],
            }
        )

    write_csv(
        out_dir / "tcp_probe_vs_pcap_rtt_actual_timeseries.csv",
        actual_rows,
        [
            "scenario",
            "step",
            "direction",
            "role",
            "timestamp",
            "seconds",
            "pcap_timestamp",
            "pcap_rtt_ms",
            "probe_rtt_ms",
            "diff_ms",
            "time_delta_ms",
        ],
    )
    if svg_timeseries_panels(
        out_dir / "fig07_tcp_probe_vs_pcap_rtt_actual_timeseries.svg",
        "TCP validation over time: time-aligned actual probe and pcap RTT samples",
        "RTT (ms)",
        actual_panels,
        panel_height=230,
    ):
        notes.append("fig07_tcp_probe_vs_pcap_rtt_actual_timeseries.svg: actual probe latest RTT samples overlaid with nearest-in-time pcap-reconstructed packet RTT samples.")

    ping_rows = read_csv(results_dir / "ping_rtt.csv")
    parallel_rows: list[dict[str, object]] = []
    parallel_categories: list[str] = []
    parallel_values: dict[tuple[str, str], float] = {}
    parallel_cdf_rows: list[dict[str, object]] = []
    parallel_cdf_panels: list[dict[str, object]] = []
    parallel_windows = [
        win
        for win in direction_windows(windows)
        if win.get("scenario") in PARALLEL_TCP_ICMP_SCENARIOS
    ]
    for win in parallel_windows:
        direction = win.get("direction", "")
        if direction != "dl":
            continue
        scenario = win.get("scenario", "")
        step = win.get("step", "")
        ping_payload_bytes, ping_ipv4_packet_bytes, ping_label = ping_size_labels(win)
        start = number(win.get("start_epoch"), 0) or 0
        end = number(win.get("end_epoch"), start) or start
        tcp_key = (scenario, step, direction, "ran")
        tcp_points = timeseries_by_group.get(tcp_key, [])
        tcp_probe_medians = [item["probe_p50_ms"] for item in tcp_points]
        tcp_probe_hist_p50s = [
            item["probe_extra_p50_ms"]
            for item in tcp_points
            if math.isfinite(item.get("probe_extra_p50_ms", float("nan")))
        ]
        tcp_pcap_medians = [item["pcap_p50_ms"] for item in tcp_points]
        icmp_mode = direction_mode(direction)
        ping_values = []
        ping_abs_times = []
        for row in ping_rows:
            if row.get("scenario") != scenario or row.get("step") != step or row.get("direction") != direction:
                continue
            ts = number(row.get("epoch"))
            value = number(row.get("rtt_ms"))
            if value is not None:
                ping_values.append(value)
                if ts is not None:
                    ping_abs_times.append(ts)
        ping_timeout = number(win.get("ping_timeout"), 2) or 2
        icmp_probe_start = min(ping_abs_times) if ping_abs_times else start
        icmp_probe_end = (max(ping_abs_times) + ping_timeout + 0.5) if ping_abs_times else end
        icmp_probe_values = []
        icmp_probe_source = probe_p50_query
        for row in prom:
            if row.get("query_name") != probe_p50_query:
                continue
            if not is_kind(row, "icmp"):
                continue
            if ue_ip and row.get("ue_ip") != ue_ip:
                continue
            ts = number(row.get("timestamp"))
            value = number(row.get("value"))
            if ts is None or value is None or ts < icmp_probe_start or ts > icmp_probe_end:
                continue
            if row.get("probe_role") != "gnb" or row.get("mode") != icmp_mode:
                continue
            icmp_probe_values.append(value)
        if not icmp_probe_values:
            icmp_probe_source = "direct_latest_ms"
            for row in prom:
                if row.get("query_name") != "direct_latest_ms":
                    continue
                if not is_kind(row, "icmp"):
                    continue
                if ue_ip and row.get("ue_ip") != ue_ip:
                    continue
                ts = number(row.get("timestamp"))
                value = number(row.get("value"))
                if ts is None or value is None or ts < icmp_probe_start or ts > icmp_probe_end:
                    continue
                if row.get("probe_role") != "gnb" or row.get("mode") != icmp_mode:
                    continue
                icmp_probe_values.append(value)
        icmp_probe_hist_p50_values = []
        if probe_extra_p50_query:
            for row in prom:
                if row.get("query_name") != probe_extra_p50_query:
                    continue
                if not is_kind(row, "icmp"):
                    continue
                if ue_ip and row.get("ue_ip") != ue_ip:
                    continue
                ts = number(row.get("timestamp"))
                value = number(row.get("value"))
                if ts is None or value is None or ts < icmp_probe_start or ts > icmp_probe_end:
                    continue
                if row.get("probe_role") != "gnb" or row.get("mode") != icmp_mode:
                    continue
                icmp_probe_hist_p50_values.append(value)
        if not (tcp_probe_medians or tcp_probe_hist_p50s or tcp_pcap_medians or ping_values or icmp_probe_values or icmp_probe_hist_p50_values):
            continue
        tcp_probe_p50 = median(tcp_probe_medians) if tcp_probe_medians else float("nan")
        tcp_probe_hist_p50 = median(tcp_probe_hist_p50s) if tcp_probe_hist_p50s else float("nan")
        tcp_pcap_p50 = median(tcp_pcap_medians) if tcp_pcap_medians else float("nan")
        ping_p50 = median(ping_values) if ping_values else float("nan")
        icmp_probe_p50 = median(icmp_probe_values) if icmp_probe_values else float("nan")
        icmp_probe_hist_p50 = median(icmp_probe_hist_p50_values) if icmp_probe_hist_p50_values else float("nan")
        label = f"{step_label(step, direction)}"
        parallel_categories.append(label)
        bar_series = [
            (tcp_probe_series_label, tcp_probe_p50),
            (icmp_probe_series_label, icmp_probe_p50),
            ("TCP pcap median", tcp_pcap_p50),
            ("Ping p50", ping_p50),
        ]
        if probe_extra_p50_query:
            bar_series.insert(1, ("TCP probe histogram p50", tcp_probe_hist_p50))
            bar_series.insert(3, ("ICMP probe histogram p50", icmp_probe_hist_p50))
        for series_name, value in bar_series:
            if math.isfinite(value):
                parallel_values[(label, series_name)] = value
        parallel_rows.append(
            {
                "scenario": scenario,
                "step": step,
                "direction": direction,
                "ping_payload_bytes": ping_payload_bytes,
                "ping_ipv4_packet_bytes": ping_ipv4_packet_bytes,
                "tcp_probe_p50_median_ms": fmt(tcp_probe_p50),
                "tcp_probe_hist_p50_median_ms": fmt(tcp_probe_hist_p50),
                "tcp_pcap_p50_median_ms": fmt(tcp_pcap_p50),
                "ping_p50_ms": fmt(ping_p50),
                "icmp_probe_p50_ms": fmt(icmp_probe_p50),
                "icmp_probe_hist_p50_ms": fmt(icmp_probe_hist_p50),
                "delta_tcp_probe_minus_ping_ms": fmt(tcp_probe_p50 - ping_p50 if math.isfinite(tcp_probe_p50) and math.isfinite(ping_p50) else float("nan")),
                "delta_tcp_pcap_minus_ping_ms": fmt(tcp_pcap_p50 - ping_p50 if math.isfinite(tcp_pcap_p50) and math.isfinite(ping_p50) else float("nan")),
                "delta_tcp_probe_minus_icmp_probe_ms": fmt(tcp_probe_p50 - icmp_probe_p50 if math.isfinite(tcp_probe_p50) and math.isfinite(icmp_probe_p50) else float("nan")),
                "tcp_probe_samples": len(tcp_probe_medians),
                "tcp_probe_hist_p50_samples": len(tcp_probe_hist_p50s),
                "tcp_pcap_bins": len(tcp_pcap_medians),
                "ping_samples": len(ping_values),
                "icmp_probe_samples": len(icmp_probe_values),
                "icmp_probe_hist_p50_samples": len(icmp_probe_hist_p50_values),
                "tcp_probe_source": probe_p50_query,
                "tcp_probe_hist_p50_source": probe_extra_p50_query or "",
                "icmp_probe_source": icmp_probe_source,
                "icmp_probe_hist_p50_source": probe_extra_p50_query or "",
            }
        )
        cdf_series = []
        cdf_source_rows = [
            ("tcp_probe_p50", tcp_probe_series_label, tcp_probe_medians, COLORS[1]),
            ("icmp_probe_p50", icmp_probe_series_label, icmp_probe_values, COLORS[2]),
            ("tcp_pcap_1s_median", "TCP pcap 1s median", tcp_pcap_medians, COLORS[0]),
            ("ping_rtt", "Ping RTT", ping_values, COLORS[3]),
        ]
        for source, display, values, color in cdf_source_rows:
            stats = summarize(values)
            parallel_cdf_rows.append(
                {
                    "scenario": scenario,
                    "step": step,
                    "direction": direction,
                    "ping_payload_bytes": ping_payload_bytes,
                    "ping_ipv4_packet_bytes": ping_ipv4_packet_bytes,
                    "source": source,
                    "display": display,
                    "count": int(stats["count"]),
                    "p50_ms": fmt(stats["p50"]),
                    "p95_ms": fmt(stats["p95"]),
                    "p99_ms": fmt(stats["p99"]),
                    "max_ms": fmt(stats["max"]),
                    "tcp_probe_source": probe_p50_query if source == "tcp_probe_p50" else (probe_extra_p50_query if source == "tcp_probe_hist_p50" else ""),
                    "icmp_probe_source": icmp_probe_source if source == "icmp_probe_p50" else (probe_extra_p50_query if source == "icmp_probe_hist_p50" else ""),
                }
            )
            if values:
                cdf_series.append(
                    {
                        "name": display,
                        "points": cdf_points(values, max_points=1200),
                        "style": "line",
                        "color": color,
                    }
                )
        if cdf_series:
            parallel_cdf_panels.append(
                {
                    "title": f"{step_label(step, direction)} parallel TCP+ICMP ({ping_label})",
                    "series": cdf_series,
                    "bands": [],
                }
            )
    write_csv(
        out_dir / "tcp_icmp_parallel_median_delta_summary.csv",
        parallel_rows,
        [
            "scenario",
            "step",
            "direction",
            "ping_payload_bytes",
            "ping_ipv4_packet_bytes",
            "tcp_probe_p50_median_ms",
            "tcp_probe_hist_p50_median_ms",
            "tcp_pcap_p50_median_ms",
            "ping_p50_ms",
            "icmp_probe_p50_ms",
            "icmp_probe_hist_p50_ms",
            "delta_tcp_probe_minus_ping_ms",
            "delta_tcp_pcap_minus_ping_ms",
            "delta_tcp_probe_minus_icmp_probe_ms",
            "tcp_probe_samples",
            "tcp_probe_hist_p50_samples",
            "tcp_pcap_bins",
            "ping_samples",
            "icmp_probe_samples",
            "icmp_probe_hist_p50_samples",
            "tcp_probe_source",
            "tcp_probe_hist_p50_source",
            "icmp_probe_source",
            "icmp_probe_hist_p50_source",
        ],
    )
    parallel_bar_series = [tcp_probe_series_label, "TCP pcap median", "Ping p50", icmp_probe_series_label]
    if probe_extra_p50_query:
        parallel_bar_series = [
            tcp_probe_series_label,
            "TCP probe histogram p50",
            "TCP pcap median",
            "Ping p50",
            icmp_probe_series_label,
            "ICMP probe histogram p50",
        ]
    if parallel_rows and svg_grouped_bars(
        out_dir / "fig22_tcp_icmp_parallel_median_delta.svg",
        "Parallel TCP+ICMP validation: median latency comparison",
        "Median latency (ms)",
        parallel_categories,
        parallel_bar_series,
        parallel_values,
        height=560,
        value_digits=2,
    ):
        extra_text = " The histogram p50 probe series is included too." if probe_extra_p50_query else ""
        notes.append(f"fig22_tcp_icmp_parallel_median_delta.svg: median TCP probe, pcap TCP, ping, and ICMP probe comparison for the parallel TCP+ICMP validation scenario(s).{extra_text}")
    write_csv(
        out_dir / "tcp_icmp_parallel_cdf_summary.csv",
        parallel_cdf_rows,
        [
            "scenario",
            "step",
            "direction",
            "ping_payload_bytes",
            "ping_ipv4_packet_bytes",
            "source",
            "display",
            "count",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "max_ms",
            "tcp_probe_source",
            "icmp_probe_source",
        ],
    )
    if parallel_cdf_panels and svg_timeseries_panels(
        out_dir / "fig25_tcp_icmp_parallel_cdf.svg",
        "Parallel TCP+ICMP validation CDF",
        "CDF",
        parallel_cdf_panels,
        panel_height=330,
        xlabel="Latency / RTT (ms)",
        y_range=(0.0, 1.0),
    ):
        notes.append(f"fig25_tcp_icmp_parallel_cdf.svg: CDFs from the parallel TCP+ICMP validation window(s), showing {tcp_probe_series_label}, TCP pcap 1s median, ping RTT, and {icmp_probe_series_label}. MTU-sized ping windows are labeled by ICMP payload and approximate IPv4 packet size. Histogram-p50 probe series are intentionally omitted from this figure.")
    return notes


def write_readme(out_dir: Path, notes: list[str], ue_ip: str) -> None:
    lines = [
        "# Validation Evidence Figures",
        "",
        "This folder contains paper-oriented validation figures. These are proof figures, not exploratory scenario plots.",
        "",
        f"Primary validation UE IP used for UE-specific comparisons: `{ue_ip or 'auto-detect failed'}`.",
        "",
        "## Figures",
        "",
    ]
    for note in notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## CSV Artifacts",
            "",
            "- `resource_container_usage_summary.csv`: CPU and memory summaries used for fig01 and fig02.",
            "- `bpf_baseline_vs_probe_summary.csv`: weighted bpftool `run_time_ns / run_cnt` comparison.",
            "- `bpf_packet_rate_budget_summary.csv`: packet-rate thresholds where the measured eBPF cost consumes a chosen CPU-core fraction.",
            "- `netem_detection_probe_summary.csv`: per-window probe latency summaries for the controlled-delay test.",
            "- `controlled_delay_pickup_summary.csv`: selected component-local reference/expected/observed p50 values used for fig04.",
            "- `controlled_delay_cross_boundary_summary.csv`: selected cross-boundary reference/expected/observed p50 values used for fig04b.",
            "- `controlled_delay_boxplot_summary.csv`: distribution summaries used for fig10.",
            "- `controlled_delay_increase_boxplot_summary.csv`: latency-increase distributions used for fig17.",
            "- `icmp_ping_vs_probe_timeseries.csv`: ping output and probe ICMP points used for fig05.",
            "- `tcp_probe_vs_pcap_rtt_matched.csv`: binned pcap RTT and probe RTT points used for fig06 and fig06b.",
            "- `tcp_pcap_packet_sanity_summary.csv`: raw pcap packet RTT summary used as the fig06 pcap sanity trace.",
            "- `tcp_probe_latest_sanity_summary.csv`: raw direct latest RTT summary used as the fig06 sanity trace.",
            "- `tcp_probe_vs_pcap_rtt_actual_timeseries.csv`: time-aligned actual pcap packet RTT and probe latest RTT samples used for fig07.",
            "- `tcp_probe_pcap_tail_summary.csv`: broader diagnostic p50/p95/p99 and absolute-error summaries.",
            "- `tcp_correlation_buffer_budget_summary.csv`: correlation-window budget check using `packet_rate_pps x latency_seconds < 2048`, with pcap tcpdump packet rates when available.",
            "- `tcp_clean_correctness_summary.csv`: compact clean baseline/reference mean-RTT correctness summaries used for fig11, fig12, fig13, fig14, fig15, and fig16.",
            "- `tcp_correctness_combined_cdf_summary.csv`: raw and median TCP correctness CDF source summaries used for fig20.",
            "- `tcp_correctness_median_paired_points.csv`: paired 1 s pcap medians and exported probe median/p50 values used for fig24.",
            "- `tcp_correctness_median_similarity_summary.csv`: bias, absolute error, correlation, KS distance, and Wasserstein distance for fig24.",
            "- `tcp_icmp_parallel_median_delta_summary.csv`: TCP/ICMP median deltas for the parallel validation scenario(s), when run.",
            "- `tcp_icmp_parallel_cdf_summary.csv`: CDF source summaries for the parallel TCP+ICMP validation figure.",
            "",
            "Interpretation reminders:",
            "- `mode=core` is logical uplink.",
            "- `mode=ran` is logical downlink.",
            "- In fig04, RAN netem is shown from a gNB view and UPF netem from a UPF view; if UPF probe series are absent, the UPF pcap view is used as a fallback and marked in the CSV.",
            "- Fig04b keeps the cross-boundary view where a delayed boundary can be crossed once or twice by the measured TCP RTT.",
            "- PCAP comparison aligns packet-level pcaps into 1 s bins against Prometheus probe mean and preferred median/p50 samples; this gives denser CDFs while keeping the probe estimator stable.",
            "- Fig06 includes raw sanity overlays; fig06b is the mean-only TCP correctness plot; fig07 is only a sample-level sanity/debug view and can look noisier.",
            "- Fig11, fig12, and fig13 are now the clean mean-based TCP correctness plots: CDF agreement, absolute-error CDF, and p50/p95 of 5 s mean RTT values.",
            "- Fig14, fig15, and fig16 are aliases of the clean mean-based TCP correctness figures kept under the older clean-figure numbering.",
            "- Fig20 and fig21 are median-focused TCP correctness plots intended to answer the supervisor concern that mean-based CDFs can look too sparse or too noisy.",
            "- Fig24 is the paired median scatter plot: points near the y=x line mean pcap reconstruction and probe median/p50 agree in the same 1 s bin.",
            "- Fig25 is separate from fig20 on purpose: it uses only the parallel TCP+ICMP scenario(s) and compares TCP pcap/probe distributions with ping and ICMP probe distributions. The MTU-sized ICMP scenario uses `ping -s 1472`, which is approximately a 1500-byte IPv4 packet.",
            "- Fig17 is a real box plot, but can stretch when pcap fallback data has extreme tails; fig18 is the safer controlled-delay figure.",
            "- The diagnostic TCP mean figures are saved with `diagnostic_...` names instead of occupying fig11, fig12, or fig13.",
            "- ICMP ping is end-to-end UE-visible RTT, while the probe is N3-observed latency, so the expected result is trend agreement rather than byte-for-byte equality.",
            "",
        ]
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results_dir = args.results_dir
    out_dir = args.out_dir or results_dir / "paper_figures"
    cleanup_old_figures(out_dir)

    prom = load_prometheus(results_dir)
    windows = load_windows(results_dir)
    ue_ip = primary_ue_ip(results_dir, prom, windows)

    notes: list[str] = []
    notes.extend(create_resource_figures(out_dir, prom))
    notes.extend(create_bpf_figure(results_dir, out_dir))
    notes.extend(create_netem_figure(results_dir, out_dir, prom, windows, ue_ip))
    notes.extend(create_icmp_figure(results_dir, out_dir, prom, windows, ue_ip))
    notes.extend(create_tcp_pcap_figure(results_dir, out_dir, prom, windows, ue_ip))
    write_readme(out_dir, notes, ue_ip)

    print(f"Wrote validation evidence figures to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

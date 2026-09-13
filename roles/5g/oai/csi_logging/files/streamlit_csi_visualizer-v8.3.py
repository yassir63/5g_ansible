#!/usr/bin/env python3
"""
CSI Visualizer v8.3 - VECTORIZED & ADAPTIVE
- Vectorized parser (pandas C engine + first-byte classification), validated == v7.3
- Heatmaps aggregated in TIME BINS (RB x window) -> monotonic time axis, O(N)
- All per-group stats via bincount/groupby (no O(groups x N) masking)
- Variance plots replaced by STANDARD DEVIATION (same units as magnitude)
- Data-driven dimensions (n_rb, n_ant, n_port, n_bins); multi-antenna ready
- Zeros (null subcarriers) KEPT in magnitude stats; empty time-bins -> NaN
"""

import streamlit as st
st.cache_data.clear()

import pandas as pd
import numpy as np
import json, io, calendar
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde, mannwhitneyu
import warnings
warnings.filterwarnings('ignore')

st.set_page_config(page_title="CSI Visualizer v8.3", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

# Units (CSI complex channel estimate, linear arbitrary units from raw I/Q)
U_MAG = "Magnitude |H| (a.u.)"
U_STD = "Std Dev (a.u.)"
_RNG = np.random.default_rng(0)
RENDER_CAP = 40000   # max scatter points actually drawn

def _sub(n, cap=RENDER_CAP):
    """Index/slice that caps the number of rendered points (rendering only, not stats)."""
    if n <= cap:
        return np.arange(n)
    return _RNG.choice(n, cap, replace=False)

# ===================== DUAL-AXIS HELPER (fixed: real data range, not padded xlim) =====================
def add_time_axis(ax, timestamps_unix):
    if timestamps_unix is None or len(timestamps_unix) == 0:
        return
    t0 = float(np.nanmin(timestamps_unix))
    t_max = float(np.nanmax(timestamps_unix)) - t0
    ax2 = ax.twiny()
    ax2.set_xlim(ax.get_xlim())                 # align with bottom axis coordinates
    xt = np.linspace(0, t_max, 7)               # ticks span ONLY the real data range
    ax2.set_xticks(xt)
    ax2.set_xticklabels([datetime.utcfromtimestamp(t0 + x).strftime('%H:%M:%S') for x in xt],
                        rotation=45, ha='left', fontsize=9)
    ax2.set_xlabel('Time (UTC)', fontsize=10, fontweight='bold')

# ===================== NUMEROLOGY =====================
class Numerology:
    SCS_MAP = {0: 15, 1: 30, 2: 60, 3: 120}
    SLOT_DURATION_MAP = {0: 1.0, 1: 0.5, 2: 0.25, 3: 0.125}
    def __init__(self, scs_mu=1):
        self.scs_mu = scs_mu
        self.scs_khz = self.SCS_MAP[scs_mu]

# ===================== VECTORIZED PARSER (validated equivalent to v7.3) =====================
@st.cache_data
def parse_csi_streaming(uploaded_file):
    content = uploaded_file.read()
    buf = np.frombuffer(content, dtype=np.uint8)
    nl = np.flatnonzero(buf == 10)
    starts = np.empty(len(nl) + 1, dtype=np.int64); starts[0] = 0; starts[1:] = nl + 1
    ends = np.empty_like(starts); ends[:len(nl)] = nl; ends[len(nl):] = len(buf)
    valid = starts < len(buf)
    first = np.zeros(len(starts), dtype=np.uint8); first[valid] = buf[starts[valid]]
    is_data    = (first >= 48) & (first <= 57)      # data rows start with the frame digit
    is_comment = first == 35                         # '#'
    is_header  = first == 102                         # repeated 'frame,' headers
    data_line_idx = np.flatnonzero(is_data)
    if len(data_line_idx) == 0:
        st.error("❌ No data lines found!"); return None

    # timestamp markers + metadata (slice only the sparse comment lines; no global split)
    metadata = {'granularity': 'rb'}
    marker_lines, marker_ts = [], []
    for i in np.flatnonzero(is_comment):
        l = content[starts[i]:ends[i]]
        if l[:12] == b'# TIMESTAMP:':
            try:
                dt = datetime.strptime(l[12:].strip().decode(), "%Y-%m-%d %H:%M:%S")
                marker_lines.append(int(i)); marker_ts.append(calendar.timegm(dt.timetuple()))
            except Exception:
                pass
        elif l[:3] == b'# {':
            try: metadata.update(json.loads(l[2:].decode()))
            except Exception: pass
    marker_lines = np.array(marker_lines, dtype=np.int64)
    marker_ts = np.array(marker_ts, dtype=np.float64)

    # format detection
    j = int(data_line_idx[0])
    ncols = len(content[starts[j]:ends[j]].decode().strip().split(','))
    is_6col = (ncols == 6)
    names = (["frame","slot","rnti","rb","real","imag"] if is_6col
             else ["frame","slot","rnti","ant","port","rb","real","imag"])
    dtypes = {"frame":np.int32,"slot":np.int16,"rb":np.int16,"real":np.float32,
              "imag":np.float32,"rnti":str,"ant":np.int8,"port":np.int8}
    st.success(f"✅ Detected format: {'6-col (1 ant/port)' if is_6col else '8-col (multi ant/port)'}")

    df = pd.read_csv(io.BytesIO(content), header=None, names=names, comment="#",
                     skiprows=is_header.nonzero()[0].tolist(),
                     dtype=dtypes, engine="c", skip_blank_lines=True)
    if len(df) != len(data_line_idx):
        st.warning(f"⚠️ Row count {len(df)} != classified {len(data_line_idx)} (continuing)")

    frames = df["frame"].to_numpy()
    slots  = df["slot"].to_numpy()
    rbs    = df["rb"].to_numpy()
    reals  = df["real"].to_numpy()
    imags  = df["imag"].to_numpy()
    n = len(df)
    if is_6col:
        ants = np.zeros(n, np.int8); ports = np.zeros(n, np.int8)
    else:
        ants = df["ant"].to_numpy(); ports = df["port"].to_numpy()

    cat = df["rnti"].astype("category"); codes = cat.cat.codes.to_numpy()
    cmap = np.array([int(c.replace("0x",""),16) if "0x" in str(c) else int(c)
                     for c in cat.cat.categories], dtype=np.int32)
    rbtis = cmap[codes]

    L = data_line_idx[:n]
    if len(marker_lines) >= 1:
        left  = np.clip(np.searchsorted(marker_lines, L, "right") - 1, 0, len(marker_lines)-1)
        right = np.clip(np.searchsorted(marker_lines, L, "left"),      0, len(marker_lines)-1)
        ll, tl = marker_lines[left], marker_ts[left]
        lr, tr = marker_lines[right], marker_ts[right]
        seg = np.maximum(lr - ll, 1)
        timestamps = tl + (L.astype(np.float64) - ll) / seg * (tr - tl)
        st.success(f"✅ Segment interpolation: {n:,} records, {len(marker_lines)} anchors "
                   f"({marker_ts[-1]-marker_ts[0]:.1f}s)")
    else:
        timestamps = np.arange(n, dtype=np.float64)
        st.warning("⚠️ No timestamps - sequential numbering")

    mags = np.sqrt(reals*reals + imags*imags).astype(np.float32)
    phases = np.arctan2(imags, reals).astype(np.float32)
    del reals, imags  # free I/Q (memory for large files)

    duration = float(timestamps.max() - timestamps.min()) if n > 1 else float(n)
    stats = {
        'total_records': n, 'num_ues': int(len(np.unique(rbtis))),
        'mag_mean': float(mags.mean()), 'mag_std': float(mags.std()),
        'mag_min': float(mags.min()), 'mag_max': float(mags.max()),
        'mag_median': float(np.median(mags)),
        'rnti_list': sorted(np.unique(rbtis).tolist()),
        'ant_list': sorted(np.unique(ants).tolist()),
        'port_list': sorted(np.unique(ports).tolist()),
        'slot_list': sorted(np.unique(slots).tolist()),
        'duration': duration,
    }
    return {'mags': mags, 'phases': phases, 'timestamps': timestamps, 'rbtis': rbtis,
            'ants': ants, 'ports': ports, 'rbs': rbs, 'frames': frames, 'slots': slots,
            'stats': stats, 'metadata': metadata}

def detect_gaps(timestamps, threshold_s=1.0):
    valid = ~np.isnan(timestamps)
    if valid.sum() < 2:
        return []
    ts = np.sort(timestamps[valid])
    d = np.diff(ts)
    idx = np.flatnonzero(d > threshold_s)
    return [{'ts_start': float(ts[i]), 'ts_end': float(ts[i+1]), 'duration_s': float(d[i])} for i in idx]

# ===================== VECTORIZED GROUP-STAT HELPERS =====================
def _grp_stats(values, keys, n_keys):
    """mean, std(ddof=0), count per integer key in [0, n_keys). Empty -> NaN. One pass, O(N)."""
    keys = keys.astype(np.int64)
    cnt = np.bincount(keys, minlength=n_keys)[:n_keys].astype(np.float64)
    s   = np.bincount(keys, weights=values,        minlength=n_keys)[:n_keys]
    s2  = np.bincount(keys, weights=values*values, minlength=n_keys)[:n_keys]
    with np.errstate(invalid='ignore', divide='ignore'):
        mean = s / cnt
        var = np.maximum(s2 / cnt - mean*mean, 0.0)
        std = np.sqrt(var)
    mean[cnt == 0] = np.nan; std[cnt == 0] = np.nan
    return mean, std, cnt

def _timebin_grid(values, rbs, tsec, window, n_rb, n_bins):
    """Mean over (rb, time-bin) cells (averages across antennas+frames in the bin). NaN if empty."""
    bin_idx = np.clip((tsec / window).astype(np.int64), 0, n_bins - 1)
    flat = rbs.astype(np.int64) * n_bins + bin_idx
    minlen = n_rb * n_bins
    s = np.bincount(flat, weights=values, minlength=minlen)[:minlen]
    c = np.bincount(flat,                 minlength=minlen)[:minlen]
    with np.errstate(invalid='ignore', divide='ignore'):
        g = s / c
    g[c == 0] = np.nan
    return g.reshape(n_rb, n_bins)

# ===================== PLOTS =====================
def plot_distribution(mags):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    nb = min(120, max(20, len(mags)//1000 + 10))
    counts, edges = np.histogram(mags, bins=nb)
    centers = (edges[:-1] + edges[1:]) / 2
    axes[0, 0].bar(centers, counts, width=(edges[1]-edges[0]), color='steelblue', edgecolor='black', alpha=0.7)
    axes[0, 0].set_title(f'Histogram ({len(mags):,})'); axes[0, 0].set_xlabel(U_MAG); axes[0, 0].set_ylabel('Count'); axes[0, 0].grid(True, alpha=0.3)
    fine_c, fine_e = np.histogram(mags, bins=1024)
    cdf = np.cumsum(fine_c) / fine_c.sum()
    axes[0, 1].plot(fine_e[1:], cdf, linewidth=2, color='steelblue')
    axes[0, 1].set_title('CDF'); axes[0, 1].set_xlabel(U_MAG); axes[0, 1].set_ylabel('Probability'); axes[0, 1].grid(True, alpha=0.3)
    axes[1, 0].boxplot(mags[_sub(len(mags))], vert=True)
    axes[1, 0].set_title('Box Plot'); axes[1, 0].set_ylabel(U_MAG); axes[1, 0].grid(True, alpha=0.3, axis='y')
    axes[1, 1].bar(centers, counts/counts.sum()/(edges[1]-edges[0]), width=(edges[1]-edges[0]), alpha=0.5, color='steelblue', edgecolor='black')
    try:
        sample = mags[_sub(len(mags), 5000)]
        kde = gaussian_kde(sample); x = np.linspace(mags.min(), mags.max(), 400)
        axes[1, 1].plot(x, kde(x), 'r-', linewidth=2, label='KDE'); axes[1, 1].legend()
    except Exception: pass
    axes[1, 1].set_title('Density + KDE'); axes[1, 1].set_xlabel(U_MAG); axes[1, 1].set_ylabel('Density'); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_per_rb(mags, rbs):
    n_rb = int(rbs.max()) + 1                          # data-driven (100, 200, ...)
    mean, std, cnt = _grp_stats(mags, rbs, n_rb)
    x = np.arange(n_rb)
    mins = np.full(n_rb, np.nan); maxs = np.full(n_rb, np.nan)
    np.fmin.at(mins, rbs, mags); np.fmax.at(maxs, rbs, mags)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].bar(x, mean, edgecolor='black', alpha=0.7, color='steelblue')
    axes[0, 0].set_title('Mean / RB'); axes[0, 0].set_xlabel('RB index'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].grid(True, alpha=0.3, axis='y')
    axes[0, 1].plot(x, std, 'o-', linewidth=2, markersize=4, color='steelblue')   # was Variance -> Std
    axes[0, 1].set_title('Std Dev / RB'); axes[0, 1].set_xlabel('RB index'); axes[0, 1].set_ylabel(U_STD); axes[0, 1].grid(True, alpha=0.3)
    axes[1, 0].errorbar(x, mean, yerr=std, fmt='o-', linewidth=2, markersize=4, color='steelblue', capsize=3, alpha=0.7)
    axes[1, 0].set_title('Mean ± Std'); axes[1, 0].set_xlabel('RB index'); axes[1, 0].set_ylabel(U_MAG); axes[1, 0].grid(True, alpha=0.3)
    axes[1, 1].fill_between(x, mins, maxs, alpha=0.3, color='steelblue', label='Range')
    axes[1, 1].plot(x, mean, 'o-', linewidth=2, color='steelblue', label='Mean')
    axes[1, 1].set_title('Range / RB'); axes[1, 1].set_xlabel('RB index'); axes[1, 1].set_ylabel(U_MAG); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def _heatmap_render(grid, t0, t_max, n_bins, window, title, cmap, cbar_label):
    fig, ax = plt.subplots(figsize=(15, 8))
    im = ax.imshow(grid, aspect='auto', cmap=cmap, origin='lower', extent=[0, n_bins*window, 0, grid.shape[0]])
    ax.set_xlabel('Time (s from start)'); ax.set_ylabel('RB index'); ax.set_title(title)
    plt.colorbar(im, ax=ax, label=cbar_label)
    ax2 = ax.twiny(); ax2.set_xlim(ax.get_xlim())
    xt = np.linspace(0, n_bins*window, 7)
    ax2.set_xticks(xt)
    ax2.set_xticklabels([datetime.utcfromtimestamp(t0 + x).strftime('%H:%M:%S') for x in xt], rotation=45, ha='left', fontsize=9)
    ax2.set_xlabel('Time (UTC)')
    plt.tight_layout(); return fig

def plot_heatmap_mag_with_time(mags, rbs, frames, timestamps, ants=None, window=10.0, per_antenna=False):
    t0 = float(np.nanmin(timestamps)); tsec = timestamps - t0
    t_max = float(tsec.max()); n_bins = int(np.floor(t_max / window)) + 1
    n_rb = int(rbs.max()) + 1
    if per_antenna and ants is not None and len(np.unique(ants)) > 1:
        ua = sorted(np.unique(ants).tolist())
        fig, axs = plt.subplots(len(ua), 1, figsize=(15, 4*len(ua)))
        axs = np.atleast_1d(axs)
        for k, a in enumerate(ua):
            mk = ants == a
            grid = _timebin_grid(mags[mk], rbs[mk], tsec[mk], window, n_rb, n_bins)
            im = axs[k].imshow(grid, aspect='auto', cmap='viridis', origin='lower', extent=[0, n_bins*window, 0, n_rb])
            axs[k].set_title(f'Magnitude RB×{int(window)}s-bin — Ant {a}'); axs[k].set_ylabel('RB index')
            plt.colorbar(im, ax=axs[k], label=U_MAG)
        axs[-1].set_xlabel('Time (s from start)')
        plt.tight_layout(); return fig
    grid = _timebin_grid(mags, rbs, tsec, window, n_rb, n_bins)
    return _heatmap_render(grid, t0, t_max, n_bins, window,
                           f'Magnitude Heatmap: RB × {int(window)}s-bin (mean over antennas, zeros kept)',
                           'viridis', U_MAG)

def plot_heatmap_phase_with_time(phases, rbs, frames, timestamps, ants=None, window=10.0, per_antenna=False):
    t0 = float(np.nanmin(timestamps)); tsec = timestamps - t0
    t_max = float(tsec.max()); n_bins = int(np.floor(t_max / window)) + 1
    n_rb = int(rbs.max()) + 1
    grid = _timebin_grid(phases, rbs, tsec, window, n_rb, n_bins)
    return _heatmap_render(grid, t0, t_max, n_bins, window,
                           f'Phase Heatmap: RB × {int(window)}s-bin (arithmetic mean)',
                           'hsv', 'Phase (rad)')

def plot_timeline(mags, phases, timestamps):
    if np.isnan(timestamps).all():
        fig, ax = plt.subplots(); ax.text(0.5, 0.5, 'No valid timestamps'); return fig
    t0 = float(np.nanmin(timestamps)); tsec = timestamps - t0
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    s = _sub(len(mags))
    axes[0, 0].scatter(tsec[s], mags[s], alpha=0.2, s=5, color='steelblue')
    axes[0, 0].set_title(f'Magnitude vs Time (subsample {len(s):,})'); axes[0, 0].set_xlabel('Time (s from start)'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].grid(True, alpha=0.3)
    w = 10.0; n_bins = int(np.floor(tsec.max()/w)) + 1
    bidx = np.clip((tsec/w).astype(np.int64), 0, n_bins-1)
    g = pd.Series(mags).groupby(bidx)
    bc = (np.arange(n_bins) + 0.5) * w
    p50 = g.median().reindex(range(n_bins)).to_numpy()
    p05 = g.quantile(0.05).reindex(range(n_bins)).to_numpy()
    p95 = g.quantile(0.95).reindex(range(n_bins)).to_numpy()
    axes[0, 1].fill_between(bc, p05, p95, alpha=0.25, color='steelblue', label='p5–p95')
    axes[0, 1].plot(bc, p50, color='red', linewidth=2, label='median')
    axes[0, 1].set_title('Per-bin percentiles (10s)'); axes[0, 1].set_xlabel('Time (s from start)'); axes[0, 1].set_ylabel(U_MAG); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    sp = _sub(len(phases), 10000)
    axes[1, 0].scatter(tsec[sp], np.rad2deg(phases[sp]), alpha=0.3, s=5, color='orange')
    axes[1, 0].set_title('Phase vs Time'); axes[1, 0].set_xlabel('Time (s from start)'); axes[1, 0].set_ylabel('Phase (°)'); axes[1, 0].grid(True, alpha=0.3)
    sig = pd.Series(p50).interpolate().bfill().ffill().to_numpy()
    sp_f = np.abs(np.fft.rfft(sig - np.nanmean(sig)))
    freqs = np.fft.rfftfreq(len(sig), d=w)
    axes[1, 1].semilogy(freqs, sp_f + 1e-9, color='steelblue')
    axes[1, 1].set_title('Spectrum of median (per 10s bin)'); axes[1, 1].set_xlabel('Frequency (Hz)'); axes[1, 1].set_ylabel('Power'); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_multipath(mags, phases, rbs):
    n_rb = int(rbs.max()) + 1; x = np.arange(n_rb)
    mag_mean, mag_std, _ = _grp_stats(mags, rbs, n_rb)
    _, ph_std, _ = _grp_stats(phases, rbs, n_rb)             # phase std (rad) per RB
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].bar(x, ph_std, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 0].set_title('Phase Spread / RB'); axes[0, 0].set_xlabel('RB index'); axes[0, 0].set_ylabel('Std (rad)'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    axes[0, 1].plot(x, mag_mean, 'o-', linewidth=2, markersize=4, color='steelblue')
    axes[0, 1].fill_between(x, mag_mean - mag_std, mag_mean + mag_std, alpha=0.2, color='steelblue')
    axes[0, 1].set_title('Mean ± Std / RB (freq. selectivity)'); axes[0, 1].set_xlabel('RB index'); axes[0, 1].set_ylabel(U_MAG); axes[0, 1].grid(True, alpha=0.3)
    axes[1, 0].axis('off'); axes[1, 0].text(0.5, 0.5, 'Multipath\nAnalysis', ha='center', va='center', fontsize=14, transform=axes[1, 0].transAxes)
    rms = np.sqrt(np.nanmean(ph_std**2))
    axes[1, 1].axis('off'); axes[1, 1].text(0.5, 0.5, f'RMS phase spread:\n{rms:.4f} rad', ha='center', va='center', fontsize=12, transform=axes[1, 1].transAxes)
    plt.tight_layout(); return fig

def plot_mimo(mags, slots, ants, ports, stats):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    sl = stats['slot_list']
    box = []
    for s in sl:
        d = mags[slots == s]
        box.append(d[_sub(len(d))] if len(d) else np.array([np.nan]))
    bp = axes[0, 0].boxplot(box, labels=[f'Slot {s}' for s in sl], patch_artist=True)
    for p in bp['boxes']: p.set_facecolor('steelblue')
    axes[0, 0].set_title('Per Slot'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].grid(True, alpha=0.3, axis='y')
    al = stats['ant_list']
    am = [np.mean(mags[ants == a]) if np.any(ants == a) else np.nan for a in al]
    axes[0, 1].bar(range(len(al)), am, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 1].set_xticks(range(len(al))); axes[0, 1].set_xticklabels([f'Ant {a}' for a in al])
    axes[0, 1].set_title('Per Antenna'); axes[0, 1].set_ylabel(U_MAG); axes[0, 1].grid(True, alpha=0.3, axis='y')
    pl = stats['port_list']
    pm = [np.mean(mags[ports == p]) if np.any(ports == p) else np.nan for p in pl]
    axes[1, 0].bar(range(len(pl)), pm, edgecolor='black', alpha=0.7, color='green')
    axes[1, 0].set_xticks(range(len(pl))); axes[1, 0].set_xticklabels([f'Port {p}' for p in pl])
    axes[1, 0].set_title('Per Port'); axes[1, 0].set_ylabel(U_MAG); axes[1, 0].grid(True, alpha=0.3, axis='y')
    axes[1, 1].axis('off')
    for yy, txt in zip([0.95,0.85,0.75,0.65,0.55],
                       ['MIMO Summary', f'Total: {len(mags):,} records', f'Mean: {np.mean(mags):.1f} a.u.',
                        f'Std: {np.std(mags):.1f} a.u.', f'Min/Max: {np.min(mags):.1f} / {np.max(mags):.1f} a.u.']):
        axes[1, 1].text(0.05, yy, txt, fontsize=(12 if yy==0.95 else 10), weight=('bold' if yy==0.95 else 'normal'), transform=axes[1, 1].transAxes, va='top')
    plt.tight_layout(); return fig

def plot_slot_signal_quality(mags, slots, stats):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    us = sorted(np.unique(slots).tolist())
    data = [mags[slots == s] for s in us]
    vio = [d[_sub(len(d), 20000)] for d in data]
    axes[0, 0].violinplot(vio, positions=range(len(us)), showmeans=True, showmedians=True)
    axes[0, 0].set_xticks(range(len(us))); axes[0, 0].set_xticklabels([f'Slot {s}' for s in us])
    axes[0, 0].set_title('Magnitude Distribution (Violin)'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].grid(True, alpha=0.3, axis='y')
    cv = [np.std(d)/(np.mean(d)+1e-6) for d in data]                 # std/mean = CV (dimensionless)
    axes[0, 1].bar(range(len(us)), cv, color='orange', edgecolor='black', alpha=0.7)
    axes[0, 1].set_xticks(range(len(us))); axes[0, 1].set_xticklabels([f'Slot {s}' for s in us])
    axes[0, 1].set_title('Variability (Std/Mean)'); axes[0, 1].set_ylabel('Std/Mean (CV, dimensionless)'); axes[0, 1].grid(True, alpha=0.3, axis='y')
    papr = [np.max(d)/(np.mean(d)+1e-6) for d in data]
    axes[1, 0].bar(range(len(us)), papr, color='red', edgecolor='black', alpha=0.7)
    axes[1, 0].set_xticks(range(len(us))); axes[1, 0].set_xticklabels([f'Slot {s}' for s in us])
    axes[1, 0].set_title('PAPR (Peak-to-Average)'); axes[1, 0].set_ylabel('Max/Mean (dimensionless)'); axes[1, 0].grid(True, alpha=0.3, axis='y')
    for s, d in zip(us, data):
        c, e = np.histogram(d, bins=512); axes[1, 1].plot(e[1:], np.cumsum(c)/c.sum(), linewidth=2, label=f'Slot {s}', alpha=0.7)
    axes[1, 1].set_title('CDF'); axes[1, 1].set_xlabel(U_MAG); axes[1, 1].set_ylabel('Probability'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_slot_temporal_stability(mags, slots, frames, timestamps, window=10.0):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    us = sorted(np.unique(slots).tolist())
    t0 = float(np.nanmin(timestamps)); tsec = timestamps - t0
    n_bins = int(np.floor(tsec.max()/window)) + 1
    bidx = np.clip((tsec/window).astype(np.int64), 0, n_bins-1)
    bc = (np.arange(n_bins) + 0.5) * window
    dfb = pd.DataFrame({'slot': slots, 'bin': bidx, 'm': mags})
    gmean = dfb.groupby(['slot', 'bin'])['m'].mean()
    gstd  = dfb.groupby(['slot', 'bin'])['m'].std(ddof=0)
    lvl0_mean = gmean.index.get_level_values(0)
    lvl0_std = gstd.index.get_level_values(0)
    means_by_slot = {}
    for s in us:
        mean_s = gmean.loc[s].reindex(range(n_bins)).to_numpy() if s in lvl0_mean else np.full(n_bins, np.nan)
        means_by_slot[s] = mean_s
        axes[0, 0].plot(bc, mean_s, 'o-', linewidth=2, markersize=2, label=f'Slot {s}', alpha=0.7)
    axes[0, 0].set_title('Mean Magnitude Evolution'); axes[0, 0].set_xlabel('Time (s from start)'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].legend(); axes[0, 0].grid(True, alpha=0.3)
    add_time_axis(axes[0, 0], timestamps)
    for s in us:
        std_s = gstd.loc[s].reindex(range(n_bins)).to_numpy() if s in lvl0_std else np.full(n_bins, np.nan)
        axes[0, 1].plot(bc, std_s, 'o-', linewidth=2, markersize=2, label=f'Slot {s}', alpha=0.7)   # was Variance -> Std
    axes[0, 1].set_title('Std Dev Evolution'); axes[0, 1].set_xlabel('Time (s from start)'); axes[0, 1].set_ylabel(U_STD); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    add_time_axis(axes[0, 1], timestamps)
    for s in us:
        mean_s = means_by_slot[s]; valid = ~np.isnan(mean_s)
        if valid.sum() >= 3:
            thirds = np.array_split(mean_s[valid], 3)
            axes[1, 0].plot(['Early','Middle','Late'], [np.mean(t) for t in thirds], 'o-', linewidth=2, markersize=8, label=f'Slot {s}')
    axes[1, 0].set_title('Signal Level by Time Period'); axes[1, 0].set_ylabel(U_MAG); axes[1, 0].legend(); axes[1, 0].grid(True, alpha=0.3)
    cvs = [np.nanstd(means_by_slot[s]) / (np.nanmean(means_by_slot[s]) + 1e-6) for s in us]
    axes[1, 1].bar(range(len(us)), cvs, color='green', edgecolor='black', alpha=0.7)
    axes[1, 1].set_xticks(range(len(us))); axes[1, 1].set_xticklabels([f'Slot {s}' for s in us])
    axes[1, 1].set_title('Temporal Stability (CV of bin-means)'); axes[1, 1].set_ylabel('CV (dimensionless)'); axes[1, 1].grid(True, alpha=0.3, axis='y')
    plt.tight_layout(); return fig

def plot_slot_rb_pattern(mags, slots, rbs):
    us = sorted(np.unique(slots).tolist())
    n_rb = int(rbs.max()) + 1
    slot_pos = {s: i for i, s in enumerate(us)}
    sidx = np.array([slot_pos[s] for s in slots])
    flat = sidx * n_rb + rbs.astype(np.int64)
    s = np.bincount(flat, weights=mags, minlength=len(us)*n_rb)[:len(us)*n_rb]
    c = np.bincount(flat,                minlength=len(us)*n_rb)[:len(us)*n_rb]
    with np.errstate(invalid='ignore', divide='ignore'):
        grid = (s/c).reshape(len(us), n_rb)
    grid[(c.reshape(len(us), n_rb)) == 0] = np.nan
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    im = axes[0, 0].imshow(grid, aspect='auto', cmap='viridis', origin='lower')
    axes[0, 0].set_yticks(range(len(us))); axes[0, 0].set_yticklabels([f'Slot {ss}' for ss in us])
    axes[0, 0].set_xlabel('RB index'); axes[0, 0].set_title('Magnitude Pattern: Slot × RB')
    plt.colorbar(im, ax=axes[0, 0], label=U_MAG)
    x = np.arange(n_rb)
    means_all = {}
    for ss in us:
        mean_s, std_s, _ = _grp_stats(mags[slots == ss], rbs[slots == ss], n_rb)
        means_all[ss] = (mean_s, std_s)
        axes[0, 1].plot(x, mean_s, 'o-', linewidth=1, markersize=3, label=f'Slot {ss}', alpha=0.7)
    axes[0, 1].set_title('Mean Magnitude per RB'); axes[0, 1].set_xlabel('RB index'); axes[0, 1].set_ylabel(U_MAG); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    dead_thr = np.percentile(mags, 5)
    for ss in us:
        mean_s = means_all[ss][0]
        dead = x[np.nan_to_num(mean_s, nan=np.inf) <= dead_thr]
        if len(dead): axes[1, 0].scatter([ss]*len(dead), dead, s=40, alpha=0.7, label=f'Slot {ss}')
    axes[1, 0].set_xlabel('Slot'); axes[1, 0].set_ylabel('RB index'); axes[1, 0].set_title(f'Dead/Weak RBs (≤ p5 = {dead_thr:.1f})'); axes[1, 0].grid(True, alpha=0.3)
    for ss in us:
        axes[1, 1].plot(x, means_all[ss][1], 'o-', linewidth=1, markersize=3, label=f'Slot {ss}', alpha=0.7)
    axes[1, 1].set_title('RB Std Dev (noise)'); axes[1, 1].set_xlabel('RB index'); axes[1, 1].set_ylabel(U_STD); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_antenna_comparison_diag(mags, ants):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    ua = sorted(np.unique(ants).tolist())
    data = [mags[ants == a] for a in ua]
    bp = axes[0].boxplot([d[_sub(len(d))] for d in data], labels=[f'Ant {a}' for a in ua], patch_artist=True)
    for p in bp['boxes']: p.set_facecolor('orange')
    axes[0].set_title('Magnitude by RX Antenna'); axes[0].set_ylabel(U_MAG); axes[0].grid(True, alpha=0.3, axis='y')
    if len(ua) >= 2:
        a0, a1 = data[0][_sub(len(data[0]),100000)], data[1][_sub(len(data[1]),100000)]
        _, pval = mannwhitneyu(a0, a1)
        axes[1].hist(data[0][_sub(len(data[0]))], bins=50, alpha=0.5, label=f'Ant {ua[0]}', color='blue')
        axes[1].hist(data[1][_sub(len(data[1]))], bins=50, alpha=0.5, label=f'Ant {ua[1]}', color='orange')
        axes[1].set_title(f'Distribution (Mann-Whitney p={pval:.4f})'); axes[1].set_xlabel(U_MAG); axes[1].set_ylabel('Count'); axes[1].legend(); axes[1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_rb_noise_profile(mags, rbs, ants):
    n_rb = int(rbs.max()) + 1; x = np.arange(n_rb)
    mean, std, _ = _grp_stats(mags, rbs, n_rb)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].plot(x, mean, 'o-', linewidth=1, markersize=4, color='steelblue')
    axes[0, 0].axhline(np.nanmean(mean), color='r', linestyle='--', label='Mean')
    axes[0, 0].set_title('Mean Magnitude per RB'); axes[0, 0].set_xlabel('RB index'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].legend(); axes[0, 0].grid(True, alpha=0.3)
    axes[0, 1].plot(x, std, 'o-', linewidth=1, markersize=4, color='orange')
    axes[0, 1].axhline(np.nanmean(std), color='r', linestyle='--', label='Mean')
    thr = np.nanmean(std) + 2*np.nanstd(std)
    axes[0, 1].axhline(thr, color='red', linestyle=':', label='Anomaly thr')
    noisy = x[np.nan_to_num(std) > thr]
    axes[0, 1].scatter(noisy, std[noisy], color='red', s=100, marker='x', label=f'Anomalies ({len(noisy)})')
    axes[0, 1].set_title('Std Dev per RB (noise)'); axes[0, 1].set_xlabel('RB index'); axes[0, 1].set_ylabel(U_STD); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    for a in sorted(np.unique(ants).tolist()):
        mk = ants == a
        mean_a, _, _ = _grp_stats(mags[mk], rbs[mk], n_rb)
        axes[1, 0].plot(x, mean_a, 'o-', linewidth=1, markersize=3, label=f'Ant {a}', alpha=0.7)
    axes[1, 0].set_title('Mean per RB (per Antenna)'); axes[1, 0].set_xlabel('RB index'); axes[1, 0].set_ylabel(U_MAG); axes[1, 0].legend(); axes[1, 0].grid(True, alpha=0.3)
    ratio = std / (mean + 1e-6)
    axes[1, 1].plot(x, ratio, 'o-', linewidth=1, markersize=4, color='red')
    axes[1, 1].axhline(np.nanmean(ratio), color='blue', linestyle='--', label='Mean')
    axes[1, 1].set_title('Noise Ratio (Std/Mean) per RB'); axes[1, 1].set_xlabel('RB index'); axes[1, 1].set_ylabel('Std/Mean (dimensionless)'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_time_anomalies(mags, timestamps):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    t0 = float(np.nanmin(timestamps)); tsec = timestamps - t0
    s = _sub(len(mags))
    axes[0, 0].scatter(tsec[s], mags[s], alpha=0.2, s=5, color='steelblue')
    axes[0, 0].set_title(f'Magnitude vs Time (subsample {len(s):,})'); axes[0, 0].set_xlabel('Time (s from start)'); axes[0, 0].set_ylabel(U_MAG); axes[0, 0].grid(True, alpha=0.3)
    w = 10.0; n_bins = int(np.floor(tsec.max()/w)) + 1
    bidx = np.clip((tsec/w).astype(np.int64), 0, n_bins-1)
    bmean, bstd, _ = _grp_stats(mags, bidx, n_bins)
    bc = (np.arange(n_bins)+0.5)*w
    axes[0, 1].plot(bc, bmean, color='blue', linewidth=1, label='Mean')
    axes[0, 1].fill_between(bc, bmean-bstd, bmean+bstd, alpha=0.2, color='blue')
    axes[0, 1].set_title('Per-bin Mean ± Std (10s)'); axes[0, 1].set_xlabel('Time (s from start)'); axes[0, 1].set_ylabel(U_MAG); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    q1, q3 = np.percentile(mags, [25, 75]); iqr = q3 - q1
    out = (mags < q1 - 3*iqr) | (mags > q3 + 3*iqr)
    axes[1, 0].scatter(tsec[s], mags[s], alpha=0.1, s=5, color='gray', label='Normal')
    os_ = np.flatnonzero(out)
    if len(os_): os_ = os_[_sub(len(os_), 5000)]
    axes[1, 0].scatter(tsec[os_], mags[os_], alpha=0.5, s=20, color='red', label=f'Outliers ({int(out.sum())})')
    axes[1, 0].set_title('Outlier Detection (IQR×3)'); axes[1, 0].set_xlabel('Time (s from start)'); axes[1, 0].set_ylabel(U_MAG); axes[1, 0].legend(); axes[1, 0].grid(True, alpha=0.3)
    c, e = np.histogram(mags, bins=min(120, len(mags)//1000+10))
    axes[1, 1].bar((e[:-1]+e[1:])/2, c, width=(e[1]-e[0]), color='steelblue', edgecolor='black', alpha=0.7)
    axes[1, 1].axvline(np.mean(mags), color='r', linestyle='--', linewidth=2, label=f'Mean={np.mean(mags):.1f}')
    axes[1, 1].axvline(np.median(mags), color='g', linestyle='--', linewidth=2, label=f'Median={np.median(mags):.1f}')
    axes[1, 1].set_title('Distribution'); axes[1, 1].set_xlabel(U_MAG); axes[1, 1].set_ylabel('Count'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def plot_phase_analysis(phases, mags):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].hist(np.rad2deg(phases[_sub(len(phases), 500000)]), bins=120, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 0].set_title('Phase Distribution'); axes[0, 0].set_xlabel('Phase (°)'); axes[0, 0].set_ylabel('Count'); axes[0, 0].grid(True, alpha=0.3)
    s = _sub(len(phases), 10000)
    axes[0, 1].scatter(mags[s], np.rad2deg(phases[s]), alpha=0.3, s=10, color='orange')
    axes[0, 1].set_title('Phase vs Magnitude'); axes[0, 1].set_xlabel(U_MAG); axes[0, 1].set_ylabel('Phase (°)'); axes[0, 1].grid(True, alpha=0.3)
    R = np.abs(np.mean(np.exp(1j*phases)))                          # mean resultant length
    circ_std = np.sqrt(-2*np.log(max(R, 1e-12)))                    # circular standard deviation (rad)
    axes[1, 0].axis('off')
    axes[1, 0].text(0.1, 0.7, 'Phase Quality', fontsize=14, weight='bold', transform=axes[1, 0].transAxes)
    axes[1, 0].text(0.1, 0.5, f'Circ. Std: {circ_std:.4f} rad', fontsize=12, transform=axes[1, 0].transAxes)
    axes[1, 0].text(0.1, 0.3, f'Mean resultant R: {R:.4f}', fontsize=12, transform=axes[1, 0].transAxes)
    u = phases[_sub(len(phases), 5000)]
    axes[1, 1].scatter(np.cos(u), np.sin(u), alpha=0.1, s=5)
    axes[1, 1].add_patch(plt.Circle((0, 0), 1, fill=False, color='r', linestyle='--'))
    axes[1, 1].set_xlim(-1.5, 1.5); axes[1, 1].set_ylim(-1.5, 1.5); axes[1, 1].set_aspect('equal')
    axes[1, 1].set_title('Phase Space (unit circle)'); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout(); return fig

def _fx(*eqs, note=None, analysis=None):
    """Render the math definition(s) + an analysis guide explaining the figure."""
    with st.expander("🧮 Formulas / definitions", expanded=True):
        for e in eqs:
            st.latex(e)
        if note:
            st.caption(note)
    if analysis:
        with st.expander("🔎 Reading & analysis (network use · anomalies)", expanded=True):
            st.markdown(analysis)

# ===================== UI =====================
st.markdown("# 📊 CSI Visualizer v8.3")
st.markdown("**Vectorized · adaptive · time-binned heatmaps · std-based · formulas + analysis per figure (EN)**")

st.sidebar.header("⚙️ Configuration")
scs_mu = st.sidebar.selectbox("Numerology (µ)", [0, 1, 2, 3], format_func=lambda x: f"{Numerology.SCS_MAP[x]} kHz", index=1)
hm_window = st.sidebar.slider("Heatmap time-bin (s)", 1, 60, 10, 1)
hm_per_ant = st.sidebar.checkbox("Heatmaps per antenna (else mean)", value=False)
st.sidebar.divider()

uploaded_file = st.file_uploader("📂 Upload CSI CSV", type=['csv'])
if uploaded_file is None:
    st.info("Upload a CSI CSV file (6-col or 8-col format)"); st.stop()

with st.spinner("⚡ Parsing (vectorized)..."):
    parsed_data = parse_csi_streaming(uploaded_file)
    if parsed_data is None:
        st.error("Parse failed - check CSV format"); st.stop()

stats = parsed_data['stats']
c = st.columns(5)
c[0].metric("Records", f"{stats['total_records']:,}")
c[1].metric("UEs", stats['num_ues'])
c[2].metric("Duration", f"{stats['duration']:.1f}s")
c[3].metric("Antennas", len(stats['ant_list']))
c[4].metric("Slots", len(stats['slot_list']))

st.sidebar.header("🎛️ Filters")
rntis_list = [f"0x{r:04x}" for r in stats['rnti_list']]
rntis_selected = st.sidebar.multiselect("UE (RNTI)", rntis_list, default=[rntis_list[0]] if rntis_list else [])
rntis_int = [int(r, 16) for r in rntis_selected] if rntis_selected else stats['rnti_list']
slots_list = [f"Slot {s}" for s in stats['slot_list']]
slots_selected = st.sidebar.multiselect("Slots", slots_list, default=slots_list)
slots_int = [int(s.split()[1]) for s in slots_selected] if slots_selected else stats['slot_list']
ants_list = [f"Ant {a}" for a in stats['ant_list']]
ants_selected = st.sidebar.multiselect("RX Antennas", ants_list, default=ants_list)
ants_int = [int(a.split()[1]) for a in ants_selected] if ants_selected else stats['ant_list']
ports_list = [f"Port {p}" for p in stats['port_list']]
ports_selected = st.sidebar.multiselect("TX Ports", ports_list, default=ports_list)
ports_int = [int(p.split()[1]) for p in ports_selected] if ports_selected else stats['port_list']

mask = (np.isin(parsed_data['rbtis'], rntis_int) & np.isin(parsed_data['slots'], slots_int)
        & np.isin(parsed_data['ants'], ants_int) & np.isin(parsed_data['ports'], ports_int))

mags_f = parsed_data['mags'][mask];   phases_f = parsed_data['phases'][mask]
slots_f = parsed_data['slots'][mask]; ants_f = parsed_data['ants'][mask]
rbs_f = parsed_data['rbs'][mask];     frames_f = parsed_data['frames'][mask]
ts_f = parsed_data['timestamps'][mask]; ports_f = parsed_data['ports'][mask]
st.sidebar.metric("Filtered", f"{len(mags_f):,}")

gap_threshold = st.sidebar.slider("Gap threshold (s)", 0.1, 10.0, 1.0, 0.1)
gaps = detect_gaps(ts_f, gap_threshold)
(st.sidebar.warning(f"⚠️ {len(gaps)} gaps") if gaps else st.sidebar.success("✅ No gaps"))
if len(mags_f) == 0:
    st.error("No data matches filters"); st.stop()

tabs = st.tabs(["📋 Summary","📈 Distribution","📊 Per-RB","🔥 Heatmap Mag","⏱️ Timeline","🔍 Phase Heat",
    "📡 Multipath","🎯 MIMO","🎯 Slot Quality","⏱️ Slot Temporal","📍 Slot RB",
    "📡 Antenna Diag","🔍 RB Noise","⏱️ Time Anomalies","🌀 Phase Analysis"])

with tabs[0]:
    st.subheader("📋 Data Summary")
    st.write(f"**Format:** {'6-col (1 ant/port)' if len(stats['ant_list'])==1 else '8-col (multi)'}")
    st.write(f"**Records (filtered):** {len(mags_f):,}")
    st.write(f"**Magnitude:** {np.mean(mags_f):.2f} ± {np.std(mags_f):.2f} a.u. (zeros = null subcarriers kept)")
    _fx(r"|H_i| = \sqrt{I_i^{\,2} + Q_i^{\,2}} \qquad \varphi_i = \operatorname{atan2}(Q_i,\ I_i)",
        note="Raw complex CSI (I,Q) -> linear magnitude (arbitrary units) and phase (rad). "
             "Zeros (real=imag=0) are null subcarriers / unallocated RBs: real data, kept.",
        analysis="**Purpose.** Entry view: amplitude |H| (link quality per subcarrier) and phase of the estimated channel. The fraction of zeros reflects the allocation (null RBs).\n\n**Anomalies / health.** Check that the record count, duration and % of zeros match the expected scenario. A collapsing mean magnitude or an unexpected % of zeros points to a link, RF-gain or allocation problem.")
with tabs[1]:
    st.pyplot(plot_distribution(mags_f), width='stretch')
    _fx(r"\mathrm{CDF}(x) = \frac{1}{N}\sum_{i=1}^{N}\mathbf{1}\!\left[\,|H_i| \le x\,\right]",
        r"\hat f(x) = \frac{1}{N h}\sum_{i=1}^{N} K\!\left(\frac{x-|H_i|}{h}\right) \quad (\text{KDE, Gaussian kernel})",
        note="Histogram, CDF (cumulative), boxplot (quartiles) and density + KDE of the magnitude.",
        analysis="**Purpose.** Statistical shape of |H| over the whole run. The spike at 0 (about 30%) = null RBs; the main lobe = active RBs. Mean < median confirms the pull from the zeros.\n\n**Anomalies / health.** Healthy channel = a clean, tight active lobe. A pronounced low tail = deep fades; a lobe that widens or shifts = changing conditions (cross-check with the Timeline). On the CDF, the initial plateau = fraction of unallocated RBs.")
with tabs[2]:
    st.pyplot(plot_per_rb(mags_f, rbs_f), width='stretch')
    _fx(r"\mu_r = \frac{1}{N_r}\sum_{i\in r} |H_i| \qquad \sigma_r = \sqrt{\frac{1}{N_r}\sum_{i\in r}\bigl(|H_i|-\mu_r\bigr)^2}",
        r"\text{Range}_r = \bigl[\min_{i\in r}|H_i|,\ \max_{i\in r}|H_i|\bigr]",
        note="Mean / std / range per RB (frequency selectivity). Zeros included.",
        analysis="**Purpose.** Channel frequency selectivity: mu_r is the frequency response, sigma_r the stability of each sub-band. Basis for understanding why some RBs carry throughput better.\n\n**Anomalies / health.** A fairly flat response = weakly selective channel (favourable). Localized dips in mu_r = frequency-fading notches; a high sigma_r on specific RBs = narrowband noise/interference. RBs constantly at 0 = unallocated (normal) - unless they should be active.")
with tabs[3]:
    st.pyplot(plot_heatmap_mag_with_time(mags_f, rbs_f, frames_f, ts_f, ants_f, hm_window, hm_per_ant), width='stretch')
    _fx(r"b(t) = \left\lfloor t / W \right\rfloor \quad (W = \text{window, s})",
        r"G(r,b) = \frac{1}{N_{r,b}}\!\!\sum_{\substack{i:\,rb_i=r\\ b(t_i)=b}}\!\! |H_i| \;,\qquad G(r,b)=\mathrm{NaN}\ \text{if}\ N_{r,b}=0",
        note="Mean per cell (RB x time-bin), averaged over the selected antennas. "
             "Time-binning avoids frame resets and gives a monotonic time axis.",
        analysis="**Purpose.** The key view: joint frequency x time evolution of the channel. Horizontal bands = frequency structure; variations along time = mobility / environment change. Ideal to tie a performance drop to a precise instant.\n\n**Anomalies / health.** Stable channel = regular colours over time. A sharp vertical break = regime change (handover, UE repositioning - e.g. ~1550 s here); dark columns = signal loss / measurement gap; a dead RB line = failing sub-band.")
with tabs[4]:
    st.pyplot(plot_timeline(mags_f, phases_f, ts_f), width='stretch')
    _fx(r"p_q(b) = Q_q\bigl\{\,|H_i| : b(t_i)=b\,\bigr\}, \quad q\in\{5,50,95\}",
        r"S(f) = \bigl|\,\mathcal{F}\{\,p_{50}(b) - \overline{p_{50}}\,\}\,\bigr|",
        note="Per-bin (10 s) percentile bands + spectrum of the median (uniform 10 s sampling -> correct Hz axis).",
        analysis="**Purpose.** Overall temporal stability and periodicities. The p5-p95 bands = instantaneous spread; the spectrum of the median reveals cycles (mobility, antenna rotation, periodic interference).\n\n**Anomalies / health.** Tight bands and a flat median = stable channel. Widening bands or median jumps = instability; a sharp spectral peak = a periodic source (interference, traffic cycle) to investigate.")
with tabs[5]:
    st.pyplot(plot_heatmap_phase_with_time(phases_f, rbs_f, frames_f, ts_f, ants_f, hm_window, hm_per_ant), width='stretch')
    _fx(r"G_\varphi(r,b) = \frac{1}{N_{r,b}}\!\!\sum_{\substack{i:\,rb_i=r\\ b(t_i)=b}}\!\! \varphi_i",
        note="Arithmetic mean of the phase per RB x bin cell. WARNING: phase is circular; "
             "the arithmetic mean is only valid for small dispersions.",
        analysis="**Purpose.** Phase structure in frequency x time: informs on timing/delay and channel coherence.\n\n**Anomalies / health.** Phase rotating quickly over time = frequency error (CFO) or strong mobility; discontinuities = sync losses. WARNING: interpret with care (arithmetic mean of a circular quantity) and remember null RBs sit at phase 0.")
with tabs[6]:
    st.pyplot(plot_multipath(mags_f, phases_f, rbs_f), width='stretch')
    _fx(r"\sigma^{\varphi}_r = \operatorname{std}\bigl\{\varphi_i : rb_i = r\bigr\} \ \text{(rad)}",
        r"\text{RMS}_\varphi = \sqrt{\frac{1}{N_{rb}}\sum_r \bigl(\sigma^{\varphi}_r\bigr)^2}",
        note="Per-RB phase spread and global RMS (indicator of delay spread / coherence).",
        analysis="**Purpose.** Per-RB phase dispersion approximates delay spread / coherence bandwidth. Low dispersion = weakly dispersive channel (near-LOS); high = rich multipath -> reduced frequency coherence.\n\n**Anomalies / health.** A sudden rise of the spread or RMS = onset of reflections/multipath (can explain an MCS drop). Very uneven dispersion between neighbouring RBs = selective interference.")
with tabs[7]:
    st.pyplot(plot_mimo(mags_f, slots_f, ants_f, ports_f, stats), width='stretch')
    _fx(r"\mu_g = \frac{1}{N_g}\sum_{i\in g} |H_i|, \qquad g \in \{\text{slot},\ \text{antenna},\ \text{port}\}",
        note="Mean magnitude per slot (boxplot), per RX antenna and per TX port.",
        analysis="**Purpose.** Branch comparison: balance across slots, RX antennas and TX ports. In multi-antenna, lets you check the symmetry of the RF chains.\n\n**Anomalies / health.** Antennas/ports at comparable levels = balanced chains. One clearly weaker antenna/port = RF imbalance, cabling, or mis-set gain; an atypical slot = scheduling/allocation issue.")
with tabs[8]:
    st.pyplot(plot_slot_signal_quality(mags_f, slots_f, stats), width='stretch')
    _fx(r"\mathrm{CV} = \frac{\sigma}{\mu} \qquad \mathrm{PAPR} = \frac{\max_i |H_i|}{\mu}",
        note="Distribution (violin), variability CV = sigma/mu (dimensionless), PAPR and CDF per slot.",
        analysis="**Purpose.** Per-slot quality: CV measures relative stability, PAPR the dynamics, the CDF the spread. Useful to compare slots carrying different UEs/flows.\n\n**Anomalies / health.** Low CV and similar distributions = homogeneous, stable slots. A high CV on a slot = instability (interference, scheduling); an abnormal PAPR or a shifted CDF = behaviour to isolate.")
with tabs[9]:
    st.pyplot(plot_slot_temporal_stability(mags_f, slots_f, frames_f, ts_f, hm_window), width='stretch')
    _fx(r"\mu_{s,b} = \frac{1}{N_{s,b}}\!\!\sum_{\substack{i:\,slot=s\\ b(t_i)=b}}\!\! |H_i|, \qquad \sigma_{s,b} = \sqrt{\tfrac{1}{N_{s,b}}\!\sum (|H_i|-\mu_{s,b})^2}",
        r"\mathrm{CV}_s = \frac{\operatorname{nanstd}_b\!\left(\mu_{s,b}\right)}{\operatorname{nanmean}_b\!\left(\mu_{s,b}\right)}",
        note="Per (slot, 10 s bin): mean and standard deviation - zeros kept. An empty bin -> NaN, "
             "so it does NOT influence the other bins or the CV (nan-aware aggregation).",
        analysis="**Purpose.** Per-slot temporal stability (mu, sigma per bin; CV of bin-means). This is the direct link to throughput/BLER: a slot whose mu drops or whose CV rises explains a degradation at a given instant.\n\n**Anomalies / health.** Flat curves and low CV = stable slot (e.g. slot 9, CV about 0.07). A mu drop, a sigma spike (e.g. slot 8 spike ~1500 s) or a high CV = an event to correlate with the logs. Gaps (empty bins) indicate partial slot coverage, without biasing the stats.")
with tabs[10]:
    st.pyplot(plot_slot_rb_pattern(mags_f, slots_f, rbs_f), width='stretch')
    _fx(r"G(s,r) = \frac{1}{N_{s,r}}\!\!\sum_{\substack{i:\,slot=s\\ rb_i=r}}\!\! |H_i|",
        r"\text{dead RB}: \ \mu_{s,r} \le Q_5\bigl\{|H|\bigr\}",
        note="Magnitude pattern Slot x RB, mean/std per RB, and dead/weak RBs (<= 5th percentile).",
        analysis="**Purpose.** Slot x RB magnitude pattern: crosses frequency structure with slot. Reveals which RBs carry signal per slot and detects dead RBs.\n\n**Anomalies / health.** An RB pattern consistent across slots = stable allocation. Unexpected dead RBs (<= p5) or a high std on some RBs = degraded/noisy sub-bands to watch.")
with tabs[11]:
    if len(np.unique(ants_f)) > 1:
        st.pyplot(plot_antenna_comparison_diag(mags_f, ants_f), width='stretch')
        _fx(r"U = \sum_{i}\sum_{j} S(x_i, y_j),\quad S=\begin{cases}1 & x_i>y_j\\ \tfrac12 & x_i=y_j\\ 0 & x_i<y_j\end{cases}",
            note="Mann-Whitney U test between two antennas (distribution equality); p-value shown.",
            analysis="**Purpose.** Statistical test (Mann-Whitney U) of distribution equality between two antennas: quantifies diversity or an imbalance.\n\n**Anomalies / health.** p >= 0.05 = statistically equivalent antennas (healthy diversity). Very low p + separated medians = one systematically weaker branch (RF/antenna fault) to fix.")
    else:
        st.info("Only one antenna in filtered data")
with tabs[12]:
    st.pyplot(plot_rb_noise_profile(mags_f, rbs_f, ants_f), width='stretch')
    _fx(r"\text{ratio}_r = \frac{\sigma_r}{\mu_r} \qquad \text{anomaly threshold} = \overline{\sigma_r} + 2\,\operatorname{std}_r(\sigma_r)",
        note="Per-RB standard deviation (noise), sigma/mu ratio, and anomaly detection beyond mean + 2 sigma.",
        analysis="**Purpose.** Per-RB noise/variability profile: sigma_r and the sigma/mu ratio give the relative quality of each sub-band, independent of level.\n\n**Anomalies / health.** Uniform sigma and ratio = homogeneous noise. RBs beyond the threshold (red crosses) = noisy/interfered sub-bands; a localized high ratio = narrowband interference targeting those RBs.")
with tabs[13]:
    st.pyplot(plot_time_anomalies(mags_f, ts_f), width='stretch')
    _fx(r"\mathrm{IQR} = Q_3 - Q_1",
        r"\text{outlier}: \ |H_i| < Q_1 - 3\,\mathrm{IQR} \ \ \text{or} \ \ |H_i| > Q_3 + 3\,\mathrm{IQR}",
        note="Outlier detection (IQR x3) + per-bin (10 s) mean +/- std band.",
        analysis="**Purpose.** Detection of point events (IQR x3 outliers) and mean +/- sigma trend per bin. Spots glitches and drops over time.\n\n**Anomalies / health.** Few/no outliers and a stable mean band = nominal operation. Bursts of outliers = glitches/saturation; a drop of the band = level loss. WARNING: with ~30% zeros, Q1 about 0 makes the IQR detector insensitive: cross-check with the heatmap.")
with tabs[14]:
    st.pyplot(plot_phase_analysis(phases_f, mags_f), width='stretch')
    _fx(r"R = \left|\,\frac{1}{N}\sum_{i=1}^{N} e^{\,j\varphi_i}\,\right| \qquad \sigma_{\text{circ}} = \sqrt{-2\ln R}\ \ \text{(rad)}",
        note="Mean resultant length R in [0,1] and circular standard deviation (replaces circular variance).",
        analysis="**Purpose.** Global phase coherence: R close to 1 = concentrated phase (stable/LOS channel); low R = dispersed phase. sigma_circ complements the reading.\n\n**Anomalies / health.** High and stable R = good coherence. A drop of R = loss of coherence (CFO, mobility, noise). WARNING: the spike at 0 deg comes from null RBs (undefined phase rendered as 0): account for it in the interpretation.")
if gaps:
    st.divider(); st.subheader("⚠️ Gaps Detected"); st.dataframe(pd.DataFrame(gaps), width='stretch')
st.markdown(f"**v8.3** | {len(mags_f):,} records | {'6-col' if len(stats['ant_list'])==1 else '8-col'} | vectorized ✅")

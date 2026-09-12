#!/usr/bin/env python3
"""
CSI Visualizer v6.0 COMPLETE - All plots + diagnostic features
"""
import streamlit as st
st.cache_data.clear()
import pandas as pd
import numpy as np
import json
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
from scipy.stats import gaussian_kde, mannwhitneyu
import warnings
warnings.filterwarnings('ignore')

st.set_page_config(page_title="CSI Visualizer v6.0", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

class Numerology:
    SCS_MAP = {0: 15, 1: 30, 2: 60, 3: 120}
    SLOT_DURATION_MAP = {0: 1.0, 1: 0.5, 2: 0.25, 3: 0.125}
    def __init__(self, scs_mu=1):
        self.scs_mu = scs_mu
        self.scs_khz = self.SCS_MAP[scs_mu]
        self.slot_duration_ms = self.SLOT_DURATION_MAP[scs_mu]

def parse_csi(input_bytes: bytes):
    text = input_bytes.decode('utf-8', errors='replace')
    lines = text.split('\n')
    
    metadata = {'granularity': 'rb'}
    timestamp_markers = []
    
    for i, line in enumerate(lines):
        line_strip = line.strip()
        if line_strip.startswith('# {'):
            try:
                meta = json.loads(line_strip[2:])
                metadata.update(meta)
            except:
                pass
        if line_strip.startswith('# TIMESTAMP:'):
            ts_str = line_strip.replace('# TIMESTAMP:', '').strip()
            try:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                timestamp_markers.append((i, dt.timestamp()))
            except:
                pass
    
    if len(timestamp_markers) < 2:
        return None, None
    
    records = []
    for i, line in enumerate(lines):
        line_strip = line.strip()
        if not line_strip or line_strip.startswith('#'):
            continue
        
        parts = line_strip.split(',')
        if len(parts) < 8:
            continue
        
        try:
            rec = {
                'line': i,
                'frame': int(parts[0]),
                'slot': int(parts[1]),
                'rnti': int(parts[2].replace('0x', ''), 16) if '0x' in parts[2] else int(parts[2]),
                'ant_rx': int(parts[3]),
                'port_tx': int(parts[4]),
                'rb': int(parts[5]),
                'real': int(parts[6]),
                'imag': int(parts[7]),
            }
            records.append(rec)
        except:
            continue
    
    if not records:
        return None, None
    
    for rec in records:
        line_num = rec['line']
        ts_value = None
        
        for m in range(len(timestamp_markers) - 1):
            line_start, ts_start = timestamp_markers[m]
            line_end, ts_end = timestamp_markers[m + 1]
            
            if line_start < line_num < line_end:
                frac = (line_num - line_start) / (line_end - line_start)
                ts_value = ts_start + frac * (ts_end - ts_start)
                break
        
        if ts_value is None:
            ts_value = timestamp_markers[0][1]
        
        rec['timestamp_unix'] = ts_value
    
    reals = np.array([r['real'] for r in records], dtype=np.float32)
    imags = np.array([r['imag'] for r in records], dtype=np.float32)
    mags = np.sqrt(reals**2 + imags**2)
    phases = np.arctan2(imags, reals)
    timestamps = np.array([r['timestamp_unix'] for r in records], dtype=np.float64)
    
    rbtis = np.array([r['rnti'] for r in records], dtype=np.int32)
    ants = np.array([r['ant_rx'] for r in records], dtype=np.int8)
    ports = np.array([r['port_tx'] for r in records], dtype=np.int8)
    rbs = np.array([r['rb'] for r in records], dtype=np.int16)
    frames = np.array([r['frame'] for r in records], dtype=np.int32)
    slots = np.array([r['slot'] for r in records], dtype=np.int16)
    
    duration = float(np.max(timestamps) - np.min(timestamps)) if len(timestamps) > 1 else 0.0
    
    stats = {
        'total_records': len(mags),
        'num_ues': len(np.unique(rbtis)),
        'mag_mean': float(np.mean(mags)),
        'mag_std': float(np.std(mags)),
        'mag_min': float(np.min(mags)),
        'mag_max': float(np.max(mags)),
        'mag_median': float(np.median(mags)),
        'mag_p95': float(np.percentile(mags, 95)),
        'mag_p05': float(np.percentile(mags, 5)),
        'rnti_list': sorted(np.unique(rbtis).tolist()),
        'ant_list': sorted(np.unique(ants).tolist()),
        'port_list': sorted(np.unique(ports).tolist()),
        'slot_list': sorted(np.unique(slots).tolist()),
        'duration': duration,
    }
    
    return {
        'mags': mags, 'phases': phases, 'timestamps': timestamps,
        'rbtis': rbtis, 'ants': ants, 'ports': ports, 'rbs': rbs, 'frames': frames, 'slots': slots,
        'stats': stats, 'metadata': metadata
    }, stats

def detect_gaps(timestamps, threshold_s=1.0):
    valid_mask = ~np.isnan(timestamps)
    if valid_mask.sum() < 2:
        return []
    valid_ts = np.sort(timestamps[valid_mask])
    gaps = []
    for i in range(len(valid_ts) - 1):
        delta = valid_ts[i+1] - valid_ts[i]
        if delta > threshold_s:
            gaps.append({'ts_start': float(valid_ts[i]), 'ts_end': float(valid_ts[i+1]), 'duration_s': float(delta)})
    return gaps

# ===================== ORIGINAL PLOTS =====================

def plot_distribution(mags):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].hist(mags, bins=min(100, len(mags)//1000 + 10), edgecolor='black', alpha=0.7, color='steelblue')
    axes[0, 0].set_title(f'Histogram ({len(mags):,})'); axes[0, 0].set_xlabel('Magnitude'); axes[0, 0].set_ylabel('Count'); axes[0, 0].grid(True, alpha=0.3)
    sorted_mags = np.sort(mags)
    cdf = np.arange(1, len(sorted_mags) + 1) / len(sorted_mags)
    axes[0, 1].plot(sorted_mags, cdf, linewidth=2, color='steelblue'); axes[0, 1].set_title('CDF'); axes[0, 1].set_xlabel('Magnitude'); axes[0, 1].set_ylabel('Probability'); axes[0, 1].grid(True, alpha=0.3)
    axes[1, 0].boxplot(mags, vert=True); axes[1, 0].set_title('Box Plot'); axes[1, 0].set_ylabel('Magnitude'); axes[1, 0].grid(True, alpha=0.3, axis='y')
    axes[1, 1].hist(mags, bins=min(100, len(mags)//1000 + 10), density=True, alpha=0.5, color='steelblue', edgecolor='black')
    try:
        sample = mags[::max(1, len(mags)//5000)]
        kde = gaussian_kde(sample)
        x = np.linspace(np.min(mags), np.max(mags), 500)
        axes[1, 1].plot(x, kde(x), 'r-', linewidth=2, label='KDE')
    except: pass
    axes[1, 1].set_title('KDE'); axes[1, 1].set_xlabel('Magnitude'); axes[1, 1].set_ylabel('Density'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

def plot_per_rb(mags, rbs):
    unique_rbs = np.unique(rbs)[:100]
    means = np.array([np.mean(mags[rbs == rb]) for rb in unique_rbs])
    stds = np.array([np.std(mags[rbs == rb]) for rb in unique_rbs])
    variances = np.array([np.var(mags[rbs == rb]) for rb in unique_rbs])
    mins = np.array([np.min(mags[rbs == rb]) for rb in unique_rbs])
    maxs = np.array([np.max(mags[rbs == rb]) for rb in unique_rbs])
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].bar(unique_rbs, means, edgecolor='black', alpha=0.7, color='steelblue'); axes[0, 0].set_title(f'Mean/RB'); axes[0, 0].set_xlabel('RB'); axes[0, 0].set_ylabel('Mean'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    axes[0, 1].plot(unique_rbs, variances, 'o-', linewidth=2, markersize=4, color='steelblue'); axes[0, 1].set_title('Variance/RB'); axes[0, 1].set_xlabel('RB'); axes[0, 1].set_ylabel('Variance'); axes[0, 1].grid(True, alpha=0.3)
    axes[1, 0].errorbar(unique_rbs, means, yerr=stds, fmt='o-', linewidth=2, markersize=4, color='steelblue', capsize=3, alpha=0.7); axes[1, 0].set_title('Mean±Std'); axes[1, 0].set_xlabel('RB'); axes[1, 0].set_ylabel('Magnitude'); axes[1, 0].grid(True, alpha=0.3)
    axes[1, 1].fill_between(unique_rbs, mins, maxs, alpha=0.3, color='steelblue', label='Range'); axes[1, 1].plot(unique_rbs, means, 'o-', linewidth=2, color='steelblue', label='Mean'); axes[1, 1].set_title('Range/RB'); axes[1, 1].set_xlabel('RB'); axes[1, 1].set_ylabel('Magnitude'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

def plot_heatmap_mag_with_time(mags, rbs, frames, timestamps):
    unique_rbs = np.unique(rbs)[:64]
    unique_frames = np.unique(frames)[:200]
    heatmap_data = np.zeros((len(unique_rbs), len(unique_frames)))
    frame_times = np.zeros(len(unique_frames))
    for i, rb in enumerate(unique_rbs):
        for j, frame in enumerate(unique_frames):
            mask = (rbs == rb) & (frames == frame)
            if np.any(mask):
                heatmap_data[i, j] = np.mean(mags[mask])
                frame_times[j] = np.mean(timestamps[mask])
    fig, ax = plt.subplots(figsize=(16, 9))
    im = ax.imshow(heatmap_data, aspect='auto', cmap='viridis', origin='lower')
    ax.set_xlabel('Frame Index', fontsize=12); ax.set_ylabel('RB Index', fontsize=12)
    ax.set_xticks(np.linspace(0, len(unique_frames)-1, 11))
    ax.set_xticklabels([f"{int(unique_frames[int(i)])}" for i in np.linspace(0, len(unique_frames)-1, 11)])
    ax2 = ax.twiny()
    ax2.set_xlim(ax.get_xlim())
    time_positions = np.linspace(0, len(unique_frames)-1, 11)
    time_labels = [datetime.fromtimestamp(frame_times[int(pos)]).strftime('%H:%M:%S') if 0 <= pos < len(frame_times) else '' for pos in time_positions]
    ax2.set_xticks(time_positions); ax2.set_xticklabels(time_labels, rotation=45, ha='left'); ax2.set_xlabel('Time', fontsize=12)
    ax.set_title('Magnitude Heatmap: RB × Frame (with Time)')
    # Position colorbar further right
    cbar_ax = fig.add_axes([0.92, 0.15, 0.02, 0.7])
    plt.colorbar(im, cax=cbar_ax, label='Mean Magnitude')
    plt.tight_layout(rect=[0, 0, 0.9, 1])
    return fig

def plot_timeline(mags, phases, timestamps):
    if np.isnan(timestamps).all():
        fig, ax = plt.subplots(); ax.text(0.5, 0.5, 'No valid timestamps'); return fig
    t_min = np.nanmin(timestamps)
    times_sec = timestamps - t_min
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    if len(mags) > 50000:
        idx = np.random.choice(len(mags), 50000, replace=False)
        axes[0, 0].scatter(times_sec[idx], mags[idx], alpha=0.2, s=5, color='steelblue')
    else:
        axes[0, 0].scatter(times_sec, mags, alpha=0.3, s=10, color='steelblue')
    axes[0, 0].set_title('Magnitude vs Time'); axes[0, 0].set_xlabel('Time (s)'); axes[0, 0].set_ylabel('Magnitude'); axes[0, 0].grid(True, alpha=0.3)
    window = min(100, max(2, len(mags) // 100))
    if window > 1:
        ma = pd.Series(mags).rolling(window=window, min_periods=1).mean().values
        axes[0, 1].plot(times_sec, mags, 'o', alpha=0.1, markersize=1, color='gray', label='Raw')
        axes[0, 1].plot(times_sec, ma, linewidth=2, color='red', label=f'MA(w={window})')
        axes[0, 1].set_title('Moving Average'); axes[0, 1].set_xlabel('Time (s)'); axes[0, 1].set_ylabel('Magnitude'); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    if len(phases) > 10000:
        idx = np.random.choice(len(phases), 10000, replace=False)
        axes[1, 0].scatter(times_sec[idx], np.rad2deg(phases[idx]), alpha=0.3, s=5, color='orange')
    else:
        axes[1, 0].scatter(times_sec, np.rad2deg(phases), alpha=0.3, s=10, color='orange')
    axes[1, 0].set_title('Phase vs Time'); axes[1, 0].set_xlabel('Time (s)'); axes[1, 0].set_ylabel('Phase (°)'); axes[1, 0].grid(True, alpha=0.3)
    if len(mags) > 64:
        fft_mag = np.abs(np.fft.fft(mags[:min(100000, len(mags))]))[:len(mags)//4]
        dt = times_sec[1] - times_sec[0] if len(times_sec) > 1 and times_sec[1] != times_sec[0] else 1.0
        freqs = np.fft.fftfreq(min(100000, len(mags)), dt)[:len(mags)//4]
        axes[1, 1].semilogy(freqs, fft_mag, linewidth=1, color='steelblue')
        axes[1, 1].set_title('FFT'); axes[1, 1].set_xlabel('Frequency (Hz)'); axes[1, 1].set_ylabel('Power'); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

def plot_heatmap_phase_with_time(phases, rbs, frames, timestamps):
    unique_rbs = np.unique(rbs)[:64]
    unique_frames = np.unique(frames)[:200]
    heatmap_data = np.zeros((len(unique_rbs), len(unique_frames)))
    frame_times = np.zeros(len(unique_frames))
    for i, rb in enumerate(unique_rbs):
        for j, frame in enumerate(unique_frames):
            mask = (rbs == rb) & (frames == frame)
            if np.any(mask):
                heatmap_data[i, j] = np.mean(phases[mask])
                frame_times[j] = np.mean(timestamps[mask])
    fig, ax = plt.subplots(figsize=(16, 9))
    im = ax.imshow(heatmap_data, aspect='auto', cmap='hsv', origin='lower')
    ax.set_xlabel('Frame Index', fontsize=12); ax.set_ylabel('RB Index', fontsize=12)
    ax.set_xticks(np.linspace(0, len(unique_frames)-1, 11))
    ax.set_xticklabels([f"{int(unique_frames[int(i)])}" for i in np.linspace(0, len(unique_frames)-1, 11)])
    ax2 = ax.twiny()
    ax2.set_xlim(ax.get_xlim())
    time_positions = np.linspace(0, len(unique_frames)-1, 11)
    time_labels = [datetime.fromtimestamp(frame_times[int(pos)]).strftime('%H:%M:%S') if 0 <= pos < len(frame_times) else '' for pos in time_positions]
    ax2.set_xticks(time_positions); ax2.set_xticklabels(time_labels, rotation=45, ha='left'); ax2.set_xlabel('Time', fontsize=12)
    ax.set_title('Phase Heatmap: RB × Frame (with Time)')
    # Position colorbar further right
    cbar_ax = fig.add_axes([0.92, 0.15, 0.02, 0.7])
    plt.colorbar(im, cax=cbar_ax, label='Phase (rad)')
    plt.tight_layout(rect=[0, 0, 0.9, 1])
    return fig

def plot_multipath(mags, phases, rbs):
    unique_rbs = np.unique(rbs)
    spreads = np.array([np.std(phases[rbs == rb]) if np.sum(rbs == rb) > 1 else 0 for rb in unique_rbs])
    mag_means = np.array([np.mean(mags[rbs == rb]) for rb in unique_rbs])
    mag_stds = np.array([np.std(mags[rbs == rb]) if np.sum(rbs == rb) > 1 else 0 for rb in unique_rbs])
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].bar(unique_rbs, spreads, edgecolor='black', alpha=0.7, color='orange'); axes[0, 0].set_title('Phase Spread/RB'); axes[0, 0].set_xlabel('RB'); axes[0, 0].set_ylabel('Std (rad)'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    axes[0, 1].plot(unique_rbs, mag_means, 'o-', linewidth=2, markersize=4, color='steelblue'); axes[0, 1].fill_between(unique_rbs, mag_means - mag_stds, mag_means + mag_stds, alpha=0.2, color='steelblue'); axes[0, 1].set_title('Coherence BW'); axes[0, 1].set_xlabel('RB'); axes[0, 1].set_ylabel('Mean'); axes[0, 1].grid(True, alpha=0.3)
    axes[1, 0].text(0.5, 0.5, 'Multipath\nAnalysis', ha='center', va='center', fontsize=14, transform=axes[1, 0].transAxes); axes[1, 0].axis('off')
    axes[1, 1].text(0.5, 0.5, f'RMS Spread:\n{np.sqrt(np.mean(spreads**2)):.4f}', ha='center', va='center', fontsize=12, transform=axes[1, 1].transAxes); axes[1, 1].axis('off')
    plt.tight_layout()
    return fig

def plot_mimo(mags, slots, ants, ports, stats):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    slot_list = stats['slot_list']
    if len(slot_list) > 0:
        bp = axes[0, 0].boxplot([mags[slots == s] for s in slot_list], labels=[f'Slot {s}' for s in slot_list], patch_artist=True)
        for patch in bp['boxes']:
            patch.set_facecolor('steelblue')
        axes[0, 0].set_title('Per Slot'); axes[0, 0].set_ylabel('Magnitude'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    
    ant_list = stats['ant_list']
    ant_means = np.array([np.mean(mags[ants == a]) for a in ant_list])
    x_pos = np.arange(len(ant_list))
    axes[0, 1].bar(x_pos, ant_means, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 1].set_xticks(x_pos)
    axes[0, 1].set_xticklabels([f'Ant {a}' for a in ant_list])
    axes[0, 1].set_title('Per Antenna'); axes[0, 1].set_ylabel('Mean Magnitude'); axes[0, 1].grid(True, alpha=0.3, axis='y')
    
    port_list = stats['port_list']
    port_means = np.array([np.mean(mags[ports == p]) for p in port_list])
    x_pos = np.arange(len(port_list))
    axes[1, 0].bar(x_pos, port_means, edgecolor='black', alpha=0.7, color='green')
    axes[1, 0].set_xticks(x_pos)
    axes[1, 0].set_xticklabels([f'Port {p}' for p in port_list])
    axes[1, 0].set_title('Per Port'); axes[1, 0].set_ylabel('Mean Magnitude'); axes[1, 0].grid(True, alpha=0.3, axis='y')
    
    axes[1, 1].text(0.05, 0.95, 'MIMO Summary', fontsize=12, weight='bold', transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.85, f'Total: {len(mags):,} records', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.75, f'Mean: {np.mean(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.65, f'Std: {np.std(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.55, f'Min/Max: {np.min(mags):.1f} / {np.max(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].axis('off')
    
    plt.tight_layout()
    return fig

def plot_slot_signal_quality(mags, slots, stats):
    """Plot 1: Signal Quality per Slot (violin, SNR, PAPR, CDF)"""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    unique_slots = sorted(np.unique(slots).tolist())
    slot_mags = [mags[slots == s] for s in unique_slots]
    
    # Violin plot
    parts = axes[0, 0].violinplot(slot_mags, positions=range(len(unique_slots)), showmeans=True, showmedians=True)
    axes[0, 0].set_xticks(range(len(unique_slots)))
    axes[0, 0].set_xticklabels([f'Slot {s}' for s in unique_slots])
    axes[0, 0].set_title('Magnitude Distribution (Violin Plot)'); axes[0, 0].set_ylabel('Magnitude'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    
    # SNR (std/mean ratio) - lower is better
    snr_values = [np.std(m) / (np.mean(m) + 1e-6) for m in slot_mags]
    axes[0, 1].bar(range(len(unique_slots)), snr_values, color='orange', edgecolor='black', alpha=0.7)
    axes[0, 1].set_xticks(range(len(unique_slots)))
    axes[0, 1].set_xticklabels([f'Slot {s}' for s in unique_slots])
    axes[0, 1].set_title('Signal Variability (Std/Mean) - Lower is Better'); axes[0, 1].set_ylabel('SNR Ratio'); axes[0, 1].grid(True, alpha=0.3, axis='y')
    
    # PAPR (Peak-to-Average Power Ratio)
    papr_values = [np.max(m) / (np.mean(m) + 1e-6) for m in slot_mags]
    axes[1, 0].bar(range(len(unique_slots)), papr_values, color='red', edgecolor='black', alpha=0.7)
    axes[1, 0].set_xticks(range(len(unique_slots)))
    axes[1, 0].set_xticklabels([f'Slot {s}' for s in unique_slots])
    axes[1, 0].set_title('PAPR (Peak-to-Average) - High = Peaks/Outliers'); axes[1, 0].set_ylabel('PAPR'); axes[1, 0].grid(True, alpha=0.3, axis='y')
    
    # CDF comparison
    for i, slot in enumerate(unique_slots):
        sorted_mag = np.sort(slot_mags[i])
        cdf = np.arange(1, len(sorted_mag) + 1) / len(sorted_mag)
        axes[1, 1].plot(sorted_mag, cdf, linewidth=2, label=f'Slot {slot}', alpha=0.7)
    axes[1, 1].set_title('Cumulative Distribution (CDF)'); axes[1, 1].set_xlabel('Magnitude'); axes[1, 1].set_ylabel('Probability'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    return fig

def plot_slot_temporal_stability(mags, slots, frames, timestamps):
    """Plot 2: Temporal Stability per Slot (mean over time, variance trend)"""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    unique_slots = sorted(np.unique(slots).tolist())
    unique_frames = sorted(np.unique(frames))
    
    t_min = np.nanmin(timestamps)
    times_sec = timestamps - t_min
    
    # Mean magnitude per frame per slot
    for slot in unique_slots:
        mask_slot = slots == slot
        frame_means = np.array([np.mean(mags[mask_slot & (frames == f)]) for f in unique_frames])
        axes[0, 0].plot(unique_frames, frame_means, 'o-', linewidth=2, markersize=4, label=f'Slot {slot}', alpha=0.7)
    axes[0, 0].set_title('Mean Magnitude Evolution'); axes[0, 0].set_xlabel('Frame'); axes[0, 0].set_ylabel('Mean Magnitude'); axes[0, 0].legend(); axes[0, 0].grid(True, alpha=0.3)
    
    # Variance per frame per slot
    for slot in unique_slots:
        mask_slot = slots == slot
        frame_vars = np.array([np.var(mags[mask_slot & (frames == f)]) for f in unique_frames])
        axes[0, 1].plot(unique_frames, frame_vars, 'o-', linewidth=2, markersize=4, label=f'Slot {slot}', alpha=0.7)
    axes[0, 1].set_title('Variance Evolution'); axes[0, 1].set_xlabel('Frame'); axes[0, 1].set_ylabel('Variance'); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    
    # Temporal trend: split into first/middle/last thirds
    for slot in unique_slots:
        mask_slot = slots == slot
        time_slot = times_sec[mask_slot]
        mag_slot = mags[mask_slot]
        
        t_split = [np.min(time_slot), np.percentile(time_slot, 33), np.percentile(time_slot, 66), np.max(time_slot)]
        period_means = []
        for i in range(3):
            mask_period = (time_slot >= t_split[i]) & (time_slot < t_split[i+1])
            if np.sum(mask_period) > 0:
                period_means.append(np.mean(mag_slot[mask_period]))
        
        axes[1, 0].plot(['Early', 'Middle', 'Late'], period_means, 'o-', linewidth=2, markersize=8, label=f'Slot {slot}')
    axes[1, 0].set_title('Signal Level by Time Period'); axes[1, 0].set_ylabel('Mean Magnitude'); axes[1, 0].legend(); axes[1, 0].grid(True, alpha=0.3)
    
    # Stability metric: coefficient of variation over time
    cv_values = []
    for slot in unique_slots:
        mask_slot = slots == slot
        frame_means = np.array([np.mean(mags[mask_slot & (frames == f)]) for f in unique_frames])
        cv = np.std(frame_means) / (np.mean(frame_means) + 1e-6)
        cv_values.append(cv)
    
    axes[1, 1].bar(range(len(unique_slots)), cv_values, color='green', edgecolor='black', alpha=0.7)
    axes[1, 1].set_xticks(range(len(unique_slots)))
    axes[1, 1].set_xticklabels([f'Slot {s}' for s in unique_slots])
    axes[1, 1].set_title('Temporal Stability (CV of means) - Lower is Better'); axes[1, 1].set_ylabel('Coefficient of Variation'); axes[1, 1].grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    return fig

def plot_slot_rb_pattern(mags, slots, rbs):
    """Plot 3: RB Pattern per Slot (heatmap, distribution, anomalies)"""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    unique_slots = sorted(np.unique(slots).tolist())
    unique_rbs = sorted(np.unique(rbs).tolist())
    
    # Heatmap: RB pattern per slot
    heatmap_data = np.zeros((len(unique_slots), len(unique_rbs)))
    for i, slot in enumerate(unique_slots):
        for j, rb in enumerate(unique_rbs):
            mask = (slots == slot) & (rbs == rb)
            if np.sum(mask) > 0:
                heatmap_data[i, j] = np.mean(mags[mask])
    
    im = axes[0, 0].imshow(heatmap_data, aspect='auto', cmap='viridis', origin='lower')
    axes[0, 0].set_yticks(range(len(unique_slots)))
    axes[0, 0].set_yticklabels([f'Slot {s}' for s in unique_slots])
    axes[0, 0].set_xlabel('RB Index')
    axes[0, 0].set_title('Magnitude Pattern: Slot × RB')
    plt.colorbar(im, ax=axes[0, 0], label='Mean Magnitude')
    
    # Per-slot RB mean distribution
    for slot in unique_slots:
        mask_slot = slots == slot
        rb_means = np.array([np.mean(mags[mask_slot & (rbs == rb)]) for rb in unique_rbs])
        axes[0, 1].plot(unique_rbs, rb_means, 'o-', linewidth=1, markersize=3, label=f'Slot {slot}', alpha=0.7)
    axes[0, 1].set_title('Mean Magnitude per RB'); axes[0, 1].set_xlabel('RB'); axes[0, 1].set_ylabel('Mean'); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    
    # Dead RBs detection (very low or zero)
    dead_threshold = np.percentile(mags, 5)
    for slot in unique_slots:
        mask_slot = slots == slot
        rb_means = np.array([np.mean(mags[mask_slot & (rbs == rb)]) for rb in unique_rbs])
        dead_rbs = [unique_rbs[i] for i in range(len(unique_rbs)) if rb_means[i] <= dead_threshold]
        if len(dead_rbs) > 0:
            axes[1, 0].scatter([slot]*len(dead_rbs), dead_rbs, s=50, alpha=0.7, label=f'Slot {slot}' if len(dead_rbs) > 0 else '')
    axes[1, 0].set_xlabel('Slot'); axes[1, 0].set_ylabel('RB Index'); axes[1, 0].set_title('Dead/Weak RBs (Bottom 5%)'); axes[1, 0].grid(True, alpha=0.3)
    
    # RB variability per slot (which RBs are noisy)
    for slot in unique_slots:
        mask_slot = slots == slot
        rb_stds = np.array([np.std(mags[mask_slot & (rbs == rb)]) for rb in unique_rbs])
        axes[1, 1].plot(unique_rbs, rb_stds, 'o-', linewidth=1, markersize=3, label=f'Slot {slot}', alpha=0.7)
    axes[1, 1].set_title('RB Noise (Std Dev)'); axes[1, 1].set_xlabel('RB'); axes[1, 1].set_ylabel('Std Dev'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    return fig
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # By Slot
    slot_list = stats['slot_list']
    if len(slot_list) > 0:
        bp = axes[0, 0].boxplot([mags[slots == s] for s in slot_list], labels=[f'Slot {s}' for s in slot_list], patch_artist=True)
        for patch in bp['boxes']:
            patch.set_facecolor('steelblue')
        axes[0, 0].set_title('Per Slot'); axes[0, 0].set_ylabel('Magnitude'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    
    # By Antenna - FIXED
    ant_list = stats['ant_list']
    ant_means = np.array([np.mean(mags[ants == a]) for a in ant_list])
    x_pos = np.arange(len(ant_list))
    axes[0, 1].bar(x_pos, ant_means, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 1].set_xticks(x_pos)
    axes[0, 1].set_xticklabels([f'Ant {a}' for a in ant_list])
    axes[0, 1].set_title('Per Antenna'); axes[0, 1].set_ylabel('Mean Magnitude'); axes[0, 1].grid(True, alpha=0.3, axis='y')
    
    # By Port - FIXED
    port_list = stats['port_list']
    port_means = np.array([np.mean(mags[ports == p]) for p in port_list])
    x_pos = np.arange(len(port_list))
    axes[1, 0].bar(x_pos, port_means, edgecolor='black', alpha=0.7, color='green')
    axes[1, 0].set_xticks(x_pos)
    axes[1, 0].set_xticklabels([f'Port {p}' for p in port_list])
    axes[1, 0].set_title('Per Port'); axes[1, 0].set_ylabel('Mean Magnitude'); axes[1, 0].grid(True, alpha=0.3, axis='y')
    
    # Summary stats
    axes[1, 1].text(0.05, 0.95, 'MIMO Summary', fontsize=12, weight='bold', transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.85, f'Total: {len(mags):,} records', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.75, f'Mean: {np.mean(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.65, f'Std: {np.std(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.55, f'Min/Max: {np.min(mags):.1f} / {np.max(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].axis('off')
    
    plt.tight_layout()
    return fig

def generate_statistical_summary(parsed_data, mask, stats):
    """Analyze data to identify actual problems and anomalies"""
    mags = parsed_data['mags'][mask]
    slots = parsed_data['slots'][mask]
    ants = parsed_data['ants'][mask]
    rbs = parsed_data['rbs'][mask]
    rbtis = parsed_data['rbtis'][mask]
    
    summary = []
    summary.append("=" * 80)
    summary.append("STATISTICAL ANALYSIS - Identifying Problems")
    summary.append("=" * 80)
    
    # 1. SLOT ANALYSIS
    summary.append("\n📊 SLOT ANALYSIS:")
    slot_list = stats['slot_list']
    if len(slot_list) >= 2:
        slot_stats = {}
        for slot in slot_list:
            mask_slot = slots == slot
            mag_slot = mags[mask_slot]
            if len(mag_slot) == 0:  # Skip empty slots
                continue
            slot_stats[slot] = {
                'mean': np.mean(mag_slot),
                'std': np.std(mag_slot),
                'median': np.median(mag_slot),
                'count': len(mag_slot),
                'min': np.min(mag_slot),
                'max': np.max(mag_slot),
            }
        
        if len(slot_stats) == 0:
            summary.append(f"  No data for any slot")
        else:
            # Print all slots
            for slot in sorted(slot_stats.keys()):
                s = slot_stats[slot]
                summary.append(f"  Slot {slot}: mean={s['mean']:.1f}±{s['std']:.1f}, median={s['median']:.1f}, records={s['count']:,}")
            
            # Find significant differences
            means = [slot_stats[s]['mean'] for s in slot_stats.keys()]
            if len(means) >= 2 and len(set(means)) > 1:
                ratio = max(means) / (min(means) + 1e-6)
                if ratio > 2:
                    slot_max = list(slot_stats.keys())[np.argmax(means)]
                    slot_min = list(slot_stats.keys())[np.argmin(means)]
                    summary.append(f"  ⚠️ SIGNIFICANT DIFFERENCE: Slot {slot_max} is {ratio:.1f}x stronger than Slot {slot_min}")
                    summary.append(f"     → Investigate: Different UEs? Different transmit power? Channel conditions?")
                elif ratio > 1.3:
                    summary.append(f"  ℹ️ Notable variation between slots ({ratio:.1f}x)")
    else:
        summary.append(f"  Only 1 slot in data (Slot {slot_list[0]})")
    
    # 2. ANTENNA ANALYSIS
    summary.append("\n📡 ANTENNA ANALYSIS:")
    ant_list = stats['ant_list']
    if len(ant_list) >= 2:
        ant_stats = {}
        for ant in ant_list:
            mask_ant = ants == ant
            mag_ant = mags[mask_ant]
            if len(mag_ant) == 0:  # Skip empty antennas
                continue
            ant_stats[ant] = {
                'mean': np.mean(mag_ant),
                'std': np.std(mag_ant),
                'median': np.median(mag_ant),
                'count': len(mag_ant),
            }
        
        if len(ant_stats) == 0:
            summary.append(f"  No data for any antenna")
        else:
            for ant in sorted(ant_stats.keys()):
                a = ant_stats[ant]
                summary.append(f"  Ant {ant}: mean={a['mean']:.1f}±{a['std']:.1f}, records={a['count']:,}")
            
            if len(ant_stats) >= 2:
                means = [ant_stats[a]['mean'] for a in ant_stats.keys()]
                ratio = max(means) / (min(means) + 1e-6)
                
                if ratio > 5:
                    summary.append(f"  ❌ CRITICAL IMBALANCE: {ratio:.1f}x difference detected")
                    summary.append(f"     → FIX HARDWARE: Check antenna connection, cable loss, gain settings")
                elif ratio > 2:
                    summary.append(f"  ⚠️ ANTENNA MISMATCH: {ratio:.1f}x imbalance")
                    summary.append(f"     → Check: Antenna placement, cable length, feed network")
                else:
                    summary.append(f"  ✅ Antennas balanced ({ratio:.1f}x ratio)")
    else:
        summary.append(f"  Only 1 antenna in data")
    
    # 3. UE/RNTI ANALYSIS
    summary.append("\n🎯 UE (RNTI) ANALYSIS:")
    rnti_list = sorted(np.unique(rbtis).tolist())
    rnti_stats = {}
    for rnti in rnti_list:
        mask_rnti = rbtis == rnti
        mag_rnti = mags[mask_rnti]
        rnti_stats[rnti] = {
            'mean': np.mean(mag_rnti),
            'std': np.std(mag_rnti),
            'count': len(mag_rnti),
        }
    
    for rnti in rnti_list:
        r = rnti_stats[rnti]
        summary.append(f"  0x{rnti:04x}: mean={r['mean']:.1f}±{r['std']:.1f}, records={r['count']:,}")
    
    if len(rnti_list) >= 2:
        rnti_means = [rnti_stats[r]['mean'] for r in rnti_list]
        ratio = max(rnti_means) / (min(rnti_means) + 1e-6)
        if ratio > 2:
            summary.append(f"  ℹ️ UEs have different signal levels ({ratio:.1f}x)")
            summary.append(f"     → Expected if: Different distance, power control, interference")
    
    # 4. RB NOISE ANALYSIS
    summary.append("\n🔍 RB NOISE ANALYSIS:")
    unique_rbs = np.unique(rbs)
    rb_means = np.array([np.mean(mags[rbs == rb]) for rb in unique_rbs])
    rb_stds = np.array([np.std(mags[rbs == rb]) for rb in unique_rbs])
    rb_noise_ratio = rb_stds / (rb_means + 1e-6)
    
    threshold = np.mean(rb_noise_ratio) + 2*np.std(rb_noise_ratio)
    noisy_rbs = unique_rbs[rb_noise_ratio > threshold]
    
    if len(noisy_rbs) > 0:
        summary.append(f"  ⚠️ {len(noisy_rbs)} RBs with anomalous noise:")
        summary.append(f"     RBs: {', '.join(map(str, noisy_rbs[:10]))}" + ("..." if len(noisy_rbs) > 10 else ""))
        summary.append(f"     → Check: Interference, dead subcarriers, RX filter response")
    else:
        summary.append(f"  ✅ RB noise levels normal across all {len(unique_rbs)} RBs")
    
    # 5. OVERALL SIGNAL QUALITY
    summary.append("\n📈 OVERALL SIGNAL QUALITY:")
    summary.append(f"  Mean magnitude: {np.mean(mags):.1f}")
    summary.append(f"  Std deviation: {np.std(mags):.1f}")
    summary.append(f"  Dynamic range: {np.max(mags) - np.min(mags):.1f}")
    summary.append(f"  Median: {np.median(mags):.1f}")
    
    cv = np.std(mags) / (np.mean(mags) + 1e-6)
    if cv > 1.5:
        summary.append(f"  ⚠️ High variability (CV={cv:.2f}) - check fading/mobility")
    elif cv < 0.3:
        summary.append(f"  ✅ Stable signal (CV={cv:.2f})")
    
    summary.append("\n" + "=" * 80)
    return "\n".join(summary)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # By Slot
    slot_list = stats['slot_list']
    if len(slot_list) > 0:
        bp = axes[0, 0].boxplot([mags[slots == s] for s in slot_list], labels=[f'Slot {s}' for s in slot_list], patch_artist=True)
        for patch in bp['boxes']:
            patch.set_facecolor('steelblue')
        axes[0, 0].set_title('Per Slot'); axes[0, 0].set_ylabel('Magnitude'); axes[0, 0].grid(True, alpha=0.3, axis='y')
    
    # By Antenna - FIXED
    ant_list = stats['ant_list']
    ant_means = np.array([np.mean(mags[ants == a]) for a in ant_list])
    x_pos = np.arange(len(ant_list))
    axes[0, 1].bar(x_pos, ant_means, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 1].set_xticks(x_pos)
    axes[0, 1].set_xticklabels([f'Ant {a}' for a in ant_list])
    axes[0, 1].set_title('Per Antenna'); axes[0, 1].set_ylabel('Mean Magnitude'); axes[0, 1].grid(True, alpha=0.3, axis='y')
    
    # By Port - FIXED
    port_list = stats['port_list']
    port_means = np.array([np.mean(mags[ports == p]) for p in port_list])
    x_pos = np.arange(len(port_list))
    axes[1, 0].bar(x_pos, port_means, edgecolor='black', alpha=0.7, color='green')
    axes[1, 0].set_xticks(x_pos)
    axes[1, 0].set_xticklabels([f'Port {p}' for p in port_list])
    axes[1, 0].set_title('Per Port'); axes[1, 0].set_ylabel('Mean Magnitude'); axes[1, 0].grid(True, alpha=0.3, axis='y')
    
    # Summary stats
    axes[1, 1].text(0.05, 0.95, 'MIMO Summary', fontsize=12, weight='bold', transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.85, f'Total: {len(mags):,} records', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.75, f'Mean: {np.mean(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.65, f'Std: {np.std(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].text(0.05, 0.55, f'Min/Max: {np.min(mags):.1f} / {np.max(mags):.1f}', fontsize=10, transform=axes[1, 1].transAxes, va='top')
    axes[1, 1].axis('off')
    
    plt.tight_layout()
    return fig

# ===================== DIAGNOSTIC PLOTS =====================

def plot_antenna_comparison_diag(mags, ants, slot_display):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    unique_ants = sorted(np.unique(ants).tolist())
    ant_mags = [mags[ants == a] for a in unique_ants]
    bp = axes[0].boxplot(ant_mags, labels=[f'Ant {a}' for a in unique_ants], patch_artist=True)
    for patch in bp['boxes']:
        patch.set_facecolor('orange')
    axes[0].set_title(f'Magnitude by RX Antenna'); axes[0].set_ylabel('Magnitude'); axes[0].grid(True, alpha=0.3, axis='y')
    if len(unique_ants) >= 2:
        ant0_mag = mags[ants == unique_ants[0]]
        ant1_mag = mags[ants == unique_ants[1]]
        stat, pval = mannwhitneyu(ant0_mag, ant1_mag)
        axes[1].hist(ant0_mag, bins=50, alpha=0.5, label=f'Ant {unique_ants[0]}', color='blue')
        axes[1].hist(ant1_mag, bins=50, alpha=0.5, label=f'Ant {unique_ants[1]}', color='orange')
        axes[1].set_title(f'Distribution (Mann-Whitney p={pval:.4f})'); axes[1].set_xlabel('Magnitude'); axes[1].set_ylabel('Count'); axes[1].legend(); axes[1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

def plot_rb_noise_profile(mags, rbs, ants):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    unique_rbs = sorted(np.unique(rbs).tolist())
    rb_means = np.array([np.mean(mags[rbs == rb]) for rb in unique_rbs])
    rb_stds = np.array([np.std(mags[rbs == rb]) for rb in unique_rbs])
    axes[0, 0].plot(unique_rbs, rb_means, 'o-', linewidth=1, markersize=4, color='steelblue')
    axes[0, 0].axhline(np.mean(rb_means), color='r', linestyle='--', label='Mean')
    axes[0, 0].set_title('Mean Magnitude per RB'); axes[0, 0].set_xlabel('RB'); axes[0, 0].set_ylabel('Mean'); axes[0, 0].legend(); axes[0, 0].grid(True, alpha=0.3)
    axes[0, 1].plot(unique_rbs, rb_stds, 'o-', linewidth=1, markersize=4, color='orange')
    axes[0, 1].axhline(np.mean(rb_stds), color='r', linestyle='--', label='Mean')
    threshold = np.mean(rb_stds) + 2*np.std(rb_stds)
    axes[0, 1].axhline(threshold, color='red', linestyle=':', label='Anomaly threshold')
    noisy_rbs = np.where(rb_stds > threshold)[0]
    axes[0, 1].scatter(np.array(unique_rbs)[noisy_rbs], rb_stds[noisy_rbs], color='red', s=100, marker='x', label=f'Anomalies ({len(noisy_rbs)})')
    axes[0, 1].set_title('Std Dev per RB (Noise)'); axes[0, 1].set_xlabel('RB'); axes[0, 1].set_ylabel('Std Dev'); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    unique_ants = sorted(np.unique(ants).tolist())
    for ant in unique_ants:
        mask = ants == ant
        ant_rb_means = np.array([np.mean(mags[mask & (rbs == rb)]) for rb in unique_rbs])
        axes[1, 0].plot(unique_rbs, ant_rb_means, 'o-', linewidth=1, markersize=3, label=f'Ant {ant}', alpha=0.7)
    axes[1, 0].set_title('Mean per RB (per Antenna)'); axes[1, 0].set_xlabel('RB'); axes[1, 0].set_ylabel('Mean'); axes[1, 0].legend(); axes[1, 0].grid(True, alpha=0.3)
    rb_noise_ratio = rb_stds / (rb_means + 1e-6)
    axes[1, 1].plot(unique_rbs, rb_noise_ratio, 'o-', linewidth=1, markersize=4, color='red')
    axes[1, 1].axhline(np.mean(rb_noise_ratio), color='blue', linestyle='--', label='Mean')
    axes[1, 1].set_title('Noise Ratio (Std/Mean) per RB'); axes[1, 1].set_xlabel('RB'); axes[1, 1].set_ylabel('Ratio'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

def plot_time_anomalies(mags, timestamps):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    t_min = np.nanmin(timestamps)
    times_sec = timestamps - t_min
    idx_sample = np.random.choice(len(mags), min(50000, len(mags)), replace=False) if len(mags) > 50000 else np.arange(len(mags))
    axes[0, 0].scatter(times_sec[idx_sample], mags[idx_sample], alpha=0.2, s=5, color='steelblue')
    axes[0, 0].set_title('Magnitude vs Time'); axes[0, 0].set_xlabel('Time (s)'); axes[0, 0].set_ylabel('Magnitude'); axes[0, 0].grid(True, alpha=0.3)
    window = min(1000, max(100, len(mags)//100))
    rolling_mean = pd.Series(mags).rolling(window=window, min_periods=1).mean().values
    rolling_std = pd.Series(mags).rolling(window=window, min_periods=1).std().values
    axes[0, 1].plot(times_sec, rolling_mean, linewidth=1, color='blue', label='Mean')
    axes[0, 1].fill_between(times_sec, rolling_mean - rolling_std, rolling_mean + rolling_std, alpha=0.2, color='blue')
    axes[0, 1].set_title(f'Rolling Mean±Std (w={window})'); axes[0, 1].set_xlabel('Time (s)'); axes[0, 1].set_ylabel('Magnitude'); axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)
    q1, q3 = np.percentile(mags, 25), np.percentile(mags, 75)
    iqr = q3 - q1
    outlier_mask = (mags < q1 - 3*iqr) | (mags > q3 + 3*iqr)
    axes[1, 0].scatter(times_sec, mags, alpha=0.1, s=5, color='gray', label='Normal')
    axes[1, 0].scatter(times_sec[outlier_mask], mags[outlier_mask], alpha=0.5, s=20, color='red', label=f'Outliers ({np.sum(outlier_mask)})')
    axes[1, 0].set_title('Outlier Detection'); axes[1, 0].set_xlabel('Time (s)'); axes[1, 0].set_ylabel('Magnitude'); axes[1, 0].legend(); axes[1, 0].grid(True, alpha=0.3)
    axes[1, 1].hist(mags, bins=min(100, len(mags)//1000 + 10), edgecolor='black', alpha=0.7, color='steelblue')
    axes[1, 1].axvline(np.mean(mags), color='r', linestyle='--', linewidth=2, label=f'Mean={np.mean(mags):.1f}')
    axes[1, 1].axvline(np.median(mags), color='g', linestyle='--', linewidth=2, label=f'Median={np.median(mags):.1f}')
    axes[1, 1].set_title('Distribution'); axes[1, 1].set_xlabel('Magnitude'); axes[1, 1].set_ylabel('Count'); axes[1, 1].legend(); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

def plot_phase_analysis(phases, mags):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes[0, 0].hist(np.rad2deg(phases), bins=100, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 0].set_title('Phase Distribution'); axes[0, 0].set_xlabel('Phase (°)'); axes[0, 0].set_ylabel('Count'); axes[0, 0].grid(True, alpha=0.3)
    idx = np.random.choice(len(phases), min(10000, len(phases)), replace=False)
    axes[0, 1].scatter(mags[idx], np.rad2deg(phases[idx]), alpha=0.3, s=10, color='orange')
    axes[0, 1].set_title('Phase vs Magnitude'); axes[0, 1].set_xlabel('Magnitude'); axes[0, 1].set_ylabel('Phase (°)'); axes[0, 1].grid(True, alpha=0.3)
    phase_var = 1 - np.abs(np.mean(np.exp(1j*phases)))
    axes[1, 0].text(0.1, 0.7, 'Phase Quality', fontsize=14, weight='bold', transform=axes[1, 0].transAxes)
    axes[1, 0].text(0.1, 0.5, f'Circ. Variance: {phase_var:.4f}', fontsize=12, transform=axes[1, 0].transAxes)
    axes[1, 0].text(0.1, 0.3, f'Phase Spread: {np.std(phases):.4f} rad', fontsize=12, transform=axes[1, 0].transAxes)
    axes[1, 0].axis('off')
    axes[1, 1].scatter(np.real(np.exp(1j*phases))[:min(5000, len(phases))], np.imag(np.exp(1j*phases))[:min(5000, len(phases))], alpha=0.1, s=5)
    circle = plt.Circle((0, 0), 1, fill=False, color='r', linestyle='--')
    axes[1, 1].add_patch(circle)
    axes[1, 1].set_xlim(-1.5, 1.5); axes[1, 1].set_ylim(-1.5, 1.5); axes[1, 1].set_aspect('equal')
    axes[1, 1].set_title('Phase Space'); axes[1, 1].grid(True, alpha=0.3)
    plt.tight_layout()
    return fig

# ===================== UI =====================

st.markdown("# 📊 CSI Visualizer v6.0")
st.markdown("**Complete analysis: distributions, timeline, diagnostics**")

st.sidebar.header("⚙️ Configuration")
scs_mu = st.sidebar.selectbox("Numerology (µ)", [0, 1, 2, 3], format_func=lambda x: f"{Numerology.SCS_MAP[x]} kHz", index=1)
st.sidebar.divider()

uploaded_file = st.file_uploader("📂 Upload CSI CSV", type=['csv'])
if uploaded_file is None:
    st.info("Upload a CSI CSV file")
    st.stop()

with st.spinner("⚡ Parsing..."):
    parsed_data, stats = parse_csi(uploaded_file.read())
    if parsed_data is None:
        st.error("Parse failed")
        st.stop()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Records", f"{stats['total_records']:,}")
col2.metric("UEs", stats['num_ues'])
col3.metric("Duration", f"{stats['duration']:.1f}s")
col4.metric("Antennas", len(stats['ant_list']))
col5.metric("Slots", len(stats['slot_list']))

st.sidebar.header("🎛️ Filters")
rntis_list = [f"0x{r:04x}" for r in stats['rnti_list']]
rntis_selected = st.sidebar.multiselect("UE (RNTI)", rntis_list, default=[rntis_list[0]])
rntis_int = [int(r, 16) for r in rntis_selected]

slots_list = [f"Slot {s}" for s in stats['slot_list']]
slots_selected = st.sidebar.multiselect("Slots", slots_list, default=slots_list)
slots_int = [int(s.split()[1]) for s in slots_selected]

ants_list = [f"Ant {a}" for a in stats['ant_list']]
ants_selected = st.sidebar.multiselect("RX Antennas", ants_list, default=ants_list)
ants_int = [int(a.split()[1]) for a in ants_selected]

ports_list = [f"Port {p}" for p in stats['port_list']]
ports_selected = st.sidebar.multiselect("TX Ports", ports_list, default=ports_list)
ports_int = [int(p.split()[1]) for p in ports_selected]

rnti_mask = np.isin(parsed_data['rbtis'], rntis_int)
slot_mask = np.isin(parsed_data['slots'], slots_int)
ant_mask = np.isin(parsed_data['ants'], ants_int)
port_mask = np.isin(parsed_data['ports'], ports_int)
mask = rnti_mask & slot_mask & ant_mask & port_mask

mags_filt = parsed_data['mags'][mask]
phases_filt = parsed_data['phases'][mask]
slots_filt = parsed_data['slots'][mask]
ants_filt = parsed_data['ants'][mask]
rbs_filt = parsed_data['rbs'][mask]
frames_filt = parsed_data['frames'][mask]
ts_filt = parsed_data['timestamps'][mask]

st.sidebar.metric("Filtered", f"{len(mags_filt):,}")

gap_threshold = st.sidebar.slider("Gap threshold (s)", 0.1, 10.0, 1.0, 0.1)
gaps = detect_gaps(ts_filt, gap_threshold)
if gaps:
    st.sidebar.warning(f"⚠️ {len(gaps)} gaps")
else:
    st.sidebar.success("✅ No gaps")

if len(mags_filt) == 0:
    st.error("No data matches filters")
    st.stop()

tabs = st.tabs([
    "📋 Summary", "📈 Distribution", "📊 Per-RB", "🔥 Heatmap Mag", "⏱️ Timeline", "🔍 Phase Heat",
    "📡 Multipath", "🎯 MIMO", "🔬 Slot Comparison", "📡 Antenna Diag", "🔍 RB Noise",
    "⏱️ Time Anomalies", "🌀 Phase Analysis"
])

with tabs[0]:
    st.subheader("📋 Statistical Summary & Anomalies")
    summary_text = generate_statistical_summary(parsed_data, mask, stats)
    st.code(summary_text, language="text")

with tabs[1]:
    st.pyplot(plot_distribution(mags_filt), use_container_width=True)
with tabs[2]:
    st.pyplot(plot_per_rb(mags_filt, rbs_filt), use_container_width=True)
with tabs[3]:
    st.pyplot(plot_heatmap_mag_with_time(mags_filt, rbs_filt, frames_filt, ts_filt), use_container_width=True)
with tabs[4]:
    st.pyplot(plot_timeline(mags_filt, phases_filt, ts_filt), use_container_width=True)
with tabs[5]:
    st.pyplot(plot_heatmap_phase_with_time(phases_filt, rbs_filt, frames_filt, ts_filt), use_container_width=True)
with tabs[6]:
    st.pyplot(plot_multipath(mags_filt, phases_filt, rbs_filt), use_container_width=True)
with tabs[7]:
    st.pyplot(plot_mimo(mags_filt, slots_filt, ants_filt, parsed_data['ports'][mask], stats), use_container_width=True)
with tabs[8]:
    st.subheader("Signal Quality per Slot")
    st.pyplot(plot_slot_signal_quality(mags_filt, slots_filt, stats), use_container_width=True)
    
with tabs[8]:
    st.subheader("Temporal Stability per Slot")
    st.pyplot(plot_slot_temporal_stability(mags_filt, slots_filt, frames_filt, ts_filt), use_container_width=True)
    
with tabs[8]:
    st.subheader("RB Pattern per Slot")
    st.pyplot(plot_slot_rb_pattern(mags_filt, slots_filt, rbs_filt), use_container_width=True)
with tabs[9]:
    if len(np.unique(ants_filt)) > 1:
        st.pyplot(plot_antenna_comparison_diag(mags_filt, ants_filt, slots_selected[0] if slots_selected else "All"), use_container_width=True)
    else:
        st.info("Only one antenna in filtered data")
with tabs[10]:
    st.pyplot(plot_rb_noise_profile(mags_filt, rbs_filt, ants_filt), use_container_width=True)
with tabs[11]:
    st.pyplot(plot_time_anomalies(mags_filt, ts_filt), use_container_width=True)
with tabs[12]:
    st.pyplot(plot_phase_analysis(phases_filt, mags_filt), use_container_width=True)

if gaps:
    st.divider()
    st.subheader("Gaps")
    st.dataframe(pd.DataFrame(gaps), use_container_width=True)

st.markdown(f"**v6.0** | {len(mags_filt):,} records | UE: {', '.join(rntis_selected)} | Slots: {', '.join(slots_selected)}")

#!/usr/bin/env python3
"""
CSI Logger Data Parser and Visualizer for OAI - v1.0.1
Supports:
  OAI DL mode: CSV /data/csi/csi_per_rb.csv

Usage:
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv --output /tmp/plots
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv --realtime
"""

import csv
import sys
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import argparse
from collections import defaultdict
from datetime import datetime


# ─────────────────────────────────────────────
# OAI CSI CSV Parser
# ─────────────────────────────────────────────

class OAICSIRecord:
    """Single OAI CSI measurement record (CSV row)"""

    def __init__(self, row):
        try:
            self.frame = int(row['frame'])
            self.slot = int(row['slot'])
            self.rb = int(row['rb'])
            self.subcarrier = int(row['subcarrier'])
            self.real = int(row['real'])
            self.imag = int(row['imag'])

            # Compute magnitude and phase
            self.magnitude = np.sqrt(self.real**2 + self.imag**2)
            self.phase = np.arctan2(self.imag, self.real)
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid CSV row: {row} - {e}")


class OAICSIParser:
    """Parse OAI CSI CSV file with incremental reading"""

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.records = []
        self.rb_measurements = defaultdict(list)
        self.last_file_pos = 0

    def parse(self, incremental=False):
        """Parse CSV file. If incremental=True, only read new lines."""
        if not self.filepath.exists():
            print(f"ERROR: File not found: {self.filepath}")
            return False

        try:
            if incremental:
                return self._parse_incremental()
            else:
                return self._parse_full()
        except Exception as e:
            print(f"ERROR parsing CSV: {e}")
            return False

    def _parse_full(self):
        """Read entire file (for initial load)"""
        print(f"[OAI Parser] Reading {self.filepath.name}")
        self.records = []
        self.rb_measurements = defaultdict(list)

        with open(self.filepath, 'r') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                print("ERROR: Empty CSV or missing headers")
                return False

            for row_num, row in enumerate(reader, start=2):
                try:
                    rec = OAICSIRecord(row)
                    self.records.append(rec)
                    self.rb_measurements[rec.rb].append(rec.magnitude)
                except ValueError as e:
                    print(f"WARNING: Skipping row {row_num}: {e}")
                    continue

        if not self.records:
            print("ERROR: No valid records parsed")
            return False

        print(f"[OAI Parser] Parsed {len(self.records)} records")
        self.last_file_pos = self.filepath.stat().st_size
        return True

    def _parse_incremental(self):
        """Only read new lines since last read (for real-time mode)"""
        current_size = self.filepath.stat().st_size

        if current_size < self.last_file_pos:
            print("[OAI Parser] CSV was truncated, reloading from start")
            return self._parse_full()

        if current_size == self.last_file_pos:
            return True

        new_records = 0
        try:
            with open(self.filepath, 'r') as f:
                f.seek(self.last_file_pos)
                reader = csv.DictReader(f)

                if self.last_file_pos == 0:
                    next(reader, None)

                for row in reader:
                    try:
                        rec = OAICSIRecord(row)
                        self.records.append(rec)
                        self.rb_measurements[rec.rb].append(rec.magnitude)
                        new_records += 1
                    except ValueError as e:
                        print(f"WARNING: Skipping malformed row: {e}")
                        continue

            self.last_file_pos = current_size

            if new_records > 0:
                print(f"[OAI Parser] Added {new_records} new records (total: {len(self.records)})")

            return True

        except Exception as e:
            print(f"ERROR in incremental read: {e}")
            return False

    def get_statistics(self):
        if not self.records:
            print("No OAI records")
            return

        frames = set(r.frame for r in self.records)
        slots = set(r.slot for r in self.records)
        rbs = set(r.rb for r in self.records)
        magnitudes = [r.magnitude for r in self.records]
        phases = [r.phase for r in self.records]

        print("\n=== OAI CSI Statistics ===")
        print(f"Total records      : {len(self.records)}")
        print(f"Frames             : {len(frames)} (range: {min(frames)}-{max(frames)})")
        print(f"Slots              : {len(slots)} (range: {min(slots)}-{max(slots)})")
        print(f"RBs                : {len(rbs)} (range: {min(rbs)}-{max(rbs)})")
        print(f"Subcarriers/RB     : 12 (standard)")
        print(f"Magnitude          : min={min(magnitudes):.1f} max={max(magnitudes):.1f} "
              f"mean={np.mean(magnitudes):.1f}")
        print(f"Phase (rad)        : min={min(phases):.3f} max={max(phases):.3f} "
              f"mean={np.mean(phases):.3f}")


# ─────────────────────────────────────────────
# OAI CSI Visualizer
# ─────────────────────────────────────────────

class OAICSIVisualizer:
    """Visualize OAI CSI measurements"""

    def __init__(self, parser):
        self.parser = parser

    def plot_rb_magnitude_distribution(self):
        """Plot magnitude distribution across RBs"""
        if not self.parser.records:
            return None

        rbs = sorted(set(r.rb for r in self.parser.records))
        rb_mags = [np.mean(self.parser.rb_measurements[rb]) for rb in rbs]
        rb_stds = [np.std(self.parser.rb_measurements[rb]) for rb in rbs]

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.bar(rbs, rb_mags, yerr=rb_stds, capsize=3, alpha=0.7, color='steelblue')
        ax.set_xlabel('Resource Block (RB)')
        ax.set_ylabel('Average Magnitude')
        ax.set_title('CSI Magnitude Distribution per RB')
        ax.grid(True, alpha=0.3, axis='y')
        plt.tight_layout()
        return fig

    def plot_magnitude_heatmap(self):
        """Plot 2D heatmap: RB vs Slot"""
        if not self.parser.records:
            return None

        frames = sorted(set(r.frame for r in self.parser.records))
        slots = sorted(set(r.slot for r in self.parser.records))
        rbs = sorted(set(r.rb for r in self.parser.records))

        rb_idx_map = {rb: idx for idx, rb in enumerate(rbs)}
        slot_idx_map = {slot: idx for idx, slot in enumerate(slots)}

        heatmap = np.zeros((len(rbs), len(slots)))
        count_matrix = np.zeros((len(rbs), len(slots)))

        for rec in self.parser.records:
            rb_idx = rb_idx_map[rec.rb]
            slot_idx = slot_idx_map[rec.slot]
            heatmap[rb_idx, slot_idx] += rec.magnitude
            count_matrix[rb_idx, slot_idx] += 1

        with np.errstate(divide='ignore', invalid='ignore'):
            heatmap = np.where(count_matrix > 0, heatmap / count_matrix, 0)

        fig, ax = plt.subplots(figsize=(14, 6))
        im = ax.imshow(heatmap, cmap='jet', aspect='auto', origin='lower')
        ax.set_xlabel('Slot')
        ax.set_ylabel('Resource Block (RB)')
        ax.set_title('CSI Magnitude per RB over Time')
        ax.set_xticks(range(0, len(slots), max(1, len(slots)//10)))
        ax.set_xticklabels([str(slots[i]) for i in range(0, len(slots), max(1, len(slots)//10))])
        ax.set_yticks(range(0, len(rbs), max(1, len(rbs)//10)))
        ax.set_yticklabels([str(rbs[i]) for i in range(0, len(rbs), max(1, len(rbs)//10))])

        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label('Magnitude')
        plt.tight_layout()
        return fig

    def plot_constellation(self, rb_idx=None):
        """Plot constellation (I-Q) for specific RB"""
        if not self.parser.records:
            return None

        if rb_idx is None:
            rbs = sorted(set(r.rb for r in self.parser.records))
            rb_idx = rbs[0]

        recs = [r for r in self.parser.records if r.rb == rb_idx]
        if not recs:
            print(f"No records for RB {rb_idx}")
            return None

        i_vals = [r.real for r in recs]
        q_vals = [r.imag for r in recs]

        fig, ax = plt.subplots(figsize=(8, 8))
        ax.scatter(i_vals, q_vals, alpha=0.5, s=10, color='blue')
        ax.set_xlabel('I (Real)')
        ax.set_ylabel('Q (Imag)')
        ax.set_title(f'Constellation - RB {rb_idx}')
        ax.grid(True, alpha=0.3)
        ax.set_aspect('equal')

        max_mag = max(np.sqrt(i**2 + q**2) for i, q in zip(i_vals, q_vals))
        circle = plt.Circle((0, 0), max_mag * 0.9, fill=False, linestyle='--',
                           color='red', alpha=0.5)
        ax.add_patch(circle)

        plt.tight_layout()
        return fig

    def plot_magnitude_timeline(self, rb_idx=None):
        """Plot magnitude over time for specific RB"""
        if not self.parser.records:
            return None

        if rb_idx is None:
            rbs = sorted(set(r.rb for r in self.parser.records))
            rb_idx = rbs[0]

        recs = [r for r in self.parser.records if r.rb == rb_idx]
        if not recs:
            print(f"No records for RB {rb_idx}")
            return None

        recs_sorted = sorted(recs, key=lambda r: (r.frame, r.slot))

        indices = range(len(recs_sorted))
        mags = [r.magnitude for r in recs_sorted]

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(indices, mags, '.-', linewidth=1, markersize=3, color='steelblue')
        ax.axhline(y=np.mean(mags), color='red', linestyle='--', linewidth=1.5,
                  label=f'Mean={np.mean(mags):.1f}')
        ax.set_xlabel('Record Index')
        ax.set_ylabel('Magnitude')
        ax.set_title(f'CSI Magnitude Timeline - RB {rb_idx}')
        ax.grid(True, alpha=0.3)
        ax.legend()
        plt.tight_layout()
        return fig

    def plot_phase_timeline(self, rb_idx=None):
        """Plot phase over time for specific RB"""
        if not self.parser.records:
            return None

        if rb_idx is None:
            rbs = sorted(set(r.rb for r in self.parser.records))
            rb_idx = rbs[0]

        recs = [r for r in self.parser.records if r.rb == rb_idx]
        if not recs:
            print(f"No records for RB {rb_idx}")
            return None

        recs_sorted = sorted(recs, key=lambda r: (r.frame, r.slot))

        indices = range(len(recs_sorted))
        phases = [r.phase for r in recs_sorted]

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(indices, phases, '.-', linewidth=1, markersize=3, color='darkorange')
        ax.axhline(y=np.mean(phases), color='red', linestyle='--', linewidth=1.5,
                  label=f'Mean={np.mean(phases):.3f}')
        ax.set_xlabel('Record Index')
        ax.set_ylabel('Phase (radians)')
        ax.set_title(f'CSI Phase Timeline - RB {rb_idx}')
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-np.pi, np.pi)
        ax.legend()
        plt.tight_layout()
        return fig


# ─────────────────────────────────────────────
# Real-time Monitoring
# ─────────────────────────────────────────────

class RealtimeCSIMonitor:
    """Monitor and plot CSI in real-time with incremental file reading"""

    def __init__(self, filepath, refresh_interval=1000):
        self.filepath = Path(filepath)
        self.refresh_interval = refresh_interval
        self.parser = OAICSIParser(filepath)
        if not self.parser.parse(incremental=False):
            raise RuntimeError(f"Failed to load initial data from {filepath}")

    def update_data(self):
        """Check and load only new data from file"""
        return self.parser.parse(incremental=True)

    def run(self):
        """Start real-time monitoring"""
        print(f"[Monitor] Watching {self.filepath}")
        print(f"[Monitor] Initial records: {len(self.parser.records)}")
        print(f"[Monitor] Refresh interval: {self.refresh_interval}ms")
        print("[Monitor] Press Ctrl+C to stop")

        fig, axes = plt.subplots(2, 2, figsize=(14, 10))

        def animate(frame_num):
            if self.update_data():
                for ax in axes.flat:
                    ax.clear()

                if self.parser.records:
                    rbs = sorted(set(r.rb for r in self.parser.records))
                    rb_mags = [np.mean(self.parser.rb_measurements[rb]) for rb in rbs]
                    axes[0, 0].bar(rbs, rb_mags, alpha=0.7, color='steelblue')
                    axes[0, 0].set_title(f'Magnitude Distribution ({len(self.parser.records)} records)')
                    axes[0, 0].set_xlabel('RB')
                    axes[0, 0].set_ylabel('Avg Magnitude')

                    latest_slot = max(r.slot for r in self.parser.records)
                    slot_recs = [r for r in self.parser.records if r.slot == latest_slot]
                    rbs_slot = sorted(set(r.rb for r in slot_recs))
                    rb_mags_slot = [np.mean([rec.magnitude for rec in slot_recs if rec.rb == rb])
                                   for rb in rbs_slot]
                    axes[0, 1].bar(rbs_slot, rb_mags_slot, alpha=0.7, color='darkorange')
                    axes[0, 1].set_title(f'Latest Slot ({latest_slot})')
                    axes[0, 1].set_xlabel('RB')
                    axes[0, 1].set_ylabel('Magnitude')

                    phases = [r.phase for r in self.parser.records]
                    axes[1, 0].hist(phases, bins=50, alpha=0.7, color='green')
                    axes[1, 0].set_title('Phase Distribution')
                    axes[1, 0].set_xlabel('Phase (radians)')
                    axes[1, 0].set_ylabel('Count')

                    mags = [r.magnitude for r in self.parser.records]
                    axes[1, 1].hist(mags, bins=50, alpha=0.7, color='purple')
                    axes[1, 1].set_title('Magnitude Distribution')
                    axes[1, 1].set_xlabel('Magnitude')
                    axes[1, 1].set_ylabel('Count')

                    fig.suptitle(f'OAI CSI Real-time Monitor - {datetime.now().strftime("%H:%M:%S")}',
                               fontsize=12, fontweight='bold')

        ani = animation.FuncAnimation(fig, animate, interval=self.refresh_interval,
                                     repeat=True, blit=False)
        plt.tight_layout()
        plt.show()


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='OAI CSI Data Visualizer v1.0.1 — Per-RB CSV with incremental reading',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv --output /tmp/plots
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv --realtime
  python3 csi_visualizer_oai.py /data/csi/csi_per_rb.csv --stats
        """)

    parser.add_argument('csi_file', nargs='?', help='Path to OAI CSI CSV file')
    parser.add_argument('--realtime', action='store_true', help='Enable real-time monitoring mode')
    parser.add_argument('--output', help='Output directory for PNG files')
    parser.add_argument('--stats', action='store_true', help='Show statistics only')
    parser.add_argument('--rb', type=int, default=None, help='Specific RB to visualize')
    parser.add_argument('--refresh-rate', type=int, default=1000, help='Real-time refresh rate in ms')

    args = parser.parse_args()

    if not args.csi_file:
        parser.print_help()
        sys.exit(1)

    if args.realtime:
        try:
            monitor = RealtimeCSIMonitor(args.csi_file, refresh_interval=args.refresh_rate)
            monitor.run()
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        return

    oai_parser = OAICSIParser(args.csi_file)
    if not oai_parser.parse(incremental=False):
        sys.exit(1)

    oai_parser.get_statistics()
    if args.stats:
        return

    viz = OAICSIVisualizer(oai_parser)

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)

        plots = [
            ('oai_rb_magnitude_distribution.png', viz.plot_rb_magnitude_distribution()),
            ('oai_magnitude_heatmap.png', viz.plot_magnitude_heatmap()),
            ('oai_constellation_rb0.png', viz.plot_constellation(rb_idx=0)),
            ('oai_magnitude_timeline_rb0.png', viz.plot_magnitude_timeline(rb_idx=0)),
            ('oai_phase_timeline_rb0.png', viz.plot_phase_timeline(rb_idx=0)),
        ]

        for name, fig in plots:
            if fig:
                fig.savefig(out / name, dpi=100)
                plt.close(fig)
                print(f"[Visualizer] Saved {name}")

        print(f"[Visualizer] Done — plots saved to {out}")
    else:
        viz.plot_rb_magnitude_distribution()
        plt.show()
        viz.plot_magnitude_heatmap()
        plt.show()
        viz.plot_constellation()
        plt.show()
        viz.plot_magnitude_timeline()
        plt.show()
        viz.plot_phase_timeline()
        plt.show()


if __name__ == '__main__':
    main()

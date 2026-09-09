#!/usr/bin/env python3
"""Create a compact analysis notebook for UERANSIM churn runs."""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path


def md(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": textwrap.dedent(source).strip().splitlines(True),
    }


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": textwrap.dedent(source).strip().splitlines(True),
    }


def build_notebook() -> dict:
    cells = [
        md(
            """
            # UERANSIM Churn Analysis

            This notebook is generated inside one churn result directory. It
            loads the churn summaries, UE mapper samples, stage-correlation
            CSVs, timeline rows, and Prometheus overhead export when present.
            """
        ),
        code(
            r"""
            from pathlib import Path
            import json

            import pandas as pd
            import matplotlib.pyplot as plt

            RESULTS_DIR = Path(".").resolve()
            FIG_DIR = RESULTS_DIR / "notebook_figures"
            FIG_DIR.mkdir(exist_ok=True)

            plt.rcParams.update({
                "figure.figsize": (11, 4.5),
                "figure.dpi": 120,
                "savefig.dpi": 220,
                "axes.grid": True,
                "grid.alpha": 0.25,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "legend.frameon": False,
            })

            def savefig(name):
                plt.tight_layout()
                plt.savefig(FIG_DIR / f"{name}.png", bbox_inches="tight")

            def read_json(path):
                path = Path(path)
                return json.loads(path.read_text()) if path.exists() else {}

            def read_text(path):
                path = Path(path)
                return path.read_text(errors="replace") if path.exists() else ""

            def read_csv(name):
                path = RESULTS_DIR / name
                return pd.read_csv(path) if path.exists() else pd.DataFrame()

            print("Results directory:", RESULTS_DIR)
            print("Available files:")
            for path in sorted(RESULTS_DIR.glob("*")):
                print(" -", path.name)
            """
        ),
        md("## Run Metadata"),
        code(
            r"""
            metadata = {
                "experiment_metadata.json": read_json(RESULTS_DIR / "experiment_metadata.json"),
                "overhead_summary.json": read_json(RESULTS_DIR / "overhead_summary.json"),
                "run_metadata.yml": read_text(RESULTS_DIR / "run_metadata.yml"),
            }
            display(metadata)
            """
        ),
        md("## Timeline"),
        code(
            r"""
            timeline = read_csv("timeline_summary.csv")
            if timeline.empty:
                print("No timeline_summary.csv found.")
            else:
                for column in ["start_epoch", "end_epoch", "duration_s"]:
                    if column in timeline.columns:
                        timeline[column] = pd.to_numeric(timeline[column], errors="coerce")
                display(timeline)
            """
        ),
        md("## UE Mapper Samples"),
        code(
            r"""
            counts = read_csv("ue_mapper_inventory_counts.csv")
            samples = read_csv("ue_mapper_inventory_samples.csv")
            if counts.empty:
                print("No UE mapper count samples found.")
            else:
                counts["time"] = pd.to_datetime(counts["sample_epoch"], unit="s", utc=True, errors="coerce")
                display(counts.tail(20))
                ax = counts.plot(x="time", y="connected_ues", marker=".", title="UE mapper connected UE samples")
                ax.set_xlabel("time")
                ax.set_ylabel("connected UEs")
                savefig("ue_mapper_connected_ues")

            if not samples.empty:
                display(samples.head(20))
                display(samples.groupby(["sample_utc", "slice_id"], dropna=False).size().rename("rows").reset_index().tail(20))
            """
        ),
        md("## Churn Stage Correlation"),
        code(
            r"""
            stage_summary = read_csv("churn_stage_summary.csv")
            stage_correlation = read_csv("churn_stage_correlation.csv")
            diagnosis = read_text(RESULTS_DIR / "churn_diagnosis.md")

            if not stage_summary.empty:
                display(stage_summary)
                numeric_cols = [
                    col for col in stage_summary.columns
                    if col not in {"checkpoint", "status"} and pd.api.types.is_numeric_dtype(stage_summary[col])
                ]
                if numeric_cols:
                    ax = stage_summary.plot(x=stage_summary.columns[0], y=numeric_cols, kind="bar", title="Churn checkpoint totals")
                    ax.set_xlabel(stage_summary.columns[0])
                    ax.set_ylabel("count")
                    savefig("churn_stage_summary")
            else:
                print("No churn_stage_summary.csv found.")

            if not stage_correlation.empty:
                display(stage_correlation.head(50))

            if diagnosis:
                print(diagnosis[:4000])
            """
        ),
        md("## CPU And Memory Overhead"),
        code(
            r"""
            summaries = {
                "focused_overhead_summary.csv": read_csv("focused_overhead_summary.csv"),
                "ue_mapper_overhead_summary.csv": read_csv("ue_mapper_overhead_summary.csv"),
                "sniffer_overhead_summary.csv": read_csv("sniffer_overhead_summary.csv"),
                "overhead_summary.csv": read_csv("overhead_summary.csv"),
            }

            for name, frame in summaries.items():
                print(f"\n{name}")
                if frame.empty:
                    print("  missing or empty")
                    continue
                display(frame.sort_values(["query_name", "p95"], ascending=[True, False]).head(30))

            focused = summaries["focused_overhead_summary.csv"]
            if not focused.empty and {"component", "query_name", "p95"}.issubset(focused.columns):
                focused["p95"] = pd.to_numeric(focused["p95"], errors="coerce")
                plot_frame = (
                    focused.dropna(subset=["p95"])
                    .sort_values("p95", ascending=False)
                    .head(20)
                    .copy()
                )
                if not plot_frame.empty:
                    plot_frame["label"] = plot_frame["component"].astype(str) + " / " + plot_frame["query_name"].astype(str)
                    ax = plot_frame.iloc[::-1].plot.barh(x="label", y="p95", title="Top focused p95 overhead values")
                    ax.set_xlabel("p95 value")
                    ax.set_ylabel("")
                    savefig("focused_overhead_p95")
            """
        ),
        md("## Raw Prometheus Export"),
        code(
            r"""
            prom_path = RESULTS_DIR / "prometheus_timeseries.csv.gz"
            if not prom_path.exists():
                prom_path = RESULTS_DIR / "prometheus_timeseries.csv"

            if prom_path.exists():
                prom = pd.read_csv(prom_path)
                print("Prometheus rows:", len(prom))
                display(prom.groupby("query_name").size().rename("rows").sort_values(ascending=False).reset_index().head(50))
            else:
                print("No Prometheus CSV found.")
            """
        ),
    ]

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / "churn_analysis.ipynb"
    out.write_text(json.dumps(build_notebook(), indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

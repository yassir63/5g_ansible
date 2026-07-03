#!/usr/bin/env python3
"""Monitoring-driven UPF scale-out/scale-in demo controller.

This controller intentionally does not implement live UPF session migration.
It scales configured Kubernetes components and emits an explicit
ue_reestablishment_required event when new/re-established sessions should be
placed on the scaled-out path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def now_s() -> int:
    return int(time.time())


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "phase": "normal",
            "scaled_out": False,
            "below_since": None,
            "last_decision": None,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "phase": "normal",
            "scaled_out": False,
            "below_since": None,
            "last_decision": "state_file_unreadable_reset",
        }


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def prometheus_query(base_url: str, query: str, timeout_s: float) -> float | None:
    if not query:
        return None
    endpoint = base_url.rstrip("/") + "/api/v1/query"
    url = endpoint + "?" + urllib.parse.urlencode({"query": query})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus returned status={payload.get('status')}")
    results = payload.get("data", {}).get("result", [])
    if not results:
        return None
    values = []
    for result in results:
        try:
            values.append(float(result["value"][1]))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    if not values:
        return None
    return max(values)


def run_kubectl_scale(
    kubectl: str,
    namespace: str,
    target: str,
    replicas: int,
    apply: bool,
) -> dict[str, Any]:
    command = [
        kubectl,
        "-n",
        namespace,
        "scale",
        target,
        f"--replicas={replicas}",
    ]
    if not apply:
        return {"command": command, "rc": 0, "stdout": "dry-run", "stderr": ""}
    proc = subprocess.run(command, check=False, text=True, capture_output=True)
    return {
        "command": command,
        "rc": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def scale_targets(
    args: argparse.Namespace,
    targets: list[str],
    replicas: int,
) -> list[dict[str, Any]]:
    results = []
    for target in targets:
        results.append(
            run_kubectl_scale(
                kubectl=args.kubectl,
                namespace=args.namespace,
                target=target,
                replicas=replicas,
                apply=args.apply,
            )
        )
    failures = [result for result in results if result["rc"] != 0]
    if failures:
        raise RuntimeError(f"kubectl scale failed: {failures}")
    return results


def split_targets(values: list[str]) -> list[str]:
    targets: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                targets.append(part)
    return targets


def threshold_crossed(value: float | None, threshold: float | None) -> bool:
    return value is not None and threshold is not None and value > threshold


def threshold_below(value: float | None, threshold: float | None) -> bool:
    return value is not None and threshold is not None and value < threshold


def scale_in_allowed(args: argparse.Namespace, secondary_sessions: float | None) -> bool:
    if secondary_sessions is None:
        return args.allow_scale_in_without_session_query
    return secondary_sessions <= args.secondary_session_drain_threshold


def controller_iteration(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    timestamp = now_s()
    cpu = prometheus_query(args.prometheus_url, args.cpu_query, args.query_timeout)
    latency = prometheus_query(args.prometheus_url, args.latency_query, args.query_timeout)
    secondary_sessions = prometheus_query(
        args.prometheus_url,
        args.secondary_session_query,
        args.query_timeout,
    )

    overload = threshold_crossed(cpu, args.cpu_scale_out_threshold) or threshold_crossed(
        latency, args.latency_scale_out_threshold_ms
    )
    below = threshold_below(cpu, args.cpu_scale_in_threshold) and (
        not args.latency_query
        or threshold_below(latency, args.latency_scale_in_threshold_ms)
    )

    event: dict[str, Any] = {
        "ts": timestamp,
        "phase": state.get("phase", "normal"),
        "cpu": cpu,
        "latency_ms": latency,
        "secondary_sessions": secondary_sessions,
        "overload": overload,
        "below_scale_in_thresholds": below,
        "apply": args.apply,
    }

    if overload and not state.get("scaled_out", False):
        scale_results = scale_targets(args, args.scale_targets, args.scale_out_replicas)
        state.update(
            {
                "phase": "scaled_out",
                "scaled_out": True,
                "below_since": None,
                "last_decision": "scale_out",
                "last_decision_ts": timestamp,
            }
        )
        event.update(
            {
                "decision": "scale_out",
                "scale_results": scale_results,
                "ue_reestablishment_required": True,
                "message": (
                    "Secondary UPF path scaled out. Reconnect selected UEs or "
                    "release/re-establish PDU sessions to place new sessions there."
                ),
            }
        )
        return event

    if state.get("scaled_out", False):
        if below:
            if state.get("below_since") is None:
                state["below_since"] = timestamp
            stable_for = timestamp - int(state["below_since"])
            event["stable_for_s"] = stable_for
            if stable_for >= args.scale_in_stable_seconds:
                if scale_in_allowed(args, secondary_sessions):
                    scale_results = scale_targets(args, args.scale_targets, args.scale_in_replicas)
                    state.update(
                        {
                            "phase": "normal",
                            "scaled_out": False,
                            "below_since": None,
                            "last_decision": "scale_in",
                            "last_decision_ts": timestamp,
                        }
                    )
                    event.update({"decision": "scale_in", "scale_results": scale_results})
                else:
                    state["phase"] = "draining"
                    event.update(
                        {
                            "decision": "wait_for_secondary_drain",
                            "message": "Scale-in blocked because secondary sessions are still present or unknown.",
                        }
                    )
            else:
                event["decision"] = "wait_for_hysteresis"
        else:
            state["below_since"] = None
            state["phase"] = "scaled_out"
            event["decision"] = "hold_scaled_out"
    else:
        state["phase"] = "normal"
        event["decision"] = "hold_normal"

    return event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", default="http://localhost:30095")
    parser.add_argument("--namespace", default="open5gs")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--apply", action="store_true", help="Actually run kubectl scale commands.")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--iterations", type=int, default=12, help="0 means run forever.")
    parser.add_argument("--query-timeout", type=float, default=5.0)
    parser.add_argument("--state-file", default="/tmp/upf-scaling-demo/state.json")
    parser.add_argument("--events-file", default="/tmp/upf-scaling-demo/events.jsonl")
    parser.add_argument(
        "--scale-target",
        action="append",
        default=[],
        help="Kubernetes target to scale, e.g. deployment/upf2. Can be repeated or comma-separated.",
    )
    parser.add_argument("--scale-out-replicas", type=int, default=1)
    parser.add_argument("--scale-in-replicas", type=int, default=0)
    parser.add_argument(
        "--cpu-query",
        default=(
            'sum(rate(container_cpu_usage_seconds_total{namespace="open5gs",'
            'pod=~".*upf1.*",container!="POD",container!=""}[30s]))'
        ),
    )
    parser.add_argument("--latency-query", default="")
    parser.add_argument("--secondary-session-query", default="")
    parser.add_argument("--cpu-scale-out-threshold", type=float, default=0.8)
    parser.add_argument("--cpu-scale-in-threshold", type=float, default=0.4)
    parser.add_argument("--latency-scale-out-threshold-ms", type=float, default=None)
    parser.add_argument("--latency-scale-in-threshold-ms", type=float, default=None)
    parser.add_argument("--scale-in-stable-seconds", type=int, default=60)
    parser.add_argument("--secondary-session-drain-threshold", type=float, default=0)
    parser.add_argument(
        "--allow-scale-in-without-session-query",
        action="store_true",
        help="Allow scale-in even if no secondary session query is configured.",
    )
    args = parser.parse_args()
    args.scale_targets = split_targets(args.scale_target) or ["deployment/upf2", "deployment/smf2"]
    return args


def main() -> int:
    args = parse_args()
    state_file = Path(args.state_file)
    events_file = Path(args.events_file)
    state = load_state(state_file)
    iteration = 0

    while True:
        iteration += 1
        try:
            event = controller_iteration(args, state)
        except Exception as exc:  # noqa: BLE001 - controller should log and continue.
            event = {
                "ts": now_s(),
                "decision": "error",
                "error": str(exc),
                "apply": args.apply,
            }
        save_state(state_file, state)
        append_event(events_file, event)
        print(json.dumps(event, sort_keys=True), flush=True)

        if args.iterations and iteration >= args.iterations:
            break
        time.sleep(args.poll_interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())

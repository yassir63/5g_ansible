#!/usr/bin/env python3
"""Print a bounded, read-only host or Kubernetes setup snapshot as JSON."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time


def command(argv, timeout=15):
    started = time.time()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        data = result.stdout
        if result.returncode == 0:
            try:
                data = json.loads(data)
            except ValueError:
                pass
        return {"status": "ok" if result.returncode == 0 else "error", "started_at": started,
                "finished_at": time.time(), "rc": result.returncode,
                "data": data, "error": result.stderr[:2000]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "started_at": started, "finished_at": time.time(), "error": str(exc)}


def pod_summary(pod):
    spec, status = pod.get("spec", {}), pod.get("status", {})
    meta = pod.get("metadata", {})
    return {
        "namespace": meta.get("namespace"), "name": meta.get("name"), "uid": meta.get("uid"),
        "labels": meta.get("labels", {}), "node": spec.get("nodeName"),
        "node_selector": spec.get("nodeSelector", {}), "host_network": spec.get("hostNetwork", False),
        "pod_ip": status.get("podIP"), "phase": status.get("phase"),
        "network_status": meta.get("annotations", {}).get("k8s.v1.cni.cncf.io/network-status"),
        "networks_requested": meta.get("annotations", {}).get("k8s.v1.cni.cncf.io/networks"),
        "containers": [
            {"kind": kind, "name": c.get("name"), "image": c.get("image"),
             "resources": c.get("resources", {}), "ports": c.get("ports", []),
             "volume_mounts": c.get("volumeMounts", [])}
            for kind in ("containers", "initContainers", "ephemeralContainers") for c in spec.get(kind, [])
        ],
        "container_statuses": [
            {key: c.get(key) for key in ("name", "imageID", "containerID", "ready", "restartCount")}
            for kind in ("containerStatuses", "initContainerStatuses", "ephemeralContainerStatuses")
            for c in status.get(kind, [])
        ],
        "configmaps": [v["configMap"]["name"] for v in spec.get("volumes", []) if "configMap" in v],
    }


def cluster_snapshot(kubeconfig):
    base = ["kubectl", "--request-timeout=15s"]
    if kubeconfig != "auto":
        base += ["--kubeconfig", kubeconfig]
    elif not os.environ.get("KUBECONFIG"):
        for path in (Path.home() / ".kube/config", Path("/root/.kube/config"), Path("/etc/kubernetes/admin.conf")):
            if path.is_file() and os.access(path, os.R_OK):
                base += ["--kubeconfig", str(path)]
                break
    result = {"version": command(base + ["version", "-o", "json"], 20)}
    nodes = command(base + ["get", "nodes", "-o", "json"], 20)
    if nodes["status"] == "ok":
        nodes["data"] = [{"name": n["metadata"]["name"], "uid": n["metadata"]["uid"],
                          "capacity": n.get("status", {}).get("capacity", {}),
                          "allocatable": n.get("status", {}).get("allocatable", {}),
                          "node_info": n.get("status", {}).get("nodeInfo", {}),
                          "addresses": n.get("status", {}).get("addresses", [])}
                         for n in nodes["data"]["items"]]
    result["nodes"] = nodes
    pods = command(base + ["get", "pods", "-A", "-o", "json"], 20)
    if pods["status"] == "ok":
        pods["data"] = [pod_summary(p) for p in pods["data"]["items"]]
    result["pods"] = pods
    services = command(base + ["get", "services", "-A", "-o", "json"], 20)
    if services["status"] == "ok":
        services["data"] = [{"name": s["metadata"]["name"], "namespace": s["metadata"]["namespace"],
                             "selector": s.get("spec", {}).get("selector", {}),
                             "ports": s.get("spec", {}).get("ports", [])} for s in services["data"]["items"]]
    result["services"] = services
    # Fingerprint mounted configuration without archiving credentials embedded in it.
    configs = command(base + ["get", "configmaps", "-A", "-o", "json"], 20)
    if configs["status"] == "ok":
        configs["data"] = [{"namespace": c["metadata"]["namespace"], "name": c["metadata"]["name"],
                            "sha256": hashlib.sha256(json.dumps({k: c.get(k, {}) for k in ("data", "binaryData")},
                                                               sort_keys=True).encode()).hexdigest()}
                           for c in configs["data"]["items"]]
    result["configmap_fingerprints"] = configs
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("host", "cluster"), required=True)
    parser.add_argument("--kubeconfig", default="auto")
    args = parser.parse_args()
    started = time.time()
    if args.mode == "host":
        data = {"hostname": platform.node(), "kernel": platform.release(), "architecture": platform.machine()}
        for name, argv in {"cpu": ["lscpu", "-J"], "memory": ["free", "-b"],
                           "interfaces": ["ip", "-j", "address"], "routes": ["ip", "-j", "route"],
                           "clock": ["timedatectl", "show"], "os": ["cat", "/etc/os-release"]}.items():
            data[name] = command(argv)
    else:
        data = cluster_snapshot(args.kubeconfig)
    print(json.dumps({"started_at": started, "finished_at": time.time(), "mode": args.mode, "data": data}))


if __name__ == "__main__":
    main()

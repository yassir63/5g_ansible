import kopf
import kubernetes
import json
import os
import logging
import subprocess
import hashlib
import requests

# -------------------------
# Config
# -------------------------

# NAMESPACE = os.getenv("CORE_NS", "default")  # fallback default
NAMESPACE = "oai"  # to be changed and made dynamic
ALERT_FILE = "/tmp/alert.json"  # kept for later (currently unused)

# DRY RUN: if set, do not kill or inject; only log what would happen
DRY_RUN = 0

# NEW: ue-mapper config
# UE_MAPPER_URL = os.getenv("UE_MAPPER_URL", "http://ue-mapper-api.oai.svc.cluster.local")
UE_MAPPER_URL = os.getenv("UE_MAPPER_URL", "http://10.244.0.18:8080")
UE_MAPPER_LIMIT = int(os.getenv("UE_MAPPER_LIMIT", "2000"))  # max UEs returned
PROBE_IMAGE = os.getenv("PROBE_IMAGE", "r2labuser/ebpf-latency-probe:2026")

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    print("✅ KOPF startup hook triggered")
    try:
        kubernetes.config.load_incluster_config()
    except:
        kubernetes.config.load_kube_config()

    settings.posting.level = logging.INFO
    settings.watching.namespaces = [NAMESPACE]


# -------------------------
# UE-MAPPER helpers
# -------------------------

def fingerprint_teids(teids: str) -> str:
    """Short stable fingerprint of TEIDS string."""
    if not teids:
        return ""
    return hashlib.sha256(teids.encode()).hexdigest()[:12]


def fetch_all_teids_from_ue_mapper(logger) -> tuple[str, str]:
    """
    Returns (teids_string, fingerprint).
    Uses /inventory/ues and joins each ue["teid_args"].
    """
    url = f"{UE_MAPPER_URL}/inventory/ues"
    try:
        r = requests.get(url, params={"limit": UE_MAPPER_LIMIT}, timeout=3)
        r.raise_for_status()
        j = r.json()
    except Exception as e:
        logger.error(f"❌ Failed to fetch UE inventory from ue-mapper ({url}): {e}")
        return "", ""

    ues = j.get("ues", []) or []
    teids = []
    for ue in ues:
        arg = (ue.get("teid_args") or "").strip()
        if arg:
            teids.append(arg)

    # Dedup + stable ordering
    teids = sorted(set(teids))
    teids_str = " ".join(teids).strip()
    return teids_str, fingerprint_teids(teids_str)


# -------------------------
# Timer reconcile (now always-on)
# -------------------------

@kopf.timer('v1', 'pods', interval=15.0)
def reconcile_probe_always_on(name, namespace, labels, logger, **kwargs):
    logger.info(f"⏱ Timer running for pod: {name} (labels: {labels})")

    if labels.get("app") != "oai-gnb":
        logger.info(f"⛔ Skipping pod {name}: app label is not 'oai-gnb' (got: {labels.get('app')})")
        return

    # Always compute TEIDS for ALL UEs, ALL slices
    teids, teids_fp = fetch_all_teids_from_ue_mapper(logger)
    if not teids:
        logger.warning("⚠️ UE-mapper returned no TEIDS yet. Will not inject probe.")
        # Optional: if you want "no UEs => no probe", you could kill existing probe here.
        # In DRY_RUN we never kill anyway.
        return

    logger.info(f"📌 Desired TEIDS fingerprint: {teids_fp} (len={len(teids)})")
    logger.info(f"📌 Desired TEIDS string: {teids}")

    # If already running with same fingerprint, do nothing
    if probe_running_with_fingerprint(name, namespace, teids_fp, logger):
        logger.info("✅ Probe already running with matching TEIDS. No action.")
        return

    logger.info("🔁 TEIDS changed or no healthy probe. Refreshing probe...")

    # --- DRY RUN BEHAVIOR ---
    if DRY_RUN:
        logger.warning("🟡 DRY_RUN=1 -> Will NOT kill or inject. Printing what would happen.")
        _ = kill_probe_container(name, logger)  # will only LOG targets; no exec in DRY_RUN
        dump_injection_request(name, namespace, logger, teids, teids_fp)
        return

    # --- REAL BEHAVIOR ---
    kill_probe_container(name, logger)
    inject_ephemeral_probe(name, namespace, logger, teids, teids_fp)


# -------------------------
# Kubernetes helpers
# -------------------------

from kubernetes.client.rest import ApiException
from kubernetes.client import ApiClient


def probe_running_with_fingerprint(pod_name, namespace, desired_fp: str, logger) -> bool:
    """
    Return True if there is an existing probe container:
      - name starts with ebpf-latency-probe
      - status is running (not terminated)
      - env TEIDS_FP matches desired_fp
    """
    try:
        core_api = kubernetes.client.CoreV1Api()
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        logger.warning(f"⚠️ Failed to read pod {pod_name}: {e}")
        return False

    ecs = pod.spec.ephemeral_containers or []
    statuses = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

    for c in ecs:
        if not c.name.startswith("ebpf-latency-probe"):
            continue

        # Check env TEIDS_FP
        fp = ""
        try:
            for env in (c.env or []):
                if env.name == "TEIDS_FP":
                    fp = env.value or ""
                    break
        except Exception:
            fp = ""

        if fp != desired_fp:
            continue

        st = statuses.get(c.name)
        if st and st.state:
            if st.state.running:
                return True
            if st.state.terminated:
                continue

    return False


def kill_probe_container(pod_name, logger):
    """
    Safer kill:
      - Only attempts kubectl exec if pod is Running
      - Targets only ephemeral containers named ebpf-latency-probe*
      - In DRY_RUN: does not exec, only logs what it would kill
    Returns True if kill was "possible" (pod running) and attempted; False otherwise.
    """
    namespace = NAMESPACE
    core_api = kubernetes.client.CoreV1Api()

    try:
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        logger.warning(f"⚠️ Failed to read pod {pod_name}: {e}")
        return False

    phase = (pod.status.phase or "")
    if phase != "Running":
        logger.info(f"⛔ Not killing probes in {pod_name}: pod phase is {phase}")
        return False

    ephemerals = pod.spec.ephemeral_containers or []
    target_names = [c.name for c in ephemerals if c.name.startswith("ebpf-latency-probe")]
    if not target_names:
        logger.info(f"ℹ️ No ephemeral probe containers to kill in {pod_name}")
        return True

    # Optional: only try to kill ones that are actually running
    running = set()
    for st in (pod.status.ephemeral_container_statuses or []):
        if st.name in target_names and st.state and st.state.running:
            running.add(st.name)

    if running:
        target_names = [n for n in target_names if n in running]
    else:
        logger.info(f"ℹ️ No probe statuses reported running in {pod_name}; will attempt kill anyway (unless DRY_RUN).")

    logger.info(f"🧹 Kill targets in {pod_name}: {target_names}")

    if DRY_RUN:
        logger.warning("🟡 DRY_RUN=1 -> Skipping kubectl exec kill commands.")
        return True

    ok = True
    for cname in target_names:
        command = [
            "kubectl", "exec", "-n", namespace, pod_name,
            "-c", cname, "--",
            "/bin/sh", "-c",
            "pkill -f entrypoint-latency.sh || true; pkill -f gtp_latency_user || true"
        ]
        try:
            subprocess.run(command, check=True)
            logger.info(f"🧹 Killed probe container {cname} in pod {pod_name}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"⚠️ Could not kill container {cname} in pod {pod_name}: {e}")
            ok = False

    return ok


def compute_next_probe_name(existing_ecs) -> str:
    """
    Returns next available probe name: ebpf-latency-probe, ebpf-latency-probe2, ...
    """
    probe_names = [c.name for c in existing_ecs if c.name.startswith("ebpf-latency-probe")]
    i = 1
    base_name = "ebpf-latency-probe"
    while True:
        cname = base_name if i == 1 else f"{base_name}{i}"
        if cname not in probe_names:
            return cname
        i += 1


def build_ephemeral_patch_body(pod_name, namespace, logger, teids: str, teids_fp: str):
    """
    Build (cname, patch_body) exactly like inject_ephemeral_probe would.
    """
    core_api = kubernetes.client.CoreV1Api()

    pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    existing_ecs = pod.spec.ephemeral_containers or []

    cname = compute_next_probe_name(existing_ecs)
    logger.info(f"🚀 Would inject probe: {cname} (TEIDS_FP={teids_fp})")

    new_container = {
        "name": cname,
        "image": PROBE_IMAGE,
        "command": ["./entrypoint-latency.sh"],
        "env": [
            {"name": "IFACE", "value": "n2"},
            {"name": "HANDLE", "value": "9"},
            {"name": "PRIO", "value": "9"},
            {"name": "TEIDS", "value": teids},
            {"name": "TEIDS_FP", "value": teids_fp},
            {"name": "EXPORTER_PORT", "value": "9100"},
            {"name": "EXPORTER_PATH", "value": "/latency"},
        ],
        "stdin": True,
        "tty": True,
        "targetContainerName": "gnb",
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["SYS_ADMIN", "SYS_RESOURCE", "NET_ADMIN"]}
        },
    }

    updated_containers = [ec.to_dict() for ec in existing_ecs] + [new_container]

    patch_body = {
        "metadata": {"name": pod_name},
        "spec": {"ephemeralContainers": updated_containers}
    }

    return cname, patch_body


def dump_injection_request(pod_name, namespace, logger, teids: str, teids_fp: str):
    """
    DRY-RUN helper: dumps the exact API endpoint and patch payload we would use.
    """
    try:
        cname, patch_body = build_ephemeral_patch_body(pod_name, namespace, logger, teids, teids_fp)
    except Exception as e:
        logger.error(f"❌ Could not build patch body for injection: {e}")
        return

    endpoint = f"/api/v1/namespaces/{namespace}/pods/{pod_name}/ephemeralcontainers"
    logger.warning("🟡 DRY RUN injection dump:")
    logger.warning(f"  METHOD: PATCH")
    logger.warning(f"  URL:    {endpoint}")
    logger.warning(f"  HEADER: Content-Type=application/strategic-merge-patch+json")
    logger.warning(f"  Would inject container name: {cname}")
    logger.warning("  PATCH BODY JSON:")
    logger.warning(json.dumps(patch_body, indent=2))


def inject_ephemeral_probe(pod_name, namespace, logger, teids: str, teids_fp: str):
    """
    REAL injection (disabled automatically in DRY_RUN by caller).
    """
    core_api = kubernetes.client.CoreV1Api()
    api_client = kubernetes.client.ApiClient()

    try:
        cname, patch_body = build_ephemeral_patch_body(pod_name, namespace, logger, teids, teids_fp)
    except Exception as e:
        logger.error(f"❌ Failed to build injection payload: {e}")
        return

    try:
        api_client.call_api(
            f"/api/v1/namespaces/{namespace}/pods/{pod_name}/ephemeralcontainers",
            "PATCH",
            body=patch_body,
            auth_settings=["BearerToken"],
            header_params={"Content-Type": "application/strategic-merge-patch+json"},
            response_type="object",
            _preload_content=False
        )
        logger.info(f"✅ Successfully injected {cname} into {pod_name}")
    except Exception as e:
        logger.error(f"❌ Failed to inject probe {cname}: {e}")

# import kopf
# import kubernetes
# import json
# import os
# import logging
# import subprocess

# NAMESPACE = "oail1"
# ALERT_FILE = "/tmp/alert.json"

# @kopf.on.startup()
# def configure(settings: kopf.OperatorSettings, **_):
#     print("✅ KOPF startup hook triggered")
#     try:
#         kubernetes.config.load_incluster_config()
#     except:
#         kubernetes.config.load_kube_config()

#     settings.posting.level = logging.INFO
#     settings.watching.namespaces = [NAMESPACE]

# @kopf.timer('v1', 'pods', interval=15.0)
# def inject_probe_if_alert_firing(name, namespace, labels, logger, **kwargs):
#     logger.info(f"⏱ Timer running for pod: {name} (labels: {labels})")

#     if labels.get("app") != "oai-gnb":
#         logger.info(f"⛔ Skipping pod {name}: app label is not 'oai-gnb' (got: {labels.get('app')})")
#         return

#     if not os.path.exists(ALERT_FILE):
#         logger.info("❌ No alert file found at /tmp/alert.json.")
#         return

#     try:
#         with open(ALERT_FILE, "r") as f:
#             alert_data = json.load(f)
#         logger.info(f"📦 Alert data loaded: {json.dumps(alert_data, indent=2)}")
#     except Exception as e:
#         logger.error(f"❌ Failed to read or parse alert file: {e}")
#         return

#     firing_alert = None
#     for alert in alert_data.get("alerts", []):
#         if alert.get("status") == "firing" and alert.get("labels", {}).get("alertname") == "LowGTPThroughput":
#             firing_alert = alert
#             break

#     if firing_alert:
#         logger.info(f"🚀 Matched LowGTPThroughput alert. Injecting into pod {name}")
#         inject_ephemeral_probe(name, namespace, logger)
#     else:
#         logger.info(f"🛑 No matching alert firing. Killing probe in pod {name}")
#         kill_probe_container(name, logger)

# # @kopf.on.cleanup()
# # def on_cleanup(logger, **_):
# #     logger.info("🔻 Cleanup triggered. Saving logs from namespace before teardown.")
# #     collect_logs_before_deletion(namespace=NAMESPACE, logger=logger)

# from kubernetes.client.rest import ApiException
# from kubernetes.client import ApiClient
# from kubernetes.client import V1Capabilities, V1SecurityContext

# # from datetime import datetime
# # import os
# # import kubernetes

# # def collect_logs_before_deletion(namespace, logger):
# #     core_api = kubernetes.client.CoreV1Api()
# #     timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
# #     log_dir = f"/tmp/pod_logs/{namespace}_{timestamp}"
# #     os.makedirs(log_dir, exist_ok=True)

# #     try:
# #         pods = core_api.list_namespaced_pod(namespace=namespace).items
# #     except Exception as e:
# #         logger.error(f"❌ Failed to list pods: {e}")
# #         return

# #     for pod in pods:
# #         pod_name = pod.metadata.name
# #         try:
# #             log = core_api.read_namespaced_pod_log(name=pod_name, namespace=namespace)
# #             with open(f"{log_dir}/{pod_name}.log", "w") as f:
# #                 f.write(log)
# #             logger.info(f"📝 Saved logs for pod {pod_name}")
# #         except Exception as e:
# #             logger.warning(f"⚠️ Could not get logs from pod {pod_name}: {e}")


# def kill_probe_container(pod_name, logger):
#     namespace = NAMESPACE
#     try:
#         core_api = kubernetes.client.CoreV1Api()
#         pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
#         ephemerals = pod.spec.ephemeral_containers or []
#         target_names = [c.name for c in ephemerals if c.name.startswith("ebpf-latency-probe")]
#     except Exception as e:
#         logger.warning(f"⚠️ Failed to read ephemeral containers from pod {pod_name}: {e}")
#         return

#     if not target_names:
#         logger.info(f"ℹ️ No ephemeral probe containers to kill in {pod_name}")
#         return

#     for cname in target_names:
#         command = [
#             "kubectl", "exec", "-n", namespace, pod_name,
#             "-c", cname, "--",
#             "/bin/sh", "-c", "pkill -f entrypoint-latency.sh"
#         ]
#         try:
#             subprocess.run(command, check=True)
#             logger.info(f"🧹 Killed probe container {cname} in pod {pod_name}")
#         except subprocess.CalledProcessError as e:
#             logger.warning(f"⚠️ Could not kill container {cname} in pod {pod_name}: {e}")



# def inject_ephemeral_probe(pod_name, namespace, logger):
#     core_api = kubernetes.client.CoreV1Api()
#     api_client = kubernetes.client.ApiClient()

#     try:
#         pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
#     except Exception as e:
#         logger.error(f"❌ Failed to read pod {pod_name}: {e}")
#         return

#     existing_ecs = pod.spec.ephemeral_containers or []
#     existing_statuses = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

#     # Check if a probe is already injected and healthy
#     probe_names = [c.name for c in existing_ecs if c.name.startswith("ebpf-latency-probe")]
#     for name in probe_names:
#         status = existing_statuses.get(name)
#         if status and status.state and status.state.terminated:
#             reason = status.state.terminated.reason
#             if reason == "Error":
#                 logger.warning(f"⚠️ Existing probe {name} is in error state, will inject new one.")
#                 continue  # Skip this one and inject a new version
#         else:
#             logger.info(f"✅ Probe {name} already running and healthy.")
#             return  # A healthy probe is already running, no action needed

#     # Pick a new name: ebpf-latency-probe, ebpf-latency-probe2, etc.
#     i = 1
#     base_name = "ebpf-latency-probe"
#     while True:
#         name = base_name if i == 1 else f"{base_name}{i}"
#         if name not in probe_names:
#             break
#         i += 1

#     logger.info(f"🚀 Injecting new probe: {name}")

#     new_container = {
#         "name": name,
#         "image": "r2labuser/ebpf-latency-probe:cleanup",
#         "command": ["./entrypoint-latency.sh"],
#         "env": [
#             {"name": "IFACE", "value": "n2"},
#             {"name": "HANDLE", "value": "4"},
#             {"name": "PRIO", "value": "4"},
#             {"name": "TEIDS", "value": "0x00000001:0x08a5a1ce@1 0x00000002:0xdd90069c@2"},
#             {"name": "EXPORTER_PORT", "value": "9100"},
#             {"name": "EXPORTER_PATH", "value": "/latency"},
#         ],
#         "stdin": True,
#         "tty": True,
#         "targetContainerName": "gnb",
#         "securityContext": {
#             "privileged": True,
#             "capabilities": {"add": ["SYS_ADMIN", "SYS_RESOURCE", "NET_ADMIN"]}
#         },
#     }

#     updated_containers = [ec.to_dict() for ec in existing_ecs] + [new_container]

#     patch_body = {
#         "metadata": {"name": pod_name},
#         "spec": {"ephemeralContainers": updated_containers}
#     }

#     try:
#         api_client.call_api(
#             f"/api/v1/namespaces/{namespace}/pods/{pod_name}/ephemeralcontainers",
#             "PATCH",
#             body=patch_body,
#             auth_settings=["BearerToken"],
#             header_params={"Content-Type": "application/strategic-merge-patch+json"},
#             response_type="object",
#             _preload_content=False
#         )
#         logger.info(f"✅ Successfully injected {name} into {pod_name}")
#     except Exception as e:
#         logger.error(f"❌ Failed to inject probe {name}: {e}")

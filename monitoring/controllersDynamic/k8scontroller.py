import kopf
import kubernetes
import json
import os
import logging
import subprocess

NAMESPACE = "oail1"
ALERT_FILE = "/tmp/alert.json"

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    print("✅ KOPF startup hook triggered")
    try:
        kubernetes.config.load_incluster_config()
    except:
        kubernetes.config.load_kube_config()

    settings.posting.level = logging.INFO
    settings.watching.namespaces = [NAMESPACE]

@kopf.timer('v1', 'pods', interval=15.0)
def inject_probe_if_alert_firing(name, namespace, labels, logger, **kwargs):
    logger.info(f"⏱ Timer running for pod: {name} (labels: {labels})")

    if labels.get("app") != "oai-gnb":
        logger.info(f"⛔ Skipping pod {name}: app label is not 'oai-gnb' (got: {labels.get('app')})")
        return

    if not os.path.exists(ALERT_FILE):
        logger.info("❌ No alert file found at /tmp/alert.json.")
        return

    try:
        with open(ALERT_FILE, "r") as f:
            alert_data = json.load(f)
        logger.info(f"📦 Alert data loaded: {json.dumps(alert_data, indent=2)}")
    except Exception as e:
        logger.error(f"❌ Failed to read or parse alert file: {e}")
        return

    firing_alert = None
    for alert in alert_data.get("alerts", []):
        if alert.get("status") == "firing" and alert.get("labels", {}).get("alertname") == "LowGTPThroughput":
            firing_alert = alert
            break

    if firing_alert:
        logger.info(f"🚀 Matched LowGTPThroughput alert. Injecting into pod {name}")
        inject_ephemeral_probe(name, namespace, logger)
    else:
        logger.info(f"🛑 No matching alert firing. Killing probe in pod {name}")
        kill_probe_container(name, logger)

# @kopf.on.cleanup()
# def on_cleanup(logger, **_):
#     logger.info("🔻 Cleanup triggered. Saving logs from namespace before teardown.")
#     collect_logs_before_deletion(namespace=NAMESPACE, logger=logger)

from kubernetes.client.rest import ApiException
from kubernetes.client import ApiClient
from kubernetes.client import V1Capabilities, V1SecurityContext

# from datetime import datetime
# import os
# import kubernetes

# def collect_logs_before_deletion(namespace, logger):
#     core_api = kubernetes.client.CoreV1Api()
#     timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
#     log_dir = f"/tmp/pod_logs/{namespace}_{timestamp}"
#     os.makedirs(log_dir, exist_ok=True)

#     try:
#         pods = core_api.list_namespaced_pod(namespace=namespace).items
#     except Exception as e:
#         logger.error(f"❌ Failed to list pods: {e}")
#         return

#     for pod in pods:
#         pod_name = pod.metadata.name
#         try:
#             log = core_api.read_namespaced_pod_log(name=pod_name, namespace=namespace)
#             with open(f"{log_dir}/{pod_name}.log", "w") as f:
#                 f.write(log)
#             logger.info(f"📝 Saved logs for pod {pod_name}")
#         except Exception as e:
#             logger.warning(f"⚠️ Could not get logs from pod {pod_name}: {e}")


def kill_probe_container(pod_name, logger):
    namespace = NAMESPACE
    try:
        core_api = kubernetes.client.CoreV1Api()
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        ephemerals = pod.spec.ephemeral_containers or []
        target_names = [c.name for c in ephemerals if c.name.startswith("ebpf-latency-probe")]
    except Exception as e:
        logger.warning(f"⚠️ Failed to read ephemeral containers from pod {pod_name}: {e}")
        return

    if not target_names:
        logger.info(f"ℹ️ No ephemeral probe containers to kill in {pod_name}")
        return

    for cname in target_names:
        command = [
            "kubectl", "exec", "-n", namespace, pod_name,
            "-c", cname, "--",
            "/bin/sh", "-c", "pkill -f entrypoint-latency.sh"
        ]
        try:
            subprocess.run(command, check=True)
            logger.info(f"🧹 Killed probe container {cname} in pod {pod_name}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"⚠️ Could not kill container {cname} in pod {pod_name}: {e}")



def inject_ephemeral_probe(pod_name, namespace, logger):
    core_api = kubernetes.client.CoreV1Api()
    api_client = kubernetes.client.ApiClient()

    try:
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        logger.error(f"❌ Failed to read pod {pod_name}: {e}")
        return

    existing_ecs = pod.spec.ephemeral_containers or []
    existing_statuses = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

    # Check if a probe is already injected and healthy
    probe_names = [c.name for c in existing_ecs if c.name.startswith("ebpf-latency-probe")]
    for name in probe_names:
        status = existing_statuses.get(name)
        if status and status.state and status.state.terminated:
            reason = status.state.terminated.reason
            if reason == "Error":
                logger.warning(f"⚠️ Existing probe {name} is in error state, will inject new one.")
                continue  # Skip this one and inject a new version
        else:
            logger.info(f"✅ Probe {name} already running and healthy.")
            return  # A healthy probe is already running, no action needed

    # Pick a new name: ebpf-latency-probe, ebpf-latency-probe2, etc.
    i = 1
    base_name = "ebpf-latency-probe"
    while True:
        name = base_name if i == 1 else f"{base_name}{i}"
        if name not in probe_names:
            break
        i += 1

    logger.info(f"🚀 Injecting new probe: {name}")

    new_container = {
        "name": name,
        "image": "r2labuser/ebpf-latency-probe:cleanup",
        "command": ["./entrypoint-latency.sh"],
        "env": [
            {"name": "IFACE", "value": "n2"},
            {"name": "HANDLE", "value": "4"},
            {"name": "PRIO", "value": "4"},
            {"name": "TEIDS", "value": "0x00000001:0x08a5a1ce@1 0x00000002:0xdd90069c@2"},
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
        logger.info(f"✅ Successfully injected {name} into {pod_name}")
    except Exception as e:
        logger.error(f"❌ Failed to inject probe {name}: {e}")

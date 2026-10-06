import kopf
import kubernetes
import json
import os
import logging
import subprocess
import hashlib
import requests
import time
import fnmatch


# -------------------------
# Config (from env / Ansible)
# -------------------------

CORE_NS = os.getenv("CORE_NS", "default")
MONITORING_NS = os.getenv("MONITORING_NS", "monitoring")

DEFAULT_IFACE = "n3" if CORE_NS == "open5gs" else "n2"
PROBE_IFACE = os.getenv("PROBE_IFACE", DEFAULT_IFACE)

PROBE_MODE = os.getenv("PROBE_MODE", "gnb").strip().lower()
MATCH_DEBUG = os.getenv("MATCH_DEBUG", "0")
PACKET_PAIRING = os.getenv("PACKET_PAIRING", os.getenv("PACKET_CORRELATION", "0")).strip()

# Probe targeting is intentionally hardcoded so the deployment only exposes
# the high-level PROBE_MODE knob: gnb, upf, or both.
PROBE_APP_LABEL_KEY = "app"

GNB_APP_LABELS = ["*gnb*"]
GNB_POD_NAME_PATTERNS = ["*gnb*"]
GNB_TARGET_CONTAINER = "*gnb*"
GNB_LATENCY_MODE = os.getenv("GNB_LATENCY_MODE", "BOTH")

UPF_APP_LABELS = ["*upf*"]
UPF_POD_NAME_PATTERNS = ["*upf*"]
UPF_TARGET_CONTAINER = "*upf*"
UPF_PROBE_IFACE = "n3"
UPF_LATENCY_MODE = os.getenv("UPF_LATENCY_MODE", "BOTH")


EXPORTER_PATH = os.getenv("EXPORTER_PATH", "/latency")

ALERT_FILE = "/tmp/alert.json"

DRY_RUN = int(os.getenv("DRY_RUN", "0"))

UE_MAPPER_LIMIT = int(os.getenv("UE_MAPPER_LIMIT", "2000"))
PROBE_IMAGE = os.getenv("PROBE_IMAGE", "r2labuser/ebpf-latency-probe:2026")

DEFAULT_UE_MAPPER_URL = f"http://ue-mapper-api.{MONITORING_NS}.svc.cluster.local"
UE_MAPPER_URL = os.getenv("UE_MAPPER_URL", DEFAULT_UE_MAPPER_URL)

# Base values for first managed probe
BASE_HANDLE = int(os.getenv("BASE_HANDLE", "1"))
BASE_PRIO = int(os.getenv("BASE_PRIO", "1"))
BASE_EXPORTER_PORT = int(os.getenv("BASE_EXPORTER_PORT", "9100"))

PROBE_NAME_PREFIX = os.getenv("PROBE_NAME_PREFIX", "ebpf-latency-probe")
PACKET_PAIRER_IMAGE = os.getenv("PACKET_PAIRER_IMAGE", PROBE_IMAGE)
PACKET_PAIRER_NAME = os.getenv("PACKET_PAIRER_NAME", f"{PROBE_NAME_PREFIX}-pairer")
PACKET_PAIRER_SERVICE_NAME = os.getenv("PACKET_PAIRER_SERVICE_NAME", PACKET_PAIRER_NAME)
PACKET_PAIRER_METRICS_PORT = int(os.getenv("PACKET_PAIRER_METRICS_PORT", os.getenv("PACKET_CORRELATION_METRICS_PORT", "9200")))
PACKET_PAIRER_METRICS_PATH = os.getenv("PACKET_PAIRER_METRICS_PATH", os.getenv("PACKET_CORRELATION_METRICS_PATH", "/metrics"))
PACKET_PAIRER_PORT = int(os.getenv("PACKET_PAIRER_PORT", os.getenv("PACKET_COLLECTOR_PORT", "9201")))

RECONCILE_INTERVAL = float(os.getenv("RECONCILE_INTERVAL", "5.0"))
UE_MAPPING_STABLE_CYCLES = max(1, int(os.getenv("UE_MAPPING_STABLE_CYCLES", "3")))
UE_MAPPING_SETTLE_SECONDS = max(0.0, float(os.getenv("UE_MAPPING_SETTLE_SECONDS", "15.0")))
UE_MAPPING_MIN_COMPLETE_UES = max(1, int(os.getenv("UE_MAPPING_MIN_COMPLETE_UES", "1")))
UE_MAPPING_REQUIRE_COMPLETE = int(os.getenv("UE_MAPPING_REQUIRE_COMPLETE", "1"))
UE_MAPPING_PARTIAL_GRACE_SECONDS = max(0.0, float(os.getenv("UE_MAPPING_PARTIAL_GRACE_SECONDS", "60.0")))
CLEANUP_WAIT_SECONDS = max(1.0, float(os.getenv("CLEANUP_WAIT_SECONDS", "20.0")))
CLEANUP_POLL_SECONDS = max(0.5, float(os.getenv("CLEANUP_POLL_SECONDS", "1.0")))

mapping_stability = {}
partial_mapping_since = {}


def env_flag(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


PACKET_PAIRING_ENABLED = env_flag(PACKET_PAIRING)


def packet_pairer_host(namespace: str) -> str:
    return f"{PACKET_PAIRER_SERVICE_NAME}.{namespace}.svc.cluster.local"


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    print("✅ KOPF startup hook triggered")
    print(
        "Probe targeting: "
        f"probe_mode={PROBE_MODE} gnb_labels={GNB_APP_LABELS} "
        f"gnb_mode={GNB_LATENCY_MODE} gnb_iface={PROBE_IFACE} "
        f"upf_labels={UPF_APP_LABELS} "
        f"upf_mode={UPF_LATENCY_MODE} upf_iface={UPF_PROBE_IFACE} "
        f"match_debug={MATCH_DEBUG} packet_pairing={PACKET_PAIRING_ENABLED}"
    )
    if PACKET_PAIRING_ENABLED:
        print(
            "Same-packet pairer: "
            f"name={PACKET_PAIRER_NAME} service={PACKET_PAIRER_SERVICE_NAME} "
            f"image={PACKET_PAIRER_IMAGE} metrics_port={PACKET_PAIRER_METRICS_PORT} "
            f"udp_port={PACKET_PAIRER_PORT} metrics_path={PACKET_PAIRER_METRICS_PATH}"
        )
    print(
        "UE mapping stabilization: "
        f"interval={RECONCILE_INTERVAL}s stable_cycles={UE_MAPPING_STABLE_CYCLES} "
        f"settle_seconds={UE_MAPPING_SETTLE_SECONDS}s "
        f"min_complete_ues={UE_MAPPING_MIN_COMPLETE_UES} "
        f"require_complete={UE_MAPPING_REQUIRE_COMPLETE} "
        f"partial_grace_seconds={UE_MAPPING_PARTIAL_GRACE_SECONDS}"
    )
    print(
        "Probe cleanup: "
        f"wait_seconds={CLEANUP_WAIT_SECONDS}s poll_seconds={CLEANUP_POLL_SECONDS}s"
    )
    try:
        kubernetes.config.load_incluster_config()
    except Exception:
        kubernetes.config.load_kube_config()

    settings.posting.level = logging.INFO
    settings.watching.namespaces = [CORE_NS]


# -------------------------
# UE-MAPPER helpers
# -------------------------

def fingerprint_teids(teids: str) -> str:
    """Short stable fingerprint of TEIDS string."""
    if not teids:
        return ""
    return hashlib.sha256(teids.encode()).hexdigest()[:12]


def fingerprint_probe_config(teids_fp: str, ue_map_fp: str, target: dict) -> str:
    material = {
        "teids_fp": teids_fp,
        "ue_map_fp": ue_map_fp,
        "iface": target["iface"],
        "latency_mode": target["latency_mode"],
        "probe_role": target["role"],
        "target_container": target["target_container"],
        "match_debug": MATCH_DEBUG,
        "packet_pairing": PACKET_PAIRING_ENABLED,
        "packet_pairer_host": packet_pairer_host(CORE_NS) if PACKET_PAIRING_ENABLED else "",
        "packet_pairer_port": PACKET_PAIRER_PORT if PACKET_PAIRING_ENABLED else 0,
        "probe_image": PROBE_IMAGE,
        "exporter_path": EXPORTER_PATH,
        "base_exporter_port": target.get("base_exporter_port", BASE_EXPORTER_PORT),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:12]


def matches_any_pattern(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value or "", pattern) for pattern in patterns)


def pod_matches(name: str, labels: dict, app_labels: list[str], pod_name_patterns: list[str]) -> bool:
    app_value = labels.get(PROBE_APP_LABEL_KEY, "")
    if matches_any_pattern(app_value, app_labels):
        return True
    return matches_any_pattern(name, pod_name_patterns)


def resolve_probe_target(name: str, labels: dict):
    if PROBE_MODE in ("gnb", "both") and pod_matches(name, labels, GNB_APP_LABELS, GNB_POD_NAME_PATTERNS):
        return {
            "role": "gnb",
            "latency_mode": GNB_LATENCY_MODE,
            "iface": PROBE_IFACE,
            "target_container": GNB_TARGET_CONTAINER,
        }

    if PROBE_MODE in ("upf", "both") and pod_matches(name, labels, UPF_APP_LABELS, UPF_POD_NAME_PATTERNS):
        return {
            "role": "upf",
            "latency_mode": UPF_LATENCY_MODE,
            "iface": UPF_PROBE_IFACE,
            "target_container": UPF_TARGET_CONTAINER,
        }

    return None


def target_matches_role(name: str, labels: dict, role: str) -> bool:
    if role == "gnb":
        return pod_matches(name, labels, GNB_APP_LABELS, GNB_POD_NAME_PATTERNS)
    if role == "upf":
        return pod_matches(name, labels, UPF_APP_LABELS, UPF_POD_NAME_PATTERNS)
    return False


def assign_exporter_port_to_target(name: str, namespace: str, target: dict, logger) -> dict:
    target = dict(target)
    core_api = kubernetes.client.CoreV1Api()

    try:
        pods = core_api.list_namespaced_pod(namespace=namespace).items
    except Exception as e:
        logger.warning(
            f"⚠️ Could not list pods in ns={namespace} to assign exporter port. "
            f"Using base port {BASE_EXPORTER_PORT}: {e}"
        )
        target["base_exporter_port"] = BASE_EXPORTER_PORT
        return target

    role_pods = []
    for pod in pods:
        pod_name = pod.metadata.name
        pod_labels = pod.metadata.labels or {}

        if pod.metadata.deletion_timestamp:
            continue
        if target_matches_role(pod_name, pod_labels, target["role"]):
            role_pods.append(pod_name)

    role_pods = sorted(set(role_pods))
    try:
        offset = role_pods.index(name)
    except ValueError:
        offset = 0

    target["base_exporter_port"] = BASE_EXPORTER_PORT + offset
    logger.info(
        f"📡 Exporter port base for {target['role']} pod {name}: "
        f"{target['base_exporter_port']} (role pods={role_pods})"
    )
    return target


def resolve_target_container(pod, target: dict, logger) -> dict:
    target = dict(target)
    requested = target["target_container"]
    container_names = [c.name for c in (pod.spec.containers or [])]

    if not any(ch in requested for ch in "*?["):
        if requested not in container_names:
            logger.warning(
                f"⚠️ Requested target container '{requested}' was not found in pod containers {container_names}"
            )
        return target

    matches = [name for name in container_names if fnmatch.fnmatchcase(name, requested)]
    if not matches:
        raise ValueError(
            f"No pod container matched target pattern '{requested}'. Available containers: {container_names}"
        )

    if len(matches) > 1:
        logger.warning(
            f"⚠️ Multiple containers matched pattern '{requested}' in pod {pod.metadata.name}: {matches}. "
            f"Using {matches[0]}."
        )

    target["target_container"] = matches[0]
    return target


def normalize_teid(value) -> str:
    if value is None:
        return ""
    try:
        return f"0x{int(str(value).strip(), 0):08x}"
    except Exception:
        return ""


def parse_teids_from_arg(teid_arg: str) -> tuple[str, str]:
    teid_pair = (teid_arg or "").strip().split("@", 1)[0]
    if not teid_pair:
        return "", ""

    if ":" in teid_pair:
        ul, dl = teid_pair.split(":", 1)
        return normalize_teid(ul), normalize_teid(dl)

    return normalize_teid(teid_pair), ""


def extract_ue_teids(ue: dict) -> tuple[str, str]:
    teid_arg = (ue.get("teid_args") or "").strip()
    parsed_ul, parsed_dl = parse_teids_from_arg(teid_arg)
    ul_teid = normalize_teid(ue.get("ul_teid")) or parsed_ul
    dl_teid = normalize_teid(ue.get("dl_teid")) or parsed_dl
    return ul_teid, dl_teid


def ue_mapping_is_complete(ue: dict) -> bool:
    ul_teid, dl_teid = extract_ue_teids(ue)
    imsi = str(ue.get("imsi") or "").strip()
    return bool(
        (ue.get("teid_args") or "").strip()
        and ul_teid
        and dl_teid
        and ul_teid != dl_teid
        and imsi
        and imsi.lower() not in {"unknown", "null", "none", "-"}
        and str(ue.get("ue_ip") or "").strip()
        and str(ue.get("slice_id") or "").strip()
    )


def filter_conflicting_ue_teids(ues: list[dict]) -> tuple[list[dict], int]:
    owners = {}
    conflicts = set()
    for index, ue in enumerate(ues):
        for teid in extract_ue_teids(ue):
            previous = owners.setdefault(teid, index)
            if previous != index:
                conflicts.update((previous, index))
    return [ue for index, ue in enumerate(ues) if index not in conflicts], len(conflicts)


def build_teid_ue_map(ues: list[dict]) -> dict:
    teid_ue_map = {}

    for ue in ues:
        ul_teid, dl_teid = extract_ue_teids(ue)

        base = {
            "imsi": str(ue.get("imsi") or ""),
            "ue_ip": str(ue.get("ue_ip") or ""),
            "ran_ue_id": str(ue.get("ran_ue_id") or ""),
            "slice_id": str(ue.get("slice_id") or ""),
            "sst": str(ue.get("sst") or ""),
            "sd": str(ue.get("sd") or ""),
        }

        if ul_teid:
            teid_ue_map[ul_teid] = {**base, "teid_direction": "ul"}
        if dl_teid:
            teid_ue_map[dl_teid] = {**base, "teid_direction": "dl"}

    return teid_ue_map


def fetch_all_teids_from_ue_mapper(logger) -> tuple[str, str, str, str, dict]:
    """
    Returns (teids_string, teids_fingerprint, teid_ue_map_json, ue_map_fingerprint).
    Uses /inventory/ues for both BPF TEID args and Prometheus UE labels.
    """
    url = f"{UE_MAPPER_URL}/inventory/ues"
    try:
        r = requests.get(url, params={"limit": UE_MAPPER_LIMIT}, timeout=3)
        r.raise_for_status()
        j = r.json()
    except Exception as e:
        logger.error(f"❌ Failed to fetch UE inventory from ue-mapper ({url}): {e}")
        return "", "", "{}", "", {
            "mapper_ues": 0,
            "complete_ues": 0,
            "incomplete_ues": 0,
            "conflicting_ues": 0,
            "teid_args": 0,
            "teid_metadata": 0,
        }

    ues = j.get("ues", []) or []
    candidates = [ue for ue in ues if ue_mapping_is_complete(ue)]
    complete_ues, conflicting_ues = filter_conflicting_ue_teids(candidates)
    teids = []
    for ue in complete_ues:
        arg = (ue.get("teid_args") or "").strip()
        if arg:
            teids.append(arg)

    teids = sorted(set(teids))
    teids_str = " ".join(teids).strip()
    incomplete_ues = len(ues) - len(complete_ues)
    teid_ue_map = build_teid_ue_map(complete_ues)
    teid_ue_map_json = json.dumps(teid_ue_map, sort_keys=True, separators=(",", ":"))
    stats = {
        "mapper_ues": len(ues),
        "complete_ues": len(complete_ues),
        "incomplete_ues": incomplete_ues,
        "conflicting_ues": conflicting_ues,
        "teid_args": len(teids),
        "teid_metadata": len(teid_ue_map),
    }
    logger.info(
        f"📚 UE mapper inventory -> ues={stats['mapper_ues']} "
        f"complete={stats['complete_ues']} incomplete={stats['incomplete_ues']} "
        f"conflicting={stats['conflicting_ues']} "
        f"teid_args={stats['teid_args']} teid_metadata={stats['teid_metadata']}"
    )
    return (
        teids_str,
        fingerprint_teids(teids_str),
        teid_ue_map_json,
        fingerprint_teids(teid_ue_map_json),
        stats,
    )


# -------------------------
# Generic ephemeral helpers
# -------------------------

def env_list_to_dict(env_list) -> dict:
    out = {}
    for item in (env_list or []):
        try:
            name = item.name if hasattr(item, "name") else item.get("name")
            value = item.value if hasattr(item, "value") else item.get("value")
            if name:
                out[name] = value or ""
        except Exception:
            continue
    return out


def safe_int(v, default=None):
    try:
        return int(str(v))
    except Exception:
        return default


def get_running_ephemeral_info(pod) -> list[dict]:
    """
    Return info for running ephemeral containers:
    [
      {
        "name": ...,
        "env": {...},
        "state": "running"
      },
      ...
    ]
    """
    ecs = pod.spec.ephemeral_containers or []
    statuses = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

    out = []
    for c in ecs:
        st = statuses.get(c.name)
        if not st or not st.state or not st.state.running:
            continue
        out.append({
            "name": c.name,
            "env": env_list_to_dict(c.env or []),
            "state": "running",
        })
    return out


def compute_next_runtime_resources(pod, logger, base_exporter_port=None) -> dict:
    """
    Scan all running ephemeral containers in the pod and choose the next free:
      - HANDLE
      - PRIO
      - EXPORTER_PORT

    This lets us coexist with future probe types as long as they expose these envs.
    """
    running_infos = get_running_ephemeral_info(pod)

    used_handles = set()
    used_prios = set()
    used_ports = set()

    for info in running_infos:
        env = info["env"]
        h = safe_int(env.get("HANDLE"))
        p = safe_int(env.get("PRIO"))
        port = safe_int(env.get("EXPORTER_PORT"))

        if h is not None:
            used_handles.add(h)
        if p is not None:
            used_prios.add(p)
        if port is not None:
            used_ports.add(port)

    next_handle = BASE_HANDLE
    while next_handle in used_handles:
        next_handle += 1

    next_prio = BASE_PRIO
    while next_prio in used_prios:
        next_prio += 1

    next_port = base_exporter_port if base_exporter_port is not None else BASE_EXPORTER_PORT
    while next_port in used_ports:
        next_port += 1

    logger.info(
        f"📦 Runtime allocation -> HANDLE={next_handle} PRIO={next_prio} EXPORTER_PORT={next_port} "
        f"(used handles={sorted(used_handles)}, prios={sorted(used_prios)}, ports={sorted(used_ports)})"
    )

    return {
        "HANDLE": str(next_handle),
        "PRIO": str(next_prio),
        "EXPORTER_PORT": str(next_port),
    }


def mapping_stability_key(namespace: str, pod_name: str, target: dict) -> str:
    return f"{namespace}/{pod_name}/{target['role']}"


def mapping_is_stable(namespace: str, pod_name: str, target: dict, config_fp: str, stats: dict, logger) -> bool:
    key = mapping_stability_key(namespace, pod_name, target)
    if stats.get("complete_ues", 0) < UE_MAPPING_MIN_COMPLETE_UES:
        mapping_stability.pop(key, None)
        partial_mapping_since.pop(key, None)
        logger.info(
            f"⏳ Waiting for UE mapper: complete UEs={stats.get('complete_ues', 0)} "
            f"< required {UE_MAPPING_MIN_COMPLETE_UES}"
        )
        return False

    now = time.time()
    state = mapping_stability.get(key)

    if not state or state.get("config_fp") != config_fp:
        state = {
            "config_fp": config_fp,
            "first_seen": now,
            "last_seen": now,
            "observations": 1,
        }
        mapping_stability[key] = state
    else:
        state["observations"] += 1
        state["last_seen"] = now

    if stats.get("incomplete_ues", 0) > 0:
        partial_mapping_since.setdefault(key, now)
    else:
        partial_mapping_since.pop(key, None)

    age = now - state["first_seen"]
    if state["observations"] < UE_MAPPING_STABLE_CYCLES or age < UE_MAPPING_SETTLE_SECONDS:
        logger.info(
            f"⏳ Waiting for stable UE mapping for {key}: "
            f"observations={state['observations']}/{UE_MAPPING_STABLE_CYCLES}, "
            f"age={age:.1f}/{UE_MAPPING_SETTLE_SECONDS:.1f}s, config_fp={config_fp}"
        )
        return False

    if UE_MAPPING_REQUIRE_COMPLETE and stats.get("incomplete_ues", 0) > 0:
        partial_age = now - partial_mapping_since[key]
        if partial_age < UE_MAPPING_PARTIAL_GRACE_SECONDS:
            logger.info(
                f"⏳ Waiting for UE mapper completeness: incomplete UEs={stats['incomplete_ues']}, "
                f"grace={partial_age:.1f}/{UE_MAPPING_PARTIAL_GRACE_SECONDS:.1f}s"
            )
            return False
        logger.warning(
            f"⚠️ UE mapper grace elapsed; injecting for {stats['complete_ues']} complete UEs "
            f"while {stats['incomplete_ues']} remain incomplete"
        )

    logger.info(
        f"✅ UE mapping stable for {key}: "
        f"observations={state['observations']}, age={age:.1f}s, config_fp={config_fp}"
    )
    return True


def build_pairer_labels() -> dict:
    return {
        "app": PACKET_PAIRER_NAME,
        "component": "same-packet-pairer",
        "managed-by": "kopf-ebpf-controller",
    }


def ensure_same_packet_pairer(namespace: str, teid_ue_map: str, logger) -> None:
    if not PACKET_PAIRING_ENABLED:
        return
    if DRY_RUN:
        logger.warning(
            f"🟡 DRY_RUN=1 -> would ensure same-packet pairer Deployment/Service "
            f"{PACKET_PAIRER_NAME}/{PACKET_PAIRER_SERVICE_NAME} in ns={namespace}"
        )
        return

    apps_api = kubernetes.client.AppsV1Api()
    core_api = kubernetes.client.CoreV1Api()
    labels = build_pairer_labels()

    service_body = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": PACKET_PAIRER_SERVICE_NAME,
            "namespace": namespace,
            "labels": labels,
            "annotations": {
                "prometheus.io/scrape": "true",
                "prometheus.io/scheme": "http",
                "prometheus.io/path": PACKET_PAIRER_METRICS_PATH,
                "prometheus.io/port": str(PACKET_PAIRER_METRICS_PORT),
            },
        },
        "spec": {
            "selector": labels,
            "ports": [
                {
                    "name": "metrics",
                    "port": PACKET_PAIRER_METRICS_PORT,
                    "targetPort": PACKET_PAIRER_METRICS_PORT,
                    "protocol": "TCP",
                },
                {
                    "name": "samples",
                    "port": PACKET_PAIRER_PORT,
                    "targetPort": PACKET_PAIRER_PORT,
                    "protocol": "UDP",
                },
            ],
        },
    }

    deployment_body = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": PACKET_PAIRER_NAME,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": labels,
            },
            "template": {
                "metadata": {
                    "labels": labels,
                },
                "spec": {
                    "containers": [
                        {
                            "name": "same-packet-pairer",
                            "image": PACKET_PAIRER_IMAGE,
                            "imagePullPolicy": "Always",
                            "command": ["/app/entrypoint-same-packet-pairer.sh"],
                            "env": [
                                {"name": "TEID_UE_MAP", "value": teid_ue_map},
                                {"name": "PACKET_PAIRER_METRICS_PORT", "value": str(PACKET_PAIRER_METRICS_PORT)},
                                {"name": "PACKET_PAIRER_METRICS_PATH", "value": PACKET_PAIRER_METRICS_PATH},
                                {"name": "PACKET_PAIRER_PORT", "value": str(PACKET_PAIRER_PORT)},
                            ],
                            "ports": [
                                {
                                    "name": "metrics",
                                    "containerPort": PACKET_PAIRER_METRICS_PORT,
                                    "protocol": "TCP",
                                },
                                {
                                    "name": "samples",
                                    "containerPort": PACKET_PAIRER_PORT,
                                    "protocol": "UDP",
                                },
                            ],
                        }
                    ]
                },
            },
        },
    }

    try:
        core_api.read_namespaced_service(name=PACKET_PAIRER_SERVICE_NAME, namespace=namespace)
        core_api.patch_namespaced_service(name=PACKET_PAIRER_SERVICE_NAME, namespace=namespace, body=service_body)
        logger.info(f"🔁 Updated same-packet pairer Service {PACKET_PAIRER_SERVICE_NAME} in ns={namespace}")
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            core_api.create_namespaced_service(namespace=namespace, body=service_body)
            logger.info(f"✅ Created same-packet pairer Service {PACKET_PAIRER_SERVICE_NAME} in ns={namespace}")
        else:
            raise

    try:
        apps_api.read_namespaced_deployment(name=PACKET_PAIRER_NAME, namespace=namespace)
        apps_api.patch_namespaced_deployment(name=PACKET_PAIRER_NAME, namespace=namespace, body=deployment_body)
        logger.info(f"🔁 Updated same-packet pairer Deployment {PACKET_PAIRER_NAME} in ns={namespace}")
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            apps_api.create_namespaced_deployment(namespace=namespace, body=deployment_body)
            logger.info(f"✅ Created same-packet pairer Deployment {PACKET_PAIRER_NAME} in ns={namespace}")
        else:
            raise


# -------------------------
# Timer reconcile (always-on)
# -------------------------

@kopf.timer('v1', 'pods', interval=RECONCILE_INTERVAL)
def reconcile_probe_always_on(name, namespace, labels, logger, **kwargs):
    if namespace != CORE_NS:
        return

    logger.info(f"⏱ Timer running for pod: {name} in ns={namespace} (labels: {labels})")

    if PACKET_PAIRING_ENABLED:
        try:
            _, _, teid_ue_map, _, _ = fetch_all_teids_from_ue_mapper(logger)
            ensure_same_packet_pairer(namespace, teid_ue_map, logger)
        except Exception as e:
            logger.error(f"❌ Failed to reconcile same-packet pairer in ns={namespace}: {e}")
            return

    target = resolve_probe_target(name, labels)
    if not target:
        logger.info(
            f"⛔ Skipping pod {name}: no enabled probe target matched "
            f"({PROBE_APP_LABEL_KEY}={labels.get(PROBE_APP_LABEL_KEY)})"
        )
        return
    target = assign_exporter_port_to_target(name, namespace, target, logger)

    teids, teids_fp, teid_ue_map, ue_map_fp, ue_stats = fetch_all_teids_from_ue_mapper(logger)

    if not teids:
        key = mapping_stability_key(namespace, name, target)
        mapping_stability.pop(key, None)
        partial_mapping_since.pop(key, None)
        logger.warning("⚠️ UE-mapper returned no TEIDS yet. Will not inject probe.")
        return

    logger.info(f"📌 Desired TEIDS fingerprint: {teids_fp} (chars={len(teids)})")
    logger.info(f"📌 Desired UE map fingerprint: {ue_map_fp} (bytes={len(teid_ue_map)})")
    config_fp = fingerprint_probe_config(teids_fp, ue_map_fp, target)
    logger.info(
        f"📌 Probe target: role={target['role']} mode={target['latency_mode']} "
        f"iface={target['iface']} target_container={target['target_container']} "
        f"config_fp={config_fp} (CORE_NS={CORE_NS})"
    )
    logger.info(f"📌 UE_MAPPER_URL: {UE_MAPPER_URL}")

    mapping_ready = mapping_is_stable(namespace, name, target, config_fp, ue_stats, logger)

    if probe_running_with_config(name, namespace, teids_fp, config_fp, logger):
        logger.info("✅ Probe already running or starting with matching TEIDS and config. No action.")
        return

    if not mapping_ready:
        return

    logger.info("🔁 TEIDS/config changed or no healthy probe. Refreshing probe...")

    if DRY_RUN:
        logger.warning("🟡 DRY_RUN=1 -> Will NOT kill or inject. Printing what would happen.")
        _ = kill_probe_container(name, namespace, logger)
        dump_injection_request(name, namespace, logger, teids, teids_fp, teid_ue_map, ue_map_fp, config_fp, target)
        return

    cleanup_ok = kill_probe_container(name, namespace, logger)
    if not cleanup_ok:
        logger.warning(
            f"⚠️ Cleanup failed for {name}; will NOT inject a replacement probe on this cycle."
        )
        return

    deadline = time.time() + CLEANUP_WAIT_SECONDS
    remaining = list_active_probe_containers(name, namespace, logger)
    while remaining and time.time() < deadline:
        time.sleep(CLEANUP_POLL_SECONDS)
        remaining = list_active_probe_containers(name, namespace, logger)

    if remaining:
        logger.warning(
            f"⚠️ Managed probes are still active in {name} after cleanup; "
            f"will NOT inject another probe yet. Remaining: {remaining}"
        )
        return

    inject_ephemeral_probe(name, namespace, logger, teids, teids_fp, teid_ue_map, ue_map_fp, config_fp, target)


# -------------------------
# Kubernetes helpers
# -------------------------

def probe_running_with_config(pod_name, namespace, desired_teids_fp: str, desired_config_fp: str, logger) -> bool:
    """
    Return True if the desired probe is running or already injected and starting,
    and no stale managed probes are still active. Kubernetes keeps old ephemeral
    containers in pod specs, so terminated stale probes are harmless; non-terminated
    stale probes must be cleaned or allowed to finish before another injection.

    Desired probe means:
      - name starts with PROBE_NAME_PREFIX
      - status is running or starting (not terminated)
      - env TEIDS_FP matches desired_fp
      - env PROBE_CONFIG_FP matches desired_config_fp
    """
    try:
        core_api = kubernetes.client.CoreV1Api()
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        logger.warning(f"⚠️ Failed to read pod {pod_name} in ns={namespace}: {e}")
        return False

    ecs = pod.spec.ephemeral_containers or []
    statuses = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

    desired_active = []
    stale_active = []

    for c in ecs:
        if not c.name.startswith(PROBE_NAME_PREFIX):
            continue

        st = statuses.get(c.name)
        state = probe_container_state(st)
        if state == "terminated":
            continue

        env = env_list_to_dict(c.env or [])
        teids_fp = env.get("TEIDS_FP", "")
        config_fp = env.get("PROBE_CONFIG_FP", "")
        info = {
            "name": c.name,
            "state": state,
            "teids_fp": teids_fp,
            "config_fp": config_fp,
        }

        if teids_fp == desired_teids_fp and config_fp == desired_config_fp:
            desired_active.append(info)
        else:
            stale_active.append(info)

    if stale_active:
        logger.info(f"♻️ Stale active probe containers found in {pod_name}: {stale_active}")
        return False

    if len(desired_active) > 1:
        logger.info(f"♻️ Duplicate desired active probes found in {pod_name}: {desired_active}")
        return False

    if not desired_active:
        return False

    desired = desired_active[0]
    if desired["state"] != "running":
        logger.info(f"⏳ Desired probe already injected but not running yet in {pod_name}: {desired}")

    return True


def probe_container_state(status) -> str:
    if not status or not status.state:
        return "pending"
    if status.state.running:
        return "running"
    if status.state.waiting:
        return "waiting"
    if status.state.terminated:
        return "terminated"
    return "unknown"


def list_active_probe_containers(pod_name, namespace, logger) -> list[dict]:
    core_api = kubernetes.client.CoreV1Api()

    try:
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        logger.warning(f"⚠️ Failed to read pod {pod_name} in ns={namespace}: {e}")
        return []

    ephemerals = pod.spec.ephemeral_containers or []
    status_map = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

    active = []
    for c in ephemerals:
        if not c.name.startswith(PROBE_NAME_PREFIX):
            continue

        st = status_map.get(c.name)
        state = probe_container_state(st)
        if state == "terminated":
            continue

        env = env_list_to_dict(c.env or [])
        active.append({
            "name": c.name,
            "state": state,
            "iface": env.get("IFACE", PROBE_IFACE),
            "prio": env.get("PRIO", str(BASE_PRIO)),
            "port": env.get("EXPORTER_PORT", str(BASE_EXPORTER_PORT)),
            "teids_fp": env.get("TEIDS_FP", ""),
            "config_fp": env.get("PROBE_CONFIG_FP", ""),
        })

    return active


def kill_probe_container(pod_name, namespace, logger):
    """
    Safer kill:
      - Only attempts kubectl exec if pod is Running
      - Targets only ephemeral containers named PROBE_NAME_PREFIX*
      - Uses each container's actual IFACE/PRIO/EXPORTER_PORT
      - In DRY_RUN: does not exec, only logs what it would kill
    """
    core_api = kubernetes.client.CoreV1Api()

    try:
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        logger.warning(f"⚠️ Failed to read pod {pod_name} in ns={namespace}: {e}")
        return False

    phase = (pod.status.phase or "")
    if phase != "Running":
        logger.info(f"⛔ Not killing probes in {pod_name}: pod phase is {phase}")
        return False

    ephemerals = pod.spec.ephemeral_containers or []
    status_map = {s.name: s for s in (pod.status.ephemeral_container_statuses or [])}

    targets = []
    for c in ephemerals:
        if not c.name.startswith(PROBE_NAME_PREFIX):
            continue

        st = status_map.get(c.name)
        if st and st.state and st.state.running:
            env = env_list_to_dict(c.env or [])
            targets.append({
                "name": c.name,
                "iface": env.get("IFACE", PROBE_IFACE),
                "handle": env.get("HANDLE", str(BASE_HANDLE)),
                "prio": env.get("PRIO", str(BASE_PRIO)),
                "port": env.get("EXPORTER_PORT", str(BASE_EXPORTER_PORT)),
            })

    if not targets:
        logger.info(f"ℹ️ No running ephemeral probe containers to kill in {pod_name}")
        return True

    logger.info(f"🧹 Kill targets in {pod_name}: {targets}")

    if DRY_RUN:
        logger.warning("🟡 DRY_RUN=1 -> Skipping kubectl exec kill commands.")
        return True

    ok = True
    for target in targets:
        cname = target["name"]
        command = [
            "kubectl", "exec", "-n", namespace, pod_name,
            "-c", cname, "--",
            "/bin/sh", "-c",
            "pkill -TERM -f '[e]ntrypoint-latency.sh'"
        ]
        try:
            result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                logger.warning(
                    f"⚠️ Cleanup exec for {cname} in pod {pod_name} returned {result.returncode}. "
                    f"stdout={result.stdout!r} stderr={result.stderr!r}"
                )
                ok = False
            else:
                logger.info(f"🧹 Cleanup signal sent to probe supervisor {cname} in pod {pod_name}")
        except Exception as e:
            logger.warning(f"⚠️ Could not run cleanup command in container {cname} in pod {pod_name}: {e}")
            ok = False

    return ok


def compute_next_probe_name(existing_ecs) -> str:
    probe_names = [c.name for c in existing_ecs if c.name.startswith(PROBE_NAME_PREFIX)]
    i = 1
    while True:
        cname = PROBE_NAME_PREFIX if i == 1 else f"{PROBE_NAME_PREFIX}{i}"
        if cname not in probe_names:
            return cname
        i += 1


def build_ephemeral_patch_body(
    pod_name,
    namespace,
    logger,
    teids: str,
    teids_fp: str,
    teid_ue_map: str,
    ue_map_fp: str,
    config_fp: str,
    target: dict,
):
    core_api = kubernetes.client.CoreV1Api()
    pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
    target = resolve_target_container(pod, target, logger)
    existing_ecs = pod.spec.ephemeral_containers or []

    cname = compute_next_probe_name(existing_ecs)
    runtime = compute_next_runtime_resources(
        pod,
        logger,
        target.get("base_exporter_port", BASE_EXPORTER_PORT),
    )

    logger.info(
        f"🚀 Probe env -> IFACE={target['iface']} HANDLE={runtime['HANDLE']} "
        f"PRIO={runtime['PRIO']} PORT={runtime['EXPORTER_PORT']} "
        f"LATENCY_MODE={target['latency_mode']} PROBE_ROLE={target['role']} "
        f"MATCH_DEBUG={MATCH_DEBUG} PROBE_IMAGE={PROBE_IMAGE} "
        f"PACKET_PAIRING={PACKET_PAIRING_ENABLED} "
        f"TARGET_CONTAINER={target['target_container']} "
        f"IMAGE_PULL_POLICY=Always TEIDS_FP={teids_fp} "
        f"UE_MAP_FP={ue_map_fp} PROBE_CONFIG_FP={config_fp}"
    )

    env = [
        {"name": "IFACE", "value": target["iface"]},
        {"name": "HANDLE", "value": runtime["HANDLE"]},
        {"name": "PRIO", "value": runtime["PRIO"]},
        {"name": "TEIDS", "value": teids},
        {"name": "TEIDS_FP", "value": teids_fp},
        {"name": "TEID_UE_MAP", "value": teid_ue_map},
        {"name": "UE_MAP_FP", "value": ue_map_fp},
        {"name": "PROBE_CONFIG_FP", "value": config_fp},
        {"name": "EXPORTER_PORT", "value": runtime["EXPORTER_PORT"]},
        {"name": "EXPORTER_PATH", "value": EXPORTER_PATH},
        {"name": "LATENCY_MODE", "value": target["latency_mode"]},
        {"name": "PROBE_ROLE", "value": target["role"]},
        {"name": "PROBE_POD", "value": pod_name},
        {"name": "PROBE_NODE", "value": pod.spec.node_name or ""},
        {"name": "PROBE_TARGET", "value": target["target_container"]},
        {"name": "MATCH_DEBUG", "value": MATCH_DEBUG},
    ]

    if PACKET_PAIRING_ENABLED:
        env.extend([
            {"name": "PACKET_PAIRING", "value": "1"},
            {"name": "PACKET_PAIRER_HOST", "value": packet_pairer_host(namespace)},
            {"name": "PACKET_PAIRER_PORT", "value": str(PACKET_PAIRER_PORT)},
        ])

    new_container = {
        "name": cname,
        "image": PROBE_IMAGE,
        "imagePullPolicy": "Always",
        "command": ["./entrypoint-latency.sh"],
        "env": env,
        "stdin": True,
        "tty": True,
        "targetContainerName": target["target_container"],
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


def dump_injection_request(
    pod_name,
    namespace,
    logger,
    teids: str,
    teids_fp: str,
    teid_ue_map: str,
    ue_map_fp: str,
    config_fp: str,
    target: dict,
):
    try:
        cname, patch_body = build_ephemeral_patch_body(
            pod_name, namespace, logger, teids, teids_fp, teid_ue_map, ue_map_fp, config_fp, target
        )
    except Exception as e:
        logger.error(f"❌ Could not build patch body for injection: {e}")
        return

    endpoint = f"/api/v1/namespaces/{namespace}/pods/{pod_name}/ephemeralcontainers"
    logger.warning("🟡 DRY RUN injection dump:")
    logger.warning("  METHOD: PATCH")
    logger.warning(f"  URL:    {endpoint}")
    logger.warning("  HEADER: Content-Type=application/strategic-merge-patch+json")
    logger.warning(f"  Would inject container name: {cname}")
    logger.warning(f"  Target role/mode: {target['role']}/{target['latency_mode']}")
    logger.warning("  PATCH BODY JSON:")
    logger.warning(json.dumps(patch_body, indent=2))


def inject_ephemeral_probe(
    pod_name,
    namespace,
    logger,
    teids: str,
    teids_fp: str,
    teid_ue_map: str,
    ue_map_fp: str,
    config_fp: str,
    target: dict,
):
    try:
        cname, patch_body = build_ephemeral_patch_body(
            pod_name, namespace, logger, teids, teids_fp, teid_ue_map, ue_map_fp, config_fp, target
        )
    except Exception as e:
        logger.error(f"❌ Failed to build injection payload: {e}")
        return

    try:
        kubernetes.client.CoreV1Api().patch_namespaced_pod_ephemeralcontainers(
            name=pod_name,
            namespace=namespace,
            body=patch_body,
            _content_type="application/strategic-merge-patch+json",
        )
        logger.info(f"✅ Successfully injected {cname} ({target['role']}/{target['latency_mode']}) into {pod_name}")
    except Exception as e:
        logger.error(f"❌ Failed to inject probe {cname}: {e}")

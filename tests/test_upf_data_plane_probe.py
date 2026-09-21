import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jinja2 import Environment
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("upf_data_plane_probe", ROOT / "probes/upf_data_plane/probe.py")
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


NETWORK_STATUS = json.dumps([
    {"name": "default/cluster-network", "interface": "eth0", "default": True},
    {"name": "open5gs/n3network", "interface": "net1"},
])
ROUTE_TABLE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT
eth0\t00000000\t0100000A\t0003\t0\t0\t0\t00000000\t0\t0\t0
net1\t00030A0A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
"""
PROC_NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
  eth0: 100 10 1 2 0 0 0 0 200 20 3 4 0 0 0 0
  net1: 300 30 5 6 0 0 0 0 400 40 7 8 0 0 0 0
"""


def ipv4_udp_frame(source_port=2152, destination_port=40000, vlan=False):
    ethernet = b"\x00" * 12 + (b"\x81\x00\x00\x01\x08\x00" if vlan else b"\x08\x00")
    ip = bytes([0x45, 0, 0, 28, 0, 0, 0, 0, 64, 17, 0, 0]) + b"\x00" * 8
    udp = source_port.to_bytes(2, "big") + destination_port.to_bytes(2, "big") + b"\x00\x08\x00\x00"
    return ethernet + ip + udp


class DiscoveryTests(unittest.TestCase):
    def test_n3_uses_matching_multus_attachment(self):
        self.assertEqual(probe.discover_n3_interface(NETWORK_STATUS, ["n3network"]), probe.PathDiscovery("net1", "network_status"))

    def test_n3_override_wins_and_ambiguity_is_not_guessed(self):
        self.assertEqual(probe.discover_n3_interface("not-json", [], "n3"), probe.PathDiscovery("n3", "override"))
        ambiguous = json.dumps([{"name": "n3network", "interface": "net1"}, {"name": "n3network", "interface": "net2"}])
        self.assertEqual(probe.discover_n3_interface(ambiguous, ["n3network"]), probe.PathDiscovery("", "network_status_ambiguous"))

    def test_n6_uses_one_default_route_or_reports_ambiguity(self):
        self.assertEqual(probe.discover_n6_interface(ROUTE_TABLE), probe.PathDiscovery("eth0", "default_route"))
        two_defaults = ROUTE_TABLE + "net2\t00000000\t0100000A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        self.assertEqual(probe.discover_n6_interface(two_defaults), probe.PathDiscovery("", "default_route_ambiguous"))

    def test_proc_counters_and_gtpu_recognition(self):
        counters = probe.parse_interface_counters(PROC_NET_DEV)
        self.assertEqual(counters["net1"].transmit_drops, 8)
        self.assertTrue(probe.is_gtpu_frame(ipv4_udp_frame()))
        self.assertTrue(probe.is_gtpu_frame(ipv4_udp_frame(vlan=True)))
        self.assertFalse(probe.is_gtpu_frame(ipv4_udp_frame(source_port=53, destination_port=54)))


class MetricsTests(unittest.TestCase):
    def test_probe_exports_resolved_path_counters_without_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            dev = directory / "dev"
            route = directory / "route"
            dev.write_text(PROC_NET_DEV)
            route.write_text(ROUTE_TABLE)
            instance = probe.UPFDataPlaneProbe(NETWORK_STATUS, ["n3network"], proc_net_dev_path=str(dev), proc_route_path=str(route), registry=CollectorRegistry())
            instance.refresh()
            instance.stop()
            output = generate_latest(instance.registry).decode()
        samples = {sample.name: sample.value for family in text_string_to_metric_families(output) for sample in family.samples if sample.name == "upf_probe_path_ready" and sample.labels.get("path") == "n3"}
        self.assertEqual(samples["upf_probe_path_ready"], 1)
        self.assertIn('path="n3"', output)
        self.assertNotIn("10.10.", output)


class IntegrationTests(unittest.TestCase):
    def test_role_defaults_service_and_deploy_wiring(self):
        defaults = yaml.safe_load((ROOT / "roles/monitoring/sniffers/upf/defaults/main.yml").read_text())
        template = Environment().from_string((ROOT / "roles/monitoring/sniffers/upf/templates/upf-data-plane-probe-service.yaml.j2").read_text())
        service = yaml.safe_load(template.render(core="open5gs", upf_data_plane_probe_metrics_port=9103, upf_data_plane_probe_target_label_key="monitoring.5g.example/upf-data-plane-probe", upf_data_plane_probe_target_label_value="enabled"))
        tasks = (ROOT / "roles/monitoring/sniffers/upf/tasks/main.yml").read_text()
        deploy = (ROOT / "playbooks/deploy.yml").read_text()
        self.assertFalse(defaults["upf_data_plane_probe_enabled"])
        self.assertEqual(defaults["upf_data_plane_probe_default_n3_network_names_by_core"]["open5gs"], ["n3network"])
        self.assertEqual(service["spec"]["clusterIP"], "None")
        self.assertEqual(service["spec"]["selector"], {"monitoring.5g.example/upf-data-plane-probe": "enabled"})
        self.assertIn("UPF_PROBE_NETWORK_STATUS_JSON", tasks)
        self.assertIn("NET_RAW", tasks)
        self.assertIn("monitoring/sniffers/upf", deploy)

    def test_dockerfile_and_artifact_queries_are_present(self):
        dockerfile = (ROOT / "probes/upf_data_plane/Dockerfile").read_text()
        queries = json.loads((ROOT / "configs/artifacts/default_prometheus_queries.json").read_text())
        names = [entry["name"] for entry in queries]
        self.assertIn("COPY probes/upf_data_plane/probe.py", dockerfile)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("upf_probe_path_ready", names)
        self.assertIn("upf_probe_gtpu_packets_per_second_30s", names)


if __name__ == "__main__":
    unittest.main()

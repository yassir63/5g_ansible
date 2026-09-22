import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jinja2 import Environment
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("user_plane_path_probe", ROOT / "probes/user_plane_path/probe.py")
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


def ipv4_udp_frame(source_port=2152, destination_port=40000, vlan=False, gtpu_flags=0x30):
    ethernet = b"\x00" * 12 + (b"\x81\x00\x00\x01\x08\x00" if vlan else b"\x08\x00")
    ip = bytes([0x45, 0, 0, 36, 0, 0, 0, 0, 64, 17, 0, 0]) + b"\x00" * 8
    udp = source_port.to_bytes(2, "big") + destination_port.to_bytes(2, "big") + b"\x00\x10\x00\x00"
    return ethernet + ip + udp + bytes([gtpu_flags, 0xFF, 0, 0, 0, 0, 0, 1])


class DiscoveryTests(unittest.TestCase):
    def test_n3_uses_matching_multus_attachment(self):
        self.assertEqual(probe.discover_n3_interface(NETWORK_STATUS, ["n3network"]), probe.PathDiscovery("net1", "network_status"))

    def test_n3_override_wins_and_ambiguity_is_not_guessed(self):
        self.assertEqual(probe.discover_n3_interface("not-json", [], "n3"), probe.PathDiscovery("n3", "override"))
        ambiguous = json.dumps([{"name": "n3network", "interface": "net1"}, {"name": "n3network", "interface": "net2"}])
        self.assertEqual(probe.discover_n3_interface(ambiguous, ["n3network"]), probe.PathDiscovery("", "network_status_ambiguous"))

    def test_n3_name_hint_is_independent_of_ran_vendor(self):
        primary = {"name": "cbr0", "interface": "eth0", "default": True}
        for name in ("open5gs/oai-gnb-n3", "open5gs/n3network"):
            status = json.dumps([primary, {"name": name, "interface": "net1"}])
            self.assertEqual(probe.discover_n3_interface(status, []), probe.PathDiscovery("net1", "name_hint"))
        no_hint = json.dumps([primary, {"name": "data-network", "interface": "net1"}])
        self.assertEqual(probe.discover_n3_interface(no_hint, []), probe.PathDiscovery("", "name_hint_missing"))
        ambiguous = json.dumps([primary, {"name": "n3-a", "interface": "net1"}, {"name": "n3-b", "interface": "net2"}])
        self.assertEqual(probe.discover_n3_interface(ambiguous, []), probe.PathDiscovery("", "name_hint_ambiguous"))

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
        self.assertFalse(probe.is_gtpu_frame(ipv4_udp_frame(gtpu_flags=0x00)))
        self.assertFalse(probe.is_gtpu_frame(ipv4_udp_frame()[:-8]))


class MetricsTests(unittest.TestCase):
    def _probe_output(self, anchor, monitor_n6):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            dev = directory / "dev"
            route = directory / "route"
            dev.write_text(PROC_NET_DEV)
            route.write_text(ROUTE_TABLE)
            instance = probe.UserPlanePathProbe(
                anchor=anchor,
                network_status=NETWORK_STATUS,
                n3_network_names=["n3network"],
                monitor_n6=monitor_n6,
                proc_net_dev_path=str(dev),
                proc_route_path=str(route),
                registry=CollectorRegistry(),
            )
            instance.refresh()
            instance.stop()
            return generate_latest(instance.registry).decode()

    def test_gnb_exports_n3_only_and_marks_collection_success(self):
        output = self._probe_output("gnb", monitor_n6=False)
        samples = {
            (sample.name, tuple(sorted(sample.labels.items()))): sample.value
            for family in text_string_to_metric_families(output)
            for sample in family.samples
        }
        self.assertEqual(samples[("user_plane_probe_path_ready", (("anchor", "gnb"), ("path", "n3")))], 1)
        self.assertEqual(samples[("user_plane_probe_collection_success", (("anchor", "gnb"),))], 1)
        self.assertNotIn('path="n6"', output)
        self.assertIn('anchor="gnb"', output)
        self.assertNotIn("10.10.", output)

    def test_upf_requires_both_n3_and_n6_paths(self):
        output = self._probe_output("upf", monitor_n6=True)
        samples = {
            (sample.name, tuple(sorted(sample.labels.items()))): sample.value
            for family in text_string_to_metric_families(output)
            for sample in family.samples
        }
        self.assertEqual(samples[("user_plane_probe_path_ready", (("anchor", "upf"), ("path", "n3")))], 1)
        self.assertEqual(samples[("user_plane_probe_path_ready", (("anchor", "upf"), ("path", "n6")))], 1)
        self.assertEqual(samples[("user_plane_probe_collection_success", (("anchor", "upf"),))], 1)

    def test_invalid_anchor_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "anchor"):
            probe.UserPlanePathProbe("amf", NETWORK_STATUS, ["n3network"])

    def test_gnb_traffic_confirms_or_corrects_name_hint_without_blocking_idle_pod(self):
        status = json.dumps([
            {"name": "cbr0", "interface": "eth0", "default": True},
            {"name": "n3-test", "interface": "net1"},
            {"name": "other-data", "interface": "net2"},
        ])
        instance = probe.UserPlanePathProbe("gnb", status, [], monitor_n6=False, registry=CollectorRegistry())
        instance.discover_paths()
        self.assertEqual(instance.n3, probe.PathDiscovery("net1", "name_hint"))
        self.assertEqual(instance.path_confirmed.labels("gnb", "n3")._value.get(), 0)
        instance.rx_bytes.labels("gnb", "n3").set(42)
        instance.record_gtpu("rx", 100, "net2")
        self.assertEqual(instance.n3, probe.PathDiscovery("net2", "gtpu_confirmed"))
        self.assertEqual(instance.path_confirmed.labels("gnb", "n3")._value.get(), 1)
        self.assertFalse(instance.rx_bytes.collect()[0].samples)
        instance.collection_success.labels("gnb").set(1)
        instance.record_gtpu("rx", 100, "net1")
        self.assertEqual(instance.n3, probe.PathDiscovery("", "gtpu_ambiguous"))
        self.assertEqual(instance.path_confirmed.labels("gnb", "n3")._value.get(), 0)
        self.assertEqual(instance.path_ready.labels("gnb", "n3")._value.get(), 0)
        self.assertEqual(instance.collection_success.labels("gnb")._value.get(), 0)

    def test_gnb_without_name_hint_waits_for_traffic(self):
        status = json.dumps([{"name": "data-a", "interface": "net1"}, {"name": "data-b", "interface": "net2"}])
        instance = probe.UserPlanePathProbe("gnb", status, [], monitor_n6=False, registry=CollectorRegistry())
        instance.discover_paths()
        self.assertEqual(instance.n3, probe.PathDiscovery("", "name_hint_missing"))
        self.assertEqual(instance.path_confirmed.labels("gnb", "n3")._value.get(), 0)
        instance.record_gtpu("tx", 100, "net2")
        self.assertEqual(instance.n3, probe.PathDiscovery("net2", "gtpu_confirmed"))
        self.assertEqual(instance.path_confirmed.labels("gnb", "n3")._value.get(), 1)

    def test_idle_gnb_starts_pod_wide_observer_without_n3_candidate(self):
        instance = probe.UserPlanePathProbe("gnb", "[]", [], monitor_n6=False, registry=CollectorRegistry())
        with patch.object(probe.GtpUCaptureWorker, "start", return_value=True) as start:
            instance.refresh()
        start.assert_called_once()
        self.assertIsNone(instance.capture.interface)
        self.assertEqual(instance.path_ready.labels("gnb", "n3")._value.get(), 0)
        self.assertEqual(instance.path_confirmed.labels("gnb", "n3")._value.get(), 0)

    def test_pod_wide_capture_uses_receiving_interface(self):
        instance = probe.UserPlanePathProbe("gnb", "[]", [], monitor_n6=False, registry=CollectorRegistry())
        instance.discover_paths()
        worker = probe.GtpUCaptureWorker(None, instance)

        class OnePacketSocket:
            def recvfrom(self, _size):
                worker.stop_event.set()
                return ipv4_udp_frame(), ("net2", 0, probe.PACKET_OUTGOING, 0, b"")

            def close(self):
                pass

        worker.socket = OnePacketSocket()
        worker._run()
        self.assertEqual(instance.n3, probe.PathDiscovery("net2", "gtpu_confirmed"))
        self.assertEqual(instance.gtpu_seen.labels("gnb")._value.get(), 1)


class IntegrationTests(unittest.TestCase):
    def test_upf_role_uses_the_shared_probe(self):
        defaults = yaml.safe_load((ROOT / "roles/monitoring/sniffers/upf/defaults/main.yml").read_text())
        template = Environment().from_string((ROOT / "roles/monitoring/sniffers/upf/templates/upf-data-plane-probe-service.yaml.j2").read_text())
        service = yaml.safe_load(template.render(core="open5gs", upf_data_plane_probe_metrics_port=9103, upf_data_plane_probe_target_label_key="monitoring.5g.example/upf-data-plane-probe", upf_data_plane_probe_target_label_value="enabled"))
        tasks = (ROOT / "roles/monitoring/sniffers/upf/tasks/main.yml").read_text()
        self.assertFalse(defaults["upf_data_plane_probe_enabled"])
        self.assertEqual(defaults["upf_data_plane_probe_default_n3_network_names_by_core"]["open5gs"], ["n3network"])
        self.assertIn("user-plane-path-probe", defaults["upf_data_plane_probe_image"])
        self.assertEqual(service["spec"]["clusterIP"], "None")
        self.assertEqual(service["spec"]["selector"], {"monitoring.5g.example/upf-data-plane-probe": "enabled"})
        self.assertIn("USER_PLANE_PROBE_ANCHOR", tasks)
        self.assertIn('value: upf', tasks)
        self.assertIn('value: "true"', tasks)
        self.assertIn("NET_RAW", tasks)

    def test_gnb_role_uses_n3_only_with_no_vendor_selector(self):
        defaults = yaml.safe_load((ROOT / "roles/monitoring/sniffers/gnb/defaults/main.yml").read_text())
        template = Environment().from_string((ROOT / "roles/monitoring/sniffers/gnb/templates/gnb-data-plane-probe-service.yaml.j2").read_text())
        service = yaml.safe_load(template.render(gnb_data_plane_probe_namespace="open5gs", gnb_data_plane_probe_metrics_port=9103, gnb_data_plane_probe_target_label_key="monitoring.5g.example/gnb-data-plane-probe", gnb_data_plane_probe_target_label_value="enabled"))
        tasks = (ROOT / "roles/monitoring/sniffers/gnb/tasks/main.yml").read_text()
        self.assertFalse(defaults["gnb_data_plane_probe_enabled"])
        self.assertEqual(defaults["gnb_data_plane_probe_n3_network_names"], [])
        self.assertIn("user-plane-path-probe", defaults["gnb_data_plane_probe_image"])
        self.assertEqual(service["spec"]["clusterIP"], "None")
        self.assertEqual(service["spec"]["selector"], {"monitoring.5g.example/gnb-data-plane-probe": "enabled"})
        self.assertIn("USER_PLANE_PROBE_ANCHOR", tasks)
        self.assertIn('value: gnb', tasks)
        self.assertIn('value: "false"', tasks)
        self.assertNotIn("USER_PLANE_PROBE_N6_INTERFACE", tasks)
        self.assertIn("NET_RAW", tasks)

    def test_dockerfile_artifacts_and_deploy_wiring_are_present(self):
        dockerfile = (ROOT / "probes/user_plane_path/Dockerfile").read_text()
        queries = json.loads((ROOT / "configs/artifacts/default_prometheus_queries.json").read_text())
        names = [entry["name"] for entry in queries]
        deploy = (ROOT / "playbooks/deploy.yml").read_text()
        self.assertIn("COPY probes/user_plane_path/probe.py", dockerfile)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("user_plane_probe_path_ready", names)
        self.assertIn("user_plane_probe_path_confirmed", names)
        self.assertIn("user_plane_probe_gtpu_packets_per_second_30s", names)
        self.assertIn("monitoring/sniffers/gnb", deploy)
        self.assertIn("monitoring/sniffers/upf", deploy)
        self.assertGreater(
            deploy.index("Attach gNB user-plane probe after RAN deployment"),
            deploy.index("- name: Deploy srsRAN"),
        )


if __name__ == "__main__":
    unittest.main()

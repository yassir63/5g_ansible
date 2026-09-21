import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jinja2 import Environment
import yaml


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_SCRIPTS = ROOT / "scripts/artifacts"
sys.path.insert(0, str(ARTIFACT_SCRIPTS))
spec = importlib.util.spec_from_file_location("check_observability", ARTIFACT_SCRIPTS / "check_observability.py")
check_observability = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check_observability
spec.loader.exec_module(check_observability)


class MlLiveObservabilityTests(unittest.TestCase):
    def test_profile_enables_every_prepared_live_source(self):
        profile = yaml.safe_load((ROOT / "configs/monitoring/profiles/ml_live_observability.yml").read_text())
        self.assertTrue(profile["monitoring_enabled"])
        self.assertTrue(profile["monitoring_loki_enabled"])
        self.assertTrue(profile["ue_mapper_metrics_enabled"])
        self.assertTrue(profile["amf_ngap_metrics_enabled"])
        self.assertTrue(profile["smf_pfcp_metrics_enabled"])
        self.assertTrue(profile["gnb_data_plane_probe_enabled"])
        self.assertTrue(profile["upf_data_plane_probe_enabled"])
        self.assertEqual(
            profile["gnb_data_plane_probe_image"],
            profile["upf_data_plane_probe_image"],
        )

    def test_expectations_require_each_control_and_user_plane_anchor(self):
        expectations = check_observability.load_expectations(
            str(ROOT / "configs/monitoring/ml_live_expectations.json")
        )
        names = {item["name"] for item in expectations}
        self.assertEqual(len(names), len(expectations))
        self.assertTrue(all("== 1" in item["query"] for item in expectations))
        self.assertIn("amf_ngap_capture_started", names)
        self.assertIn("smf_pfcp_capture_started", names)
        self.assertIn("gnb_n3_path_ready", names)
        self.assertIn("upf_n6_path_ready", names)

    def test_rejects_duplicate_or_incomplete_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expectations.json"
            path.write_text(json.dumps([{"name": "same", "query": "up"}, {"name": "same", "query": "up"}]))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                check_observability.load_expectations(str(path))
            path.write_text(json.dumps([{"name": "missing-query"}]))
            with self.assertRaisesRegex(ValueError, "non-empty query"):
                check_observability.load_expectations(str(path))

    def test_srsran_gnb_latency_service_accepts_lowercase_ran_name(self):
        template = Environment().from_string(
            (ROOT / "roles/monitoring/kpi_calculators/templates/oai-ran-mde-services.yml.j2").read_text()
        )
        services = list(
            yaml.safe_load_all(
                template.render(monitoring_ran_namespace="open5gs", ran="srsran")
            )
        )
        latency_service = next(
            service
            for service in services
            if service["metadata"]["name"] == "gnb-latency-metrics-service"
        )
        self.assertEqual(
            latency_service["spec"]["selector"],
            {"app": "srsran", "component": "gnb"},
        )


if __name__ == "__main__":
    unittest.main()

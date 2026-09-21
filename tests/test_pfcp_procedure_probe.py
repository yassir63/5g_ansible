import importlib.util
import json
from pathlib import Path
import unittest

from jinja2 import Environment
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pfcp_procedure_metrics", ROOT / "probes/pfcp_procedure/metrics.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def metric_values(tracker):
    output = generate_latest(tracker.registry).decode()
    values = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(output)
        for sample in family.samples
    }
    return values, output


class TrackerTests(unittest.TestCase):
    def test_exact_accepted_response_is_correlated_and_timed(self):
        tracker = probe.PFCPSessionEstablishmentTracker()
        self.assertEqual(tracker.observe(50, 41, "smf|upf", observed_at=10), "request")
        self.assertEqual(tracker.observe(51, 41, "smf|upf", cause=1, observed_at=10.25), "accepted")
        values, _ = metric_values(tracker)
        self.assertEqual(values[("pfcp_session_establishment_requests_total", ())], 1)
        self.assertEqual(values[("pfcp_session_establishment_completed_total", (("outcome", "accepted"),))], 1)
        self.assertAlmostEqual(values[("pfcp_session_establishment_duration_seconds_sum", (("outcome", "accepted"),))], 0.25)

    def test_rejected_and_unknown_causes_are_kept_distinct(self):
        tracker = probe.PFCPSessionEstablishmentTracker()
        for sequence, cause, expected in ((1, 64, "rejected"), (2, None, "unknown")):
            tracker.observe(50, sequence, "smf|upf", observed_at=sequence)
            self.assertEqual(tracker.observe(51, sequence, "smf|upf", cause=cause, observed_at=sequence + 0.1), expected)
        values, _ = metric_values(tracker)
        self.assertEqual(values[("pfcp_session_establishment_responses_total", (("outcome", "rejected"),))], 1)
        self.assertEqual(values[("pfcp_session_establishment_responses_total", (("outcome", "unknown"),))], 1)

    def test_strict_correlation_quality_events_are_not_failures(self):
        now = [0.0]
        tracker = probe.PFCPSessionEstablishmentTracker(pending_timeout_seconds=5, clock=lambda: now[0])
        tracker.observe(50, 1, "smf|upf", observed_at=0)
        self.assertEqual(tracker.observe(50, 1, "smf|upf", observed_at=1), "overlapping_request")
        self.assertEqual(tracker.observe(51, 2, "smf|upf", cause=1, observed_at=2), "unmatched_response")
        self.assertEqual(tracker.observe(50, -1, "smf|upf", observed_at=2), "untrackable_request")
        now[0] = 5
        values, _ = metric_values(tracker)
        self.assertEqual(values[("pfcp_session_establishment_expired_total", ())], 1)
        self.assertEqual(values[("pfcp_session_establishment_overlapping_requests_total", ())], 1)
        self.assertEqual(values[("pfcp_session_establishment_unmatched_responses_total", ())], 1)
        self.assertEqual(values[("pfcp_session_establishment_untrackable_requests_total", ())], 1)

    def test_private_correlation_key_is_never_exported(self):
        tracker = probe.PFCPSessionEstablishmentTracker()
        tracker.observe(50, 7, "10.10.4.1|10.10.4.2", observed_at=0)
        tracker.observe(51, 7, "10.10.4.1|10.10.4.2", cause="Request accepted", observed_at=1)
        _, output = metric_values(tracker)
        self.assertNotIn("10.10.4.1", output)
        self.assertNotIn("10.10.4.2", output)


class IntegrationTests(unittest.TestCase):
    def test_smf_service_is_explicit_and_targeted_by_role_label(self):
        template = Environment().from_string((ROOT / "roles/monitoring/sniffers/smf/templates/smf-pfcp-metrics-service.yaml.j2").read_text())
        service = yaml.safe_load(template.render(core="open5gs", smf_pfcp_metrics_port=9104, smf_pfcp_target_label_key="monitoring.5g.example/smf-pfcp-probe", smf_pfcp_target_label_value="enabled"))
        self.assertEqual(service["spec"]["clusterIP"], "None")
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], 9104)
        self.assertEqual(service["metadata"]["annotations"]["prometheus.io/path"], "/metrics")
        self.assertEqual(service["spec"]["selector"], {"monitoring.5g.example/smf-pfcp-probe": "enabled"})

    def test_disabled_defaults_and_single_sniffer_extension_are_present(self):
        defaults = yaml.safe_load((ROOT / "roles/monitoring/sniffers/smf/defaults/main.yml").read_text())
        tasks = (ROOT / "roles/monitoring/sniffers/smf/tasks/main.yml").read_text()
        self.assertFalse(defaults["smf_pfcp_metrics_enabled"])
        self.assertEqual(defaults["smf_sniffer_image"], "r2labuser/smf-sniffer:2026w10")
        self.assertIn("smf-sniffer", tasks)
        self.assertIn("SMF_PFCP_METRICS_ENABLED", tasks)
        self.assertIn("cannot be upgraded in place", tasks)
        self.assertNotIn("pfcp-procedure-probe", tasks)

    def test_build_context_and_artifact_queries(self):
        dockerfile = (ROOT / "probes/smf_sniffer/Dockerfile").read_text()
        sniffer = (ROOT / "probes/smf_sniffer/smf_sniffer.py").read_text()
        queries = json.loads((ROOT / "configs/artifacts/default_prometheus_queries.json").read_text())
        names = [query["name"] for query in queries]
        self.assertIn("COPY probes/pfcp_procedure/metrics.py", dockerfile)
        self.assertIn("PFCPSessionEstablishmentTracker", sniffer)
        self.assertIn("pfcp_tracker.observe", sniffer)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("pfcp_session_establishment_p95_ms_30s", names)
        self.assertIn("pfcp_session_establishment_capture_started", names)


if __name__ == "__main__":
    unittest.main()

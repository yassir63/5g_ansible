import importlib.util
import json
from pathlib import Path
import unittest

from jinja2 import Environment
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ngap_procedure_probe", ROOT / "probes/ngap_procedure/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeLayer:
    def __init__(self, fields=(), values=None):
        self.field_names = list(fields)
        self.values = values or {}

    def get_field_values(self, name):
        if name not in self.values:
            raise KeyError(name)
        return self.values[name]

    def __str__(self):
        return " ".join(self.field_names)


def layer(kind, sessions=(1,), fields=()):
    return FakeLayer(
        [kind, *fields],
        {
            "aMF_UE_NGAP_ID": ["10"],
            "rAN_UE_NGAP_ID": ["20"],
            "pDUSessionID": [str(session) for session in sessions],
        },
    )


def metric_values(tracker):
    output = generate_latest(tracker.registry).decode()
    values = {(sample.name, tuple(sorted(sample.labels.items()))): sample.value
              for family in text_string_to_metric_families(output) for sample in family.samples}
    return values, output


class TrackerTests(unittest.TestCase):
    def test_exact_success_is_correlated_and_timed(self):
        tracker = probe.PduSessionSetupTracker()
        self.assertEqual(tracker.observe_layer(layer("PDUSessionResourceSetupRequest_element"), 10), "request")
        result = tracker.observe_layer(layer("PDUSessionResourceSetupResponse_element", fields=["PDUSessionResourceSetupListSURes"]), 10.4)
        values, _ = metric_values(tracker)
        self.assertEqual(result, "success")
        self.assertEqual(values["ngap_pdu_session_setup_requests_total", ()], 1)
        self.assertEqual(values["ngap_pdu_session_setup_completed_total", (("outcome", "success"),)], 1)
        self.assertAlmostEqual(values["ngap_pdu_session_setup_duration_seconds_sum", (("outcome", "success"),)], 0.4)

    def test_mixed_and_failure_stay_distinct(self):
        tracker = probe.PduSessionSetupTracker()
        for number, response_fields, expected in (
            (1, ["PDUSessionResourceSetupListSURes", "PDUSessionResourceFailedToSetupListSURes"], "mixed"),
            (2, ["PDUSessionResourceFailedToSetupListSURes"], "failure"),
        ):
            tracker.observe_layer(layer("PDUSessionResourceSetupRequest_element", (number,)), number)
            self.assertEqual(tracker.observe_layer(layer("PDUSessionResourceSetupResponse_element", (number,), response_fields), number + 0.1), expected)
        values, _ = metric_values(tracker)
        self.assertEqual(values["ngap_pdu_session_setup_responses_total", (("outcome", "mixed"),)], 1)
        self.assertEqual(values["ngap_pdu_session_setup_responses_total", (("outcome", "failure"),)], 1)

    def test_unclassified_response_is_not_promoted_to_success(self):
        tracker = probe.PduSessionSetupTracker()
        tracker.observe_layer(layer("PDUSessionResourceSetupRequest_element"), 1)
        self.assertEqual(tracker.observe_layer(layer("PDUSessionResourceSetupResponse_element"), 2), "unclassified")
        values, _ = metric_values(tracker)
        self.assertEqual(values["ngap_pdu_session_setup_completed_total", (("outcome", "unclassified"),)], 1)
        self.assertNotIn(("ngap_pdu_session_setup_completed_total", (("outcome", "success"),)), values)

    def test_mismatch_expiry_and_overlap_are_not_failures(self):
        now = [0.0]
        tracker = probe.PduSessionSetupTracker(pending_timeout_seconds=5, clock=lambda: now[0])
        tracker.observe_layer(layer("PDUSessionResourceSetupRequest_element", (1,)), 0)
        self.assertEqual(tracker.observe_layer(layer("PDUSessionResourceSetupRequest_element", (1,)), 1), "overlapping_request")
        self.assertEqual(tracker.observe_layer(layer("PDUSessionResourceSetupResponse_element", (2,)), 2), "unmatched_response")
        now[0] = 5
        values, _ = metric_values(tracker)
        self.assertEqual(values["ngap_pdu_session_setup_expired_total", ()], 1)
        self.assertEqual(values["ngap_pdu_session_setup_unmatched_responses_total", ()], 1)
        self.assertEqual(values["ngap_pdu_session_setup_overlapping_requests_total", ()], 1)

    def test_untrackable_and_ignored_messages(self):
        tracker = probe.PduSessionSetupTracker()
        incomplete = FakeLayer(["PDUSessionResourceSetupRequest_element"], {})
        self.assertEqual(tracker.observe_layer(incomplete, 0), "untrackable_request")
        self.assertEqual(tracker.observe_layer(FakeLayer(["UEContextReleaseCommand_element"]), 0), "ignored")
        tracker.record_decode_error()
        values, _ = metric_values(tracker)
        self.assertEqual(values["ngap_pdu_session_setup_untrackable_requests_total", ()], 1)
        self.assertEqual(values["ngap_pdu_session_setup_decode_errors_total", ()], 1)

    def test_metrics_never_expose_ue_identifiers(self):
        tracker = probe.PduSessionSetupTracker()
        record = layer("PDUSessionResourceSetupRequest_element", (7,))
        record.values["aMF_UE_NGAP_ID"] = ["123456"]
        record.values["rAN_UE_NGAP_ID"] = ["789012"]
        tracker.observe_layer(record, 0)
        _, output = metric_values(tracker)
        self.assertNotIn("123456", output)
        self.assertNotIn("789012", output)
        self.assertNotIn('"7"', output)


class IntegrationTests(unittest.TestCase):
    def test_probe_service_is_explicit_and_headless(self):
        template = Environment().from_string((ROOT / "roles/monitoring/sniffers/amf/templates/ngap-procedure-probe-service.yaml.j2").read_text())
        service = yaml.safe_load(template.render(core="open5gs", amf_ngap_probe_metrics_port=9102))
        self.assertEqual(service["spec"]["clusterIP"], "None")
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], 9102)
        self.assertEqual(service["metadata"]["annotations"]["prometheus.io/path"], "/metrics")
        self.assertEqual(service["spec"]["selector"], {"nf": "amf", "app": "monitoring"})

    def test_disabled_defaults_and_injection_guard_are_present(self):
        defaults = yaml.safe_load((ROOT / "roles/monitoring/sniffers/amf/defaults/main.yml").read_text())
        tasks = (ROOT / "roles/monitoring/sniffers/amf/tasks/main.yml").read_text()
        self.assertFalse(defaults["amf_ngap_probe_enabled"])
        self.assertEqual(defaults["amf_ngap_probe_image"], "")
        self.assertIn("ngap-procedure-probe", tasks)
        self.assertIn("amf_ngap_probe_enabled | bool", tasks)
        self.assertIn("cannot be changed", tasks)

    def test_build_context_and_artifact_queries(self):
        dockerfile = (ROOT / "probes/ngap_procedure/Dockerfile").read_text()
        self.assertIn("COPY probes/ngap_procedure/probe.py", dockerfile)
        queries = json.loads((ROOT / "configs/artifacts/default_prometheus_queries.json").read_text())
        names = [query["name"] for query in queries]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("ngap_pdu_session_setup_p95_ms_30s", names)
        self.assertIn("ngap_pdu_session_setup_capture_started", names)


if __name__ == "__main__":
    unittest.main()

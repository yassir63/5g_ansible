import unittest
from unittest.mock import patch

from check_observability import check_loki, check_targets, summarize_query


def matrix(*values):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {"pod": "upf"}, "values": [[index, value] for index, value in enumerate(values)]}],
        },
    }


class ObservabilityCheckTests(unittest.TestCase):
    def test_zero_is_available(self):
        result = summarize_query(matrix("0", "0"))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["finite_samples"], 2)

    def test_missing_is_not_zero(self):
        self.assertEqual(summarize_query(matrix())["status"], "empty")

    def test_nan_and_infinity_are_not_available(self):
        result = summarize_query(matrix("NaN", "+Inf", "-Inf"))
        self.assertEqual(result["status"], "non_finite")
        self.assertEqual(result["non_finite_samples"], 3)

    def test_mixed_samples_are_partial(self):
        self.assertEqual(summarize_query(matrix("NaN", "0"))["status"], "partial")

    def test_api_errors_are_distinct_from_empty(self):
        self.assertEqual(summarize_query({"status": "error", "error": "bad query"})["status"], "error")
        self.assertEqual(summarize_query({"status": "success", "data": {"resultType": "scalar"}})["status"], "error")

    @patch("check_observability.http_json")
    def test_down_targets_preserve_diagnostic_context(self, fetch):
        fetch.return_value = {"status": "success", "data": {"activeTargets": [
            {"labels": {"node": "ran"}, "health": "down", "lastError": "timeout"},
        ]}}
        target = check_targets("http://example")["targets"][0]
        self.assertEqual(target["health"], "down")
        self.assertEqual(target["last_error"], "timeout")
        self.assertEqual(target["labels"]["node"], "ran")

    @patch("check_observability.http_json")
    def test_loki_checks_ingestion_without_exporting_log_text(self, fetch):
        fetch.return_value = {"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"namespace": "open5gs"}, "values": [["123", "application log"]]},
        ]}}
        result = check_loki("http://example", '{namespace="open5gs"}', 100, 200)
        self.assertEqual(result["status"], "available")
        self.assertNotIn("application log", str(result))
        self.assertEqual(fetch.call_args.args[1]["start"], "100000000000")


if __name__ == "__main__":
    unittest.main()

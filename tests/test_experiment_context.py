import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("context_probe", ROOT / "probes/ue_context/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class InventoryTests(unittest.TestCase):
    def test_valid_empty_inventory(self):
        result = probe.inventory_record({"count": 0, "limit": 10, "ues": []}, 10)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["possible_truncation"])

    def test_at_limit_is_flagged_and_credentials_are_not_archived(self):
        result = probe.inventory_record({"count": 1, "limit": 1, "ues": [{"imsi": "001", "key": "secret"}]}, 1)
        self.assertTrue(result["possible_truncation"])
        self.assertEqual(result["ues"], [{"imsi": "001"}])

    def test_invalid_inventory_is_not_an_empty_success(self):
        for payload in ({}, {"ues": [None]}, {"ues": [], "limit": 0},
                        {"ues": [], "count": -1}, {"ues": [{"last_seen": float('nan')}]}):
            with self.assertRaises(ValueError):
                probe.inventory_record(payload, 10)

    def test_error_and_final_observation_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(out_dir=directory, stop_file=directory + "/STOP", run_id="test",
                                   max_seconds=10, interval_seconds=0.1, limit=10)
            calls = 0
            def fetch():
                nonlocal calls
                calls += 1
                if calls == 1:
                    Path(args.stop_file).touch()
                    raise OSError("mapper unavailable")
                return {"ues": [{"imsi": "001", "ul_teid": "0x1"}], "limit": 10}
            summary = probe.sample(args, fetch, threading.Event())
            rows = [json.loads(line) for line in Path(directory, "history.jsonl").read_text().splitlines()]
            self.assertEqual(summary["stop_reason"], "requested")
            self.assertEqual(summary["errors"], 1)
            self.assertNotIn("ues", rows[0])
            self.assertTrue(rows[-1]["final"])
            self.assertEqual(rows[-1]["ues"][0]["ul_teid"], "0x1")

    def test_deadline_without_stop_file(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(out_dir=directory, stop_file=directory + "/STOP", run_id="test",
                                   max_seconds=0.05, interval_seconds=0.1, limit=10)
            summary = probe.sample(args, lambda: {"ues": []}, threading.Event())
            self.assertEqual(summary["stop_reason"], "max_seconds")


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook required for lifecycle tests")
class LifecycleTests(unittest.TestCase):
    def run_experiment(self, failed=False, enabled=True, dry_run=False, invalid=False):
        with tempfile.TemporaryDirectory(prefix="context-lifecycle-") as directory:
            temp = Path(directory)
            fake = temp / "kubectl"
            fake.write_text('''#!/usr/bin/env python3
import json, sys
if '--raw' in sys.argv:
    print(json.dumps({'count': 1, 'limit': 5000, 'ues': [{'imsi': '001', 'ue_ip': '12.1.1.1', 'slice_id': '01:ffffff', 'ul_teid': '0x1'}]}))
elif 'version' in sys.argv:
    print(json.dumps({'serverVersion': {'gitVersion': 'test'}}))
else:
    print(json.dumps({'items': []}))
''')
            fake.chmod(0o700)
            scenario = {
                "name": "context_test", "collect": {
                    "enabled": enabled, "control_host": "localhost",
                    "setup": {"enabled": True, "hosts": ["localhost"]},
                    "ue_context": {"enabled": True, "interval_seconds": -1 if invalid else 0.1, "max_seconds": 60},
                    "prometheus": {"enabled": False}, "pod_logs": {"enabled": False}, "pcaps": {"enabled": False},
                },
                "sections": [{"name": "test", "runner": {"type": "command", "command": "exit 7" if failed else "sleep 1"}}],
            }
            config = temp / "scenario.json"
            config.write_text(json.dumps(scenario))
            output = temp / "results"
            ansible_config = temp / "ansible.cfg"
            ansible_config.write_text("[defaults]\nstdout_callback=default\n")
            extra = {"experiment_scenario_file": str(config), "experiment_results_dir": str(output),
                     "experiment_dry_run": dry_run, "ansible_python_interpreter": shutil.which("python3")}
            env = {**os.environ, "PATH": str(temp) + os.pathsep + os.environ["PATH"],
                   "ANSIBLE_CONFIG": str(ansible_config), "ANSIBLE_LOCAL_TEMP": str(temp / "ansible-local"),
                   "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
            run = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local",
                                  str(ROOT / "playbooks/run_experiment.yml"), "-e", json.dumps(extra)],
                                 env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)
            log = run.stdout + run.stderr
            if invalid:
                self.assertNotEqual(run.returncode, 0, log)
                self.assertIn("Invalid setup/UE context settings", log)
                self.assertFalse(output.exists())
                return
            if dry_run:
                self.assertEqual(run.returncode, 0, log)
                self.assertFalse(output.exists(), log)
                return
            self.assertEqual(run.returncode != 0, failed, log)
            self.assertTrue((output / "experiment_status.json").exists(), log)
            self.assertEqual(json.loads((output / "experiment_status.json").read_text())["failed"], failed)
            if not enabled:
                self.assertFalse((output / "setup").exists())
                self.assertFalse((output / "ue_context").exists())
                return
            for phase in ("before", "after"):
                self.assertTrue((output / "setup" / phase / "deployment.json").exists(), log)
                cluster = json.loads((output / "setup" / phase / "cluster.json").read_text())
                self.assertEqual(json.loads(cluster["stdout"])["data"]["pods"]["status"], "ok", log)
            summary = json.loads((output / "ue_context/summary.json").read_text())
            self.assertEqual(summary["stop_reason"], "requested", log)
            self.assertEqual(summary["errors"], 0, log)
            rows = [json.loads(line) for line in (output / "ue_context/history.jsonl").read_text().splitlines()]
            self.assertTrue(rows[-1]["final"])
            self.assertGreaterEqual(len(rows), 2)
            status = json.loads((output / "ue_context/collection_status.json").read_text())
            self.assertTrue(status["exit"]["finished"])
            self.assertFalse(Path(status["remote_directory"]).exists(), log)
            events = [json.loads(line) for line in (output / "timeline_events.jsonl").read_text().splitlines()]
            self.assertTrue(any(e.get("level") == "campaign" and e.get("phase") == "end" for e in events))

    def test_success(self):
        self.run_experiment()

    def test_failure_still_collects_and_stops(self):
        self.run_experiment(failed=True)

    def test_disabled(self):
        self.run_experiment(enabled=False)

    def test_dry_run_has_no_side_effects(self):
        self.run_experiment(dry_run=True)

    def test_invalid_interval_rejected_before_execution(self):
        self.run_experiment(invalid=True)


if __name__ == "__main__":
    unittest.main()

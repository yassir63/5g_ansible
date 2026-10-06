import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


def load_controller():
    decorator = lambda *args, **kwargs: lambda function: function
    kopf = ModuleType("kopf")
    kopf.on = SimpleNamespace(startup=decorator)
    kopf.timer = decorator
    kopf.OperatorSettings = object
    kubernetes = ModuleType("kubernetes")
    requests = ModuleType("requests")
    spec = importlib.util.spec_from_file_location(
        "k8scontroller", Path(__file__).with_name("k8scontroller.py")
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "kopf": kopf, "kubernetes": kubernetes, "requests": requests
    }):
        spec.loader.exec_module(module)
    return module


def ue(imsi, ul, dl, ran_id):
    return {
        "imsi": imsi, "ran_ue_id": ran_id, "ue_ip": f"12.1.1.{ran_id}",
        "slice_id": "01:100000", "sst": "01", "sd": "100000",
        "ul_teid": f"0x{ul}", "dl_teid": f"0x{dl}",
        "teid_args": f"0x{ul}:0x{dl}@01:100000",
    }


class ControllerMappingTests(unittest.TestCase):
    def test_inventory_uses_only_complete_nonconflicting_ues(self):
        controller = load_controller()
        rows = [
            ue("001010000000006", "00000001", "000000a1", 1),
            ue(None, "00000002", "000000a2", 2),
            ue("001010000000008", "00000003", "000000a3", 3),
        ]
        response = SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"ues": rows}
        )
        controller.requests.get = Mock(return_value=response)
        teids, _, metadata, _, stats = controller.fetch_all_teids_from_ue_mapper(Mock())
        self.assertEqual(stats["complete_ues"], 2)
        self.assertEqual(stats["incomplete_ues"], 1)
        self.assertNotIn("00000002", teids)
        self.assertNotIn("00000002", metadata)

    def test_duplicate_teid_excludes_both_owners(self):
        controller = load_controller()
        rows = [
            ue("001010000000006", "00000009", "818e1a82", 1),
            ue("001010000000006", "00000009", "2ec1c176", 6),
        ]
        complete, conflicts = controller.filter_conflicting_ue_teids(rows)
        self.assertEqual(complete, [])
        self.assertEqual(conflicts, 2)

    def test_partial_mapping_waits_then_proceeds(self):
        controller = load_controller()
        target = {"role": "gnb"}
        logger = Mock()
        stats = {"complete_ues": 2, "incomplete_ues": 1}
        with patch.object(controller.time, "time", side_effect=[0, 20, 59, 60]):
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "a", stats, logger))
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "a", stats, logger))
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "a", stats, logger))
            self.assertTrue(controller.mapping_is_stable("oai", "gnb-1", target, "a", stats, logger))

    def test_completed_inventory_clears_grace(self):
        controller = load_controller()
        target = {"role": "gnb"}
        logger = Mock()
        partial = {"complete_ues": 1, "incomplete_ues": 1}
        complete = {"complete_ues": 2, "incomplete_ues": 0}
        with patch.object(controller.time, "time", side_effect=[0, 20]):
            controller.mapping_is_stable("oai", "gnb-1", target, "a", partial, logger)
            controller.mapping_is_stable("oai", "gnb-1", target, "b", complete, logger)
        self.assertEqual(controller.partial_mapping_since, {})

    def test_completed_inventory_proceeds_without_partial_grace(self):
        controller = load_controller()
        target = {"role": "gnb"}
        logger = Mock()
        partial = {"complete_ues": 1, "incomplete_ues": 1}
        complete = {"complete_ues": 2, "incomplete_ues": 0}
        with patch.object(controller.time, "time", side_effect=[0, 5, 10, 15, 20]):
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "a", partial, logger))
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "b", complete, logger))
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "b", complete, logger))
            self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "b", complete, logger))
            self.assertTrue(controller.mapping_is_stable("oai", "gnb-1", target, "b", complete, logger))
        self.assertEqual(controller.partial_mapping_since, {})

    def test_zero_complete_ues_never_proceeds(self):
        controller = load_controller()
        target = {"role": "gnb"}
        stats = {"complete_ues": 0, "incomplete_ues": 3}
        self.assertFalse(controller.mapping_is_stable("oai", "gnb-1", target, "a", stats, Mock()))
        self.assertEqual(controller.partial_mapping_since, {})


if __name__ == "__main__":
    unittest.main()

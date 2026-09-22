from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReloadBooleansTests(unittest.TestCase):
    def test_missing_optional_inventory_booleans_default_to_false(self):
        tasks = yaml.safe_load((ROOT / "tasks/reload_booleans.yml").read_text())
        normalization = next(
            task
            for task in tasks
            if task["name"] == "Force inventory booleans to real booleans"
        )
        value = next(iter(normalization["set_fact"].values()))
        self.assertIn(".get(item, false)", value)
        self.assertIn("| bool", value)


if __name__ == "__main__":
    unittest.main()

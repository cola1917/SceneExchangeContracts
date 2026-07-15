import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


class ConformanceTests(unittest.TestCase):
    def test_canonical_inventory_and_examples(self):
        from scene_exchange_contracts.conformance import run_conformance_suite

        report = run_conformance_suite()
        self.assertEqual(report["schema_count"], 14)
        self.assertGreaterEqual(report["example_count"], 9)


if __name__ == "__main__":
    unittest.main()

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.scoring import METRIC_DEFINITIONS, match_data


class ScoringTests(unittest.TestCase):
    def test_four_metrics_are_defined_before_scoring(self):
        self.assertEqual(set(METRIC_DEFINITIONS), {"semantic_exact", "data_accuracy", "token_usage", "e2e_ms"})

    def test_matches_exact_and_float_tolerance(self):
        actual = {"data": {"symbol": "ACME", "price": 10.004}}
        expected = {"data": {"symbol": "ACME", "price": 10.0}}
        rule = {"fields": {"data.symbol": "exact", "data.price": {"mode": "float_tolerance", "absolute": 0.01}}}
        self.assertTrue(match_data(actual, expected, rule))
        self.assertFalse(match_data({"data": {"symbol": "ACME", "price": 10.02}}, expected, rule))


if __name__ == "__main__":
    unittest.main()

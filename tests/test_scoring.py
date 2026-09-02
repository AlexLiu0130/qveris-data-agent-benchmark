import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.scoring import METRIC_DEFINITIONS, TokenCostPolicy, derive_token_usage, match_data


class ScoringTests(unittest.TestCase):
    def test_four_metrics_are_defined_before_scoring(self):
        self.assertEqual(set(METRIC_DEFINITIONS), {"semantic_exact", "data_accuracy", "token_usage", "e2e_ms"})

    def test_matches_exact_and_float_tolerance(self):
        actual = {"data": {"symbol": "ACME", "price": 10.004}}
        expected = {"data": {"symbol": "ACME", "price": 10.0}}
        rule = {"fields": {"data.symbol": "exact", "data.price": {"mode": "float_tolerance", "absolute": 0.01}}}
        self.assertTrue(match_data(actual, expected, rule))
        self.assertFalse(match_data({"data": {"symbol": "ACME", "price": 10.02}}, expected, rule))

    def test_same_usage_receipt_has_policy_owned_cost_metrics(self):
        receipt = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        self.assertEqual(derive_token_usage(receipt)["cost"], "unknown")
        priced = derive_token_usage(receipt, TokenCostPolicy(0.1, 0.2))
        self.assertEqual(priced["cost"], 2.0)
        self.assertEqual(priced["total_tokens"], 15)

    def test_agent_module_has_no_metric_dependency(self):
        agent_source = (pathlib.Path(__file__).parents[1] / "src" / "qveris_benchmark" / "agent.py").read_text()
        self.assertNotIn(".scoring", agent_source)


if __name__ == "__main__":
    unittest.main()

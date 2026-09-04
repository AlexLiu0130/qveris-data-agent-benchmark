import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("financial_scored_diagnostic", ROOT / "scripts" / "run_financial_diagnostic_scored.py")
DIAGNOSTIC = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(DIAGNOSTIC)


class FinancialRunnerScorerArenaTests(unittest.TestCase):
    def test_synthetic_checkpoints_are_scored_without_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            summaries = DIAGNOSTIC.run_all(ROOT, pathlib.Path(directory) / "runs")
        self.assertEqual([(item["checkpoint"], item["cells"], item["assertions"]) for item in summaries], [("A", 1, 37), ("B", 2, 111), ("C", 30, 1347)])
        self.assertTrue(all(item["client_calls"] == item["cells"] and item["resume_additional_calls"] == 0 for item in summaries))
        self.assertTrue(all(item["projection_status"] == "SCORED_NOT_RANKED" and item["token_usage"] == "unknown" for item in summaries))
        self.assertTrue(all(item["receipt_coverage"] == {"available": 0, "denominator": item["cells"], "value": 0.0} for item in summaries))


if __name__ == "__main__":
    unittest.main()

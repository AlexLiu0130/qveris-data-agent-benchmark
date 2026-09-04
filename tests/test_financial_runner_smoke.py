import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("financial_runner_smoke", ROOT / "scripts" / "run_financial_runner_smoke.py")
SMOKE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SMOKE)


class FinancialRunnerSmokeTests(unittest.TestCase):
    def test_single_variant_diagnostic_runs_one_cell_once(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = SMOKE.run_single_variant_smoke(ROOT, pathlib.Path(directory) / "run")
        self.assertEqual(summary["profile"], "single-variant-smoke")
        self.assertEqual(summary["compiled_cases"], ["FS-046"])
        self.assertEqual(summary["variant_ids"], ["synthetic-financial-a"])
        self.assertEqual(summary["client_calls"], 1)
        self.assertEqual(summary["resume_additional_calls"], 0)
        self.assertEqual(summary["event_counts"], {"dispatch_intent": 1, "run_finished": 1, "run_started": 1, "terminal": 1})
        self.assertEqual(summary["internal_status"], "execution_complete")
        self.assertEqual(summary["snapshot_status"], "incomplete")
        self.assertEqual(summary["projection_status"], "UNSCORED")
        self.assertTrue(summary["journal_hash_chain_valid"])
        self.assertTrue(summary["manifest_immutable"])
        self.assertEqual(summary["usage"], "unknown")

    def test_compiles_and_runs_exactly_four_synthetic_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = SMOKE.run_smoke(ROOT, pathlib.Path(directory) / "run")
        self.assertEqual(summary["compiled_cases"], ["FS-046", "FS-050"])
        self.assertEqual(summary["client_calls"], 4)
        self.assertEqual(summary["resume_additional_calls"], 0)
        self.assertEqual(summary["event_counts"], {"dispatch_intent": 4, "run_finished": 1, "run_started": 1, "terminal": 4})
        self.assertEqual(summary["internal_status"], "execution_complete")
        self.assertEqual(summary["snapshot_status"], "incomplete")
        self.assertTrue(summary["journal_hash_chain_valid"])
        self.assertTrue(summary["manifest_immutable"])
        self.assertEqual(summary["usage"], "unknown")

    def test_rejects_illegal_execution_evidence(self):
        manifest, responses = SMOKE.compile_smoke(ROOT)
        variant = manifest["variants"][0]
        client = SMOKE.FrozenOracleFakeClient(variant, responses)
        result = client.run(manifest["cases"][0]["query"], request_id="attempt-test", idempotency_key="idem-test")
        illegal = SMOKE.ExecutionEvidence(**SMOKE._variant_identity(variant), agent_invocations=1, tool_executions=1, structured_outputs=1, tools_used=("Search",))
        with self.assertRaises(SMOKE.RunBackendError):
            SMOKE.RunService._project_result(SMOKE.PublicGetResult(result.public_response, illegal), variant)

    def test_explicit_output_does_not_create_default_artifacts_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "root"
            output = pathlib.Path(directory) / "external" / "run-store"
            with (
                mock.patch.object(SMOKE.sys, "argv", ["smoke", "--root", str(root), "--output", str(output)]),
                mock.patch.object(SMOKE, "run_smoke", return_value={}) as runner,
            ):
                SMOKE.main()
            runner.assert_called_once_with(root, output)
            self.assertFalse((root / "artifacts").exists())


if __name__ == "__main__":
    unittest.main()

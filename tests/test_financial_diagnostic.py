import copy
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark import financial_diagnostic as diagnostic
from qveris_benchmark.benchmark_scorer import _validate_bundle, _validate_policy
from qveris_benchmark.run_backend import RunStore


ROOT = pathlib.Path(__file__).parents[1]
VALIDATOR_SPEC = importlib.util.spec_from_file_location("financial_diagnostic_validator", ROOT / "scripts" / "validate_financial_diagnostic_30.py")
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)
VARIANTS = [{
    "variant_id": "synthetic-financial-v1",
    "stable_display_order": 1,
    "agent_variant_id": "synthetic-agent-v1",
    "agent_version": "synthetic-v1",
    "get_variant_id": "synthetic-public-get-v1",
    "get_version": "synthetic-v1",
    "model_identifier": "synthetic-no-model-v1",
    "model_version": "synthetic-v1",
    "model_config_digest": diagnostic.digest({"synthetic": True, "version": 1}),
}]


class FinancialDiagnosticCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiled = diagnostic.compile_with_digest(ROOT, variants=VARIANTS)

    def test_compiles_deterministically_from_the_frozen_release(self):
        repeated = diagnostic.compile_with_digest(ROOT, variants=VARIANTS)
        self.assertEqual(self.compiled, repeated)
        self.assertEqual(self.compiled["source_summary"], {
            "candidate_cases": 100,
            "frozen_normal_cases": 80,
            "selected_cases": 30,
            "selected_contracts": 27,
            "scoring_assertions": 1347,
            "release_id": "financial-statements-v1-20260903-r27-c80-a1198",
        })
        self.assertEqual(self.compiled["scoring_policy"]["metric_names"], ["semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage"])
        self.assertIsNone(self.compiled["scoring_policy"]["ranking"])
        run = self.compiled["run_config"]
        self.assertEqual((run["mode"], run["concurrency"], len(run["variants"]), len(run["cases"])), ("diagnostic", 1, 1, 30))
        with tempfile.TemporaryDirectory() as directory:
            stored, _ = RunStore(directory).create(run)
        self.assertEqual(stored, run)
        self.assertEqual(_validate_policy(self.compiled["scoring_policy"]), self.compiled["scoring_policy"])
        self.assertEqual(_validate_bundle(self.compiled["oracle_bundle"], run, self.compiled["scoring_policy"]), self.compiled["oracle_bundle"]["oracles"])

    def test_per_case_oracles_preserve_frozen_special_assertion_metadata(self):
        oracles = self.compiled["oracle_bundle"]["oracles"]
        self.assertEqual(len(oracles), 30)
        nil = [atom for oracle in oracles.values() for atom in oracle["data_assertions"] if atom["operator"] == "canonical_zero_from_display_nil"]
        self.assertEqual(len(nil), 2)
        self.assertTrue(all(atom["raw_display"] == "–" and atom["response_root"] == "data" and atom["weight"] == 1 and atom["fatal"] is True for atom in nil))
        normalized = [atom for oracle in oracles.values() for atom in oracle["data_assertions"] if atom["operator"] == "exact_normalized"]
        self.assertEqual(len(normalized), 1344)
        self.assertTrue(all(atom["unit"] and atom["tolerance"] is None for atom in normalized))
        self.assertIn("|", next(atom["field"] for atom in oracles["financial-diagnostic-fs-048"]["data_assertions"] if "|" in atom["field"]))
        provenance = self.compiled["frozen_assertion_provenance"]
        self.assertEqual(provenance["financial-diagnostic-fs-001"][0]["comparison"], "exact_normalized")
        self.assertEqual(oracles["financial-diagnostic-fs-001"]["source_ref"], oracles["financial-diagnostic-fs-004"]["source_ref"])
        self.assertNotEqual(oracles["financial-diagnostic-fs-001"]["oracle_id"], oracles["financial-diagnostic-fs-004"]["oracle_id"])
        self.assertEqual(oracles["financial-diagnostic-fs-001"]["independence"], "independent_frozen")

    def test_candidate_single_source_metadata_cannot_override_frozen_contract(self):
        original = diagnostic._load_json

        def changed(path):
            value = original(path)
            if path.name == "financial_statements.cases.json":
                value = copy.deepcopy(value)
                value[0]["data_oracle"]["oracle_status"] = "unreviewed_candidate_metadata"
            return value

        with mock.patch.object(diagnostic, "_load_json", side_effect=changed):
            compiled = diagnostic.compile_with_digest(ROOT, variants=VARIANTS)
        self.assertEqual(compiled["oracle_bundle"]["oracles"]["financial-diagnostic-fs-001"]["independence"], "independent_frozen")

    def test_missing_case_contract_and_review_fail_closed(self):
        original = diagnostic._load_json

        def missing_case(path):
            value = original(path)
            if path.name == "financial_statements.cases.json":
                value = copy.deepcopy(value)
                value[0]["id"] = "FS-999"
            return value

        with mock.patch.object(diagnostic, "_load_json", side_effect=missing_case):
            with self.assertRaisesRegex(diagnostic.FinancialDiagnosticError, "selected candidate case is missing"):
                diagnostic.compile_with_digest(ROOT, variants=VARIANTS)

        def bad_contract(path):
            value = original(path)
            if path.name == "financial_statements.cases.json":
                value = copy.deepcopy(value)
                value[0]["fact_contract_ref"] = "missing-contract"
            return value

        with mock.patch.object(diagnostic, "_load_json", side_effect=bad_contract):
            with self.assertRaisesRegex(diagnostic.FinancialDiagnosticError, "frozen fact contract"):
                diagnostic.compile_with_digest(ROOT, variants=VARIANTS)

        def missing_review(path):
            value = original(path)
            if path.name == "review-ledger.json":
                value = copy.deepcopy(value)
                for ledger in value["review_ledgers"]:
                    if ledger["oracle_id"] == "cn-600519-fy2024-consolidated-income-statement":
                        ledger["reviews"] = [review for review in ledger["reviews"] if review.get("role") != "semantic_reviewer"]
            return value

        with mock.patch.object(diagnostic, "_load_json", side_effect=missing_review):
            with self.assertRaisesRegex(diagnostic.FinancialDiagnosticError, "required reviewer approvals"):
                diagnostic.compile_with_digest(ROOT, variants=VARIANTS)

    def test_frozen_manifest_hash_mismatch_fails_closed(self):
        original = diagnostic._sha256_file

        def bad_hash(path):
            if path.name == "manifest.json":
                return "0" * 64
            return original(path)

        with mock.patch.object(diagnostic, "_sha256_file", side_effect=bad_hash):
            with self.assertRaisesRegex(diagnostic.FinancialDiagnosticError, "manifest hash mismatch"):
                diagnostic.compile_with_digest(ROOT, variants=VARIANTS)


class FinancialDiagnosticValidatorTests(unittest.TestCase):
    def test_rejects_relative_path_escape_and_absolute_release_paths(self):
        original = VALIDATOR.load
        selection_path = (ROOT / VALIDATOR.SELECTION).resolve()
        for bad_path in ("../outside.json", str(ROOT / "README.md")):
            def escaped(path, *, bad_path=bad_path):
                value = original(path)
                if path == selection_path:
                    value = copy.deepcopy(value)
                    value["frozen_financial_release"]["manifest_path"] = bad_path
                return value

            with mock.patch.object(VALIDATOR, "load", side_effect=escaped):
                with self.assertRaisesRegex(ValueError, "contained relative path"):
                    VALIDATOR.main(ROOT)


if __name__ == "__main__":
    unittest.main()

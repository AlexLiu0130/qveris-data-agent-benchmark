from __future__ import annotations

import copy
import json
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile
import unittest

from qveris_benchmark.v2_compiler import CompileError, compile_v2
from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunService, RunStore, _digest as _payload_digest, _variant_contract_digest, _variant_identity
from qveris_benchmark.benchmark_scorer import BenchmarkScoreError, BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION, _validate_bundle


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _score_policy() -> dict:
    contracts = {
        "success": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]},
        "partial": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]},
        "needs_clarification": {"required_non_null_paths": ["clarification"], "required_null_paths": ["data", "terminal_reason"]},
        "unsupported": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
        "no_data": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
        "error": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
    }
    return {
        "schema_version": "score-policy/v1", "metric_names": ["semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage"], "percentile_method": "nearest_rank",
        "assertion_operators": ["exact", "within_abs"], "operator_registry": ["exact", "within_abs"], "case_pass_gate": ["schema_valid", "status_correct", "semantic_pass", "data_pass", "execution_complete"],
        "completeness": {}, "response_schema_version": "get-response/v1", "response_status_contracts": contracts, "max_reference_window_seconds": 60,
        "error": "disabled", "timeout_latency_treatment": "observed", "usage_receipt_required_fields": ["receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"],
        "trusted_receipt_issuers": ["runner"], "eligibility": None, "ranking": None,
    }


class _CompiledOracleClient:
    def __init__(self, variant: dict, response: dict):
        self.variant, self.response = variant, response

    def run(self, _query: str, **kwargs: object) -> PublicGetResult:
        response = copy.deepcopy(self.response)
        response["resolved_request"]["accepted_variant_id"] = self.variant["variant_id"]
        response["meta"]["usage"]["request_id"] = str(kwargs["request_id"]).replace("request-", "attempt-", 1)
        return PublicGetResult(response, ExecutionEvidence(**_variant_identity(self.variant), agent_invocations=1, tool_executions=1, structured_outputs=1, tools_used=("get",)))


class V2CompilerTests(unittest.TestCase):
    @staticmethod
    def _variants():
        return [
            {"variant_id": "agent-a", "stable_display_order": 1, "agent_variant_id": "agent-a", "agent_version": "v1", "get_variant_id": "get-a", "get_version": "v1", "model_identifier": "model-a", "model_version": "v1", "model_config_digest": "a" * 64},
            {"variant_id": "agent-b", "stable_display_order": 2, "agent_variant_id": "agent-b", "agent_version": "v1", "get_variant_id": "get-b", "get_version": "v1", "model_identifier": "model-b", "model_version": "v1", "model_config_digest": "b" * 64},
        ]

    def test_compiles_all_cases_and_keeps_dynamic_realtime_unscored(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            result = compile_v2(ROOT, output)
            bundle = json.loads(result["oracle_bundle"].read_text(encoding="utf-8"))
            manifest = json.loads(result["run_manifest"].read_text(encoding="utf-8"))
        self.assertEqual(bundle["schema_version"], "oracle-bundle/v2")
        self.assertEqual(len(bundle["oracles"]), 300)
        self.assertEqual(bundle["expected_status_counts"]["financial_statements"], {"success": 88, "needs_clarification": 5, "no_data": 7})
        self.assertEqual(manifest["schema_version"], "runner-run-manifest-template/v2")
        self.assertEqual(len(manifest["cases"]), 300)
        self.assertEqual(sum(oracle["data_not_scored_until_receipt"] for oracle in bundle["oracles"].values()), 90)
        by_source_case = {oracle["source_case_id"]: oracle for oracle in bundle["oracles"].values()}
        self.assertEqual(by_source_case["FS-001"]["runtime_contract"]["response_data_path"], "data.facts")
        self.assertEqual(by_source_case["hist-A股-01"]["runtime_contract"]["response_data_path"], "data.bars.d")
        self.assertEqual(by_source_case["RTQ-002"]["runtime_contract"]["response_data_path"], "data.quote.fields")
        self.assertEqual(len({oracle["case_id"] for oracle in bundle["oracles"].values()}), 300)
        self.assertTrue(all(oracle["case_id"].isascii() and oracle["oracle_id"].isascii() for oracle in bundle["oracles"].values()))
        fact = by_source_case["FS-001"]["data_assertions"][0]
        self.assertEqual(fact["path"], "data.facts.is_002")
        self.assertEqual(fact["expected"]["assertion_id"], "is-002")
        self.assertIs(fact["expected"]["nil"], False)
        self.assertIsInstance(fact["expected"]["nil"], bool)
        historical = by_source_case["hist-港股-06"]["alternative_assertion_sets"]
        self.assertTrue(all({item["semantic_assertions"][1]["path"], item["data_assertions"][0]["path"]} == {"resolved_request.accepted_variant_id", "data.accepted_variant_id"} for item in historical))
        self.assertFalse(any("last_price" in json.dumps(oracle["data_assertions"], ensure_ascii=False) for oracle in bundle["oracles"].values() if oracle["suite"] == "realtime_quote"))

    def test_source_hash_tampering_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "benchmarks"
            shutil.copytree(ROOT, copied)
            candidate = copied / "candidates/v0.2/financial_statements.cases.json"
            candidate.write_text(candidate.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "hash mismatch"):
                compile_v2(copied, Path(temp) / "out")

    def test_variants_require_a_realtime_reference_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CompileError, "variants require a realtime reference contract"):
                compile_v2(ROOT, Path(temp) / "out", variants=self._variants())

    def test_real_runtime_inputs_emit_a_runner_accepted_diagnostic_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            result = compile_v2(ROOT, Path(temp) / "out", run_id="v2-ready", variants=self._variants(), reference_contract={"source_contract_hash": "c" * 64, "window_rule_version": "reference-window.v1"})
            manifest = json.loads(result["run_manifest"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "runner-run-manifest/v2")
            self.assertEqual(manifest["compile_status"], "ready")
            self.assertTrue(all("reference_contract" in case for case in manifest["cases"] if case["suite"] == "realtime_quote"))
            RunStore(Path(temp) / "runs").create(manifest)
            _validate_bundle(json.loads(result["oracle_bundle"].read_text(encoding="utf-8")), manifest)

    def test_v2_bundle_rejects_unknown_root_and_oracle_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            result = compile_v2(ROOT, Path(temp) / "out", variants=self._variants(), reference_contract={"source_contract_hash": "c" * 64, "window_rule_version": "reference-window.v1"})
            manifest = json.loads(result["run_manifest"].read_text(encoding="utf-8"))
            bundle = json.loads(result["oracle_bundle"].read_text(encoding="utf-8"))
            root_unknown = copy.deepcopy(bundle); root_unknown["unexpected"] = "nope"
            with self.assertRaises(BenchmarkScoreError):
                _validate_bundle(root_unknown, manifest)
            oracle_unknown = copy.deepcopy(bundle)
            next(iter(oracle_unknown["oracles"].values()))["unexpected"] = "nope"
            with self.assertRaises(BenchmarkScoreError):
                _validate_bundle(oracle_unknown, manifest)

    def test_real_compiled_oracle_loads_and_scores_with_canonical_latency_metric(self):
        with tempfile.TemporaryDirectory() as temp:
            result = compile_v2(ROOT, Path(temp) / "out", run_id="v2-score", variants=self._variants(), reference_contract={"source_contract_hash": "c" * 64, "window_rule_version": "reference-window.v1"})
            compiled_manifest = json.loads(result["run_manifest"].read_text(encoding="utf-8"))
            compiled_bundle = json.loads(result["oracle_bundle"].read_text(encoding="utf-8"))
            _validate_bundle(compiled_bundle, compiled_manifest)
            case = next(item for item in compiled_manifest["cases"] if item["suite"] == "financial_statements" and item["score_case"]["expected_status"] == ["success"])
            oracle = compiled_bundle["oracles"][case["score_case"]["oracle_id"]]
            score_bundle = {"schema_version": "oracle-bundle/v2", "oracles": {oracle["oracle_id"]: oracle}}
            policy = _score_policy()
            manifest = copy.deepcopy(compiled_manifest)
            manifest["cases"] = [case]
            manifest["policy"] = {"version": "test-v2-score"}
            manifest["oracle_bundle_digest"] = _payload_digest(score_bundle)
            manifest["scoring_contract"] = {
                "policy_digest": _payload_digest(policy), "oracle_bundle_digest": _payload_digest(score_bundle), "scorer_version": SCORER_VERSION,
                "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(manifest["variants"]),
            }
            facts = {assertion["path"].rsplit(".", 1)[1]: assertion["expected"] for assertion in oracle["data_assertions"]}
            response = {
                "schema_version": "get-response/v1", "status": "success", "resolved_request": {"suite": "financial_statements", "accepted_variant_id": "agent-a"},
                "data": {"kind": "financial_statement", "facts": facts}, "as_of": "2026-09-04T00:00:00Z", "source": "compiled-test", "clarification": None, "terminal_reason": None,
                "meta": {"usage": {"receipt_id": "receipt-1", "measurement_version": "usage-v1", "cache_status": "miss", "request_id": "pending", "issuer": "runner", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}},
            }
            store = RunStore(Path(temp) / "runs")
            service = RunService(store, {variant["variant_id"]: _CompiledOracleClient(variant, response) for variant in manifest["variants"]})
            service.create_run(manifest)
            service.execute(manifest["run_id"])
            projection = BenchmarkScorer(store, policy=policy, oracle_bundle=score_bundle, approved_policy_digests={_payload_digest(policy)}, approved_oracle_bundle_digests={_payload_digest(score_bundle)}).score(manifest["run_id"])
            self.assertEqual(projection["projection_status"], "SCORED_NOT_RANKED")
            self.assertTrue(all(item["case_pass_rate"]["value"] == 1.0 for item in projection["variants"]))
            self.assertTrue(all(item["metrics"]["end_to_end_latency"]["count"] == 1 for item in projection["variants"]))
            self.assertTrue(all("e2e_latency" not in item["metrics"] for item in projection["variants"]))

    def test_missing_case_fails_even_when_the_manifest_hashes_are_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "benchmarks"
            shutil.copytree(ROOT, copied)
            oracle_path = copied / "oracles/v2/outputs/financial_statements/oracles.json"
            oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
            oracle["oracles"].pop()
            _write(oracle_path, oracle)
            suite_manifest_path = copied / "oracles/v2/outputs/financial_statements/manifest.json"
            suite_manifest = json.loads(suite_manifest_path.read_text(encoding="utf-8"))
            for item in suite_manifest["candidate_files"]:
                if item["path"] == "oracles/v2/outputs/financial_statements/oracles.json":
                    item["sha256"] = _digest(oracle_path)
            _write(suite_manifest_path, suite_manifest)
            root_manifest_path = copied / "candidates/v0.2/manifest.json"
            root_manifest = json.loads(root_manifest_path.read_text(encoding="utf-8"))
            for item in root_manifest["suite_oracle_manifests"]:
                if item["path"] == "oracles/v2/outputs/financial_statements/manifest.json":
                    item["sha256"] = _digest(suite_manifest_path)
            _write(root_manifest_path, root_manifest)
            with self.assertRaisesRegex(CompileError, "requires exactly 100 oracles"):
                compile_v2(copied, Path(temp) / "out")

    def test_compiled_bytes_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            left, right = Path(temp) / "left", Path(temp) / "right"
            compile_v2(ROOT, left)
            compile_v2(ROOT, right)
            self.assertEqual((left / "oracle-bundle.v2.json").read_bytes(), (right / "oracle-bundle.v2.json").read_bytes())
            self.assertEqual((left / "run-manifest-template.v2.json").read_bytes(), (right / "run-manifest-template.v2.json").read_bytes())

    def test_v3_compiles_its_final_300_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            result = compile_v2(ROOT, Path(temp) / "out", candidate_revision="v0.3", oracle_revision="v3")
            bundle = json.loads(result["oracle_bundle"].read_text(encoding="utf-8"))
            manifest = json.loads(result["run_manifest"].read_text(encoding="utf-8"))
        self.assertEqual(len(bundle["oracles"]), 300)
        self.assertEqual(manifest["schema_version"], "runner-run-manifest-template/v2")
        self.assertEqual({oracle["source_case_id"][:3] for oracle in bundle["oracles"].values()}, {"FS3", "HIS", "RTQ"})

    def test_v3_tamper_duplicate_and_status_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "benchmarks"
            shutil.copytree(ROOT, copied)
            candidate = copied / "candidates/v0.3/financial_statements.cases.json"
            cases = json.loads(candidate.read_text(encoding="utf-8"))
            cases[1]["case_id"] = cases[0]["case_id"]
            _write(candidate, cases)
            manifest_path = copied / "candidates/v0.3/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            next(item for item in manifest["files"] if item["path"] == "candidates/v0.3/financial_statements.cases.json")["sha256"] = _digest(candidate)
            suite_manifest_path = copied / "oracles/v3/outputs/financial_statements/manifest.json"
            suite_manifest = json.loads(suite_manifest_path.read_text(encoding="utf-8"))
            next(item for item in suite_manifest["candidate_files"] if item["path"] == "candidates/v0.3/financial_statements.cases.json")["sha256"] = _digest(candidate)
            _write(suite_manifest_path, suite_manifest)
            next(item for item in manifest["suite_oracle_manifests"] if item["path"] == "oracles/v3/outputs/financial_statements/manifest.json")["sha256"] = _digest(suite_manifest_path)
            _write(manifest_path, manifest)
            with self.assertRaisesRegex(CompileError, "duplicate candidate"):
                compile_v2(copied, Path(temp) / "out", candidate_revision="v0.3", oracle_revision="v3")
            cases[1]["case_id"] = "FS3-002"
            cases[0]["expected_status"] = "no_data"
            _write(candidate, cases)
            next(item for item in manifest["files"] if item["path"] == "candidates/v0.3/financial_statements.cases.json")["sha256"] = _digest(candidate)
            next(item for item in suite_manifest["candidate_files"] if item["path"] == "candidates/v0.3/financial_statements.cases.json")["sha256"] = _digest(candidate)
            _write(suite_manifest_path, suite_manifest)
            next(item for item in manifest["suite_oracle_manifests"] if item["path"] == "oracles/v3/outputs/financial_statements/manifest.json")["sha256"] = _digest(suite_manifest_path)
            _write(manifest_path, manifest)
            with self.assertRaisesRegex(CompileError, "expected_status differs"):
                compile_v2(copied, Path(temp) / "out", candidate_revision="v0.3", oracle_revision="v3")


if __name__ == "__main__":
    unittest.main()

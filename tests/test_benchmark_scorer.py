import copy
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.benchmark_scorer import BenchmarkScoreError, BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION
from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunBackendError, RunService, RunStore, _digest, _score_projection_hash, _variant_contract_digest, _variant_identity


def policy(*, percentile="nearest_rank", ranked=True):
    contracts = {"success": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]}, "partial": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]}, "needs_clarification": {"required_non_null_paths": ["clarification"], "required_null_paths": ["data", "terminal_reason"]}, "unsupported": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]}, "no_data": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]}, "error": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]}}
    return {"schema_version": "score-policy/v1", "metric_names": ["semantic_accuracy", "data_accuracy", "token_usage", "e2e_latency"], "percentile_method": percentile, "assertion_operators": ["exact", "within_abs"], "operator_registry": ["exact", "within_abs"], "case_pass_gate": ["schema_valid", "status_correct", "semantic_pass", "data_pass", "execution_complete"], "completeness": {}, "response_schema_version": "get-response/v1", "response_status_contracts": contracts, "max_reference_window_seconds": 60, "error": "disabled", "timeout_latency_treatment": "cap_at_timeout", "usage_receipt_required_fields": ["receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"], "trusted_receipt_issuers": ["runner"], "eligibility": {"semantic_coverage_min": 1, "oracle_coverage_min": 1, "receipt_coverage_min": 1, "require_complete_execution": True} if ranked else None, "ranking": {"ordered_keys": ["case_pass_rate", "data_accuracy", "semantic_accuracy", "e2e_p95_ms", "average_total_tokens"], "directions": ["desc", "desc", "desc", "asc", "asc"], "tie_break": "variant_id"} if ranked else None}


def response(close=10, *, usage=True, status="success"):
    value = {"schema_version": "get-response/v1", "status": status, "resolved_request": {"symbol": "ABC"}, "data": {"close": close}, "as_of": "2026-09-03T00:00:00Z", "source": "frozen"}
    if usage: value["meta"] = {"usage": {"receipt_id": "receipt-1", "measurement_version": "usage-v1", "cache_status": "miss", "request_id": "request-" + "f" * 48, "issuer": "runner", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}
    return value


def bundle(*, tolerance=None, independence="independent_frozen"):
    data = {"path": "data.close", "operator": "exact" if tolerance is None else "within_abs", "expected": 10, "tolerance": tolerance, "weight": 2, "fatal": True}
    return {"schema_version": "oracle-bundle/v1", "oracles": {"oracle-one": {"oracle_id": "oracle-one", "case_id": "case-one", "independence": independence, "semantic_assertions": [{"path": "resolved_request.symbol", "operator": "exact", "expected": "ABC", "tolerance": None, "weight": 1, "fatal": True}], "data_assertions": [data], "state_assertions": [], "reference_evidence": None, "source_ref": "frozen", "version": "v1", "semantic_review_status": "approved", "data_review_status": "approved" if independence != "unavailable" else "unavailable", "state_review_status": "not_applicable"}}}


def variants():
    identity = {"agent_version": "v1", "get_variant_id": "public-get", "get_version": "v1", "model_identifier": "test-model", "model_version": "v1", "model_config_digest": "e" * 64}
    return [{"variant_id": "variant-b", "stable_display_order": 2, "agent_variant_id": "agent-b", **identity}, {"variant_id": "variant-a", "stable_display_order": 1, "agent_variant_id": "agent-a", **identity}]


def scoring_contract(scoring, oracle):
    return {"policy_digest": _digest(scoring), "oracle_bundle_digest": _digest(oracle), "scorer_version": SCORER_VERSION, "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(variants())}


def manifest(scoring, oracle):
    return {"run_id": "score-run", "mode": "diagnostic", "freeze_digest": "a" * 64, "policy": {"version": "v1"}, "timeout_ms": 100, "concurrency": 1, "scoring_contract": scoring_contract(scoring, oracle), "variants": variants(), "cases": [{"case_id": "case-one", "suite": "historical_price", "query": "safe", "score_case": {"expected_status": ["success"], "oracle_id": "oracle-one", "case_type": "normal"}}]}


class Client:
    def __init__(self, value, *, variant_id="variant-a"): self.value, self.variant_id = value, variant_id
    def run(self, _query, **kwargs):
        value = copy.deepcopy(self.value)
        if isinstance(value, dict) and isinstance(value.get("meta", {}).get("usage"), dict): value["meta"]["usage"]["request_id"] = kwargs["request_id"].replace("request-", "attempt-", 1)
        identity = _variant_identity(next(item for item in variants() if item["variant_id"] == self.variant_id))
        return PublicGetResult(value, ExecutionEvidence(**identity, agent_invocations=1, tool_executions=1, structured_outputs=1, tools_used=("get",)))


def clients(first, second):
    return {"variant-a": Client(first, variant_id="variant-a"), "variant-b": Client(second, variant_id="variant-b")}


class BenchmarkScorerTests(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory()
    def tearDown(self): self.tmp.cleanup()

    def execute_run(self, first=None, second=None, *, policy_value=None, oracle_value=None):
        p, o = policy_value or policy(), oracle_value or bundle()
        store = RunStore(self.tmp.name)
        service = RunService(store, clients(first or response(), second or response()))
        service.create_run(manifest(p, o)); service.execute("score-run")
        return store, p, o, service

    def scorer(self, store, p, o):
        return BenchmarkScorer(store, policy=p, oracle_bundle=o, approved_policy_digests={_digest(p)}, approved_oracle_bundle_digests={_digest(o)})

    def test_happy_metrics_ranking_snapshot_and_synthetic_event(self):
        store, p, o, service = self.execute_run(response(10), response(11))
        projection = self.scorer(store, p, o).score("score-run")
        self.assertEqual(projection["projection_status"], "SCORED")
        self.assertEqual(projection["receipt_basis"], "structurally_bound_attested_receipt")
        self.assertEqual({item["variant_id"] for item in projection["ranked_results"]}, {"variant-a", "variant-b"})
        a = next(v for v in projection["variants"] if v["variant_id"] == "variant-a")
        self.assertEqual(a["metrics"]["semantic_accuracy"], {"passed": 1, "denominator": 1, "value": 1.0})
        self.assertEqual(a["metrics"]["data_accuracy"]["value"], 1.0)
        self.assertEqual(a["metrics"]["token_usage"]["total_mean"], 5.0)
        self.assertEqual(service.get_snapshot("score-run")["status"], "completed")
        self.assertEqual(service.get_events("score-run")[-1]["event_type"], "scorer_projection")

    def test_weighted_data_tolerance_and_percentiles(self):
        p, o = policy(percentile="linear"), bundle(tolerance=1)
        store, p, o, _ = self.execute_run(response(11), response(12), policy_value=p, oracle_value=o)
        scored = self.scorer(store, p, o).score("score-run")
        self.assertEqual(next(v for v in scored["variants"] if v["variant_id"] == "variant-a")["metrics"]["data_accuracy"]["value"], 1.0)
        self.assertEqual(next(v for v in scored["variants"] if v["variant_id"] == "variant-b")["metrics"]["data_accuracy"]["value"], 0.0)

    def test_schema_status_transport_and_usage_missing_are_not_success(self):
        store, p, o, _ = self.execute_run({"status": "success"}, response(10, usage=False))
        scored = self.scorer(store, p, o).score("score-run")
        values = {v["variant_id"]: v for v in scored["variants"]}
        self.assertIn("RESPONSE_SCHEMA_INVALID", scored["public_failure_summaries"])
        self.assertIn("USAGE_UNAVAILABLE", values["variant-b"]["completeness_reasons"])
        self.assertEqual(values["variant-b"]["receipt_coverage"]["value"], 0.0)

    def test_unavailable_oracle_not_ranked(self):
        p, o = policy(), bundle(independence="unavailable")
        store, p, o, _ = self.execute_run(policy_value=p, oracle_value=o)
        scored = self.scorer(store, p, o).score("score-run")
        self.assertEqual(scored["projection_status"], "SCORED_NOT_RANKED")
        self.assertTrue(all(v["eligibility"] == "ineligible" for v in scored["variants"]))

    def test_digest_mismatch_and_no_contract_fail_without_score_files(self):
        store, p, o, _ = self.execute_run()
        with self.assertRaises(BenchmarkScoreError):
            BenchmarkScorer(store, policy=p, oracle_bundle=o, approved_policy_digests=set(), approved_oracle_bundle_digests={_digest(o)}).score("score-run")
        self.assertFalse((store.path_for("score-run") / "score-events.jsonl").exists())
        plain = manifest(p, o); plain.pop("scoring_contract")
        other = RunStore(self.tmp.name + "-plain")
        service = RunService(other, clients(response(), response())); service.create_run(plain); service.execute("score-run")
        with self.assertRaises(BenchmarkScoreError): self.scorer(other, p, o).score("score-run")
        self.assertIsNone(self.scorer(other, p, o).get_projection("score-run"))

    def test_tamper_resume_and_idempotency(self):
        store, p, o, _ = self.execute_run()
        scorer = self.scorer(store, p, o); first = scorer.score("score-run")
        self.assertEqual(first, scorer.score("score-run"))
        path = store.path_for("score-run") / "score-events.jsonl"
        lines = path.read_text().splitlines(); item = json.loads(lines[1]); item["record"]["case_id"] = "forged"; lines[1] = json.dumps(item, sort_keys=True, separators=(",", ":")); path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(BenchmarkScoreError): scorer.get_projection("score-run")

    def test_unsafe_oracle_and_manifest_score_case_are_rejected(self):
        bad = bundle(); bad["oracles"]["oracle-one"]["data_assertions"][0]["path"] = "__class__"
        store, p, _, _ = self.execute_run()
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, bad).score("score-run")
        value = manifest(p, bundle()); value["cases"][0]["score_case"] = {"expected_status": [], "oracle_id": "oracle-one", "case_type": "normal"}
        with self.assertRaises(Exception): RunStore(self.tmp.name + "-bad").create(value)

    def test_projection_hash_is_the_tail_event_hash(self):
        store, p, o, _ = self.execute_run()
        projection = self.scorer(store, p, o).score("score-run")
        tail = store.score_events("score-run")[-1]
        self.assertEqual((projection["score_tail_hash"], projection["projection_hash"]), (tail["score_event_hash"], tail["projection_hash"]))

    def test_missing_projection_artifact_rebuilds_from_terminal_score_event(self):
        store, p, o, _ = self.execute_run()
        scorer = self.scorer(store, p, o); first = scorer.score("score-run")
        (store.path_for("score-run") / "score-projection.json").unlink()
        self.assertEqual(first, scorer.score("score-run"))

    def test_score_journal_rejects_duplicate_record(self):
        store, p, o, _ = self.execute_run()
        scorer = self.scorer(store, p, o); scorer.score("score-run")
        event = next(item for item in store.score_events("score-run") if item["event_type"] == "score_record")
        with self.assertRaises(RunBackendError):
            store.append_score_event("score-run", {"event_type": "score_record", "bindings": event["bindings"], "record": event["record"]})

    def test_score_journal_rejects_out_of_order_record(self):
        store, p, o, _ = self.execute_run()
        bindings = {"execution_tail_hash": store.events("score-run")[-1]["event_hash"], **scoring_contract(p, o)}
        store.append_score_event("score-run", {"event_type": "score_started", "bindings": bindings})
        with self.assertRaises(RunBackendError):
            store.append_score_event("score-run", {"event_type": "score_record", "bindings": bindings, "record": {"variant_id": "variant-b", "case_id": "case-one", "trial": 1}})

    def test_score_record_has_trial_one(self):
        store, p, o, _ = self.execute_run()
        self.scorer(store, p, o).score("score-run")
        self.assertEqual({event["record"]["trial"] for event in store.score_events("score-run") if event["event_type"] == "score_record"}, {1})

    def test_tiny_decimal_weight_is_not_underflowed(self):
        o = bundle(); o["oracles"]["oracle-one"]["data_assertions"][0]["weight"] = "1e-1000"
        store, p, o, _ = self.execute_run(policy_value=policy(), oracle_value=o)
        metric = self.scorer(store, p, o).score("score-run")["variants"][0]["metrics"]["data_accuracy"]
        self.assertEqual(metric["value"], 1.0)
        self.assertNotEqual(metric["eligible_weight"], 0.0)

    def test_fatal_data_failure_keeps_other_weighted_credit(self):
        o = bundle(); assertions = o["oracles"]["oracle-one"]["data_assertions"]
        assertions[0].update({"expected": 9, "weight": 1, "fatal": True})
        assertions.append({"path": "data.close", "operator": "exact", "expected": 10, "tolerance": None, "weight": 999, "fatal": False})
        store, p, o, _ = self.execute_run(policy_value=policy(), oracle_value=o)
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertFalse(item["case_pass_rate"]["value"])
        self.assertEqual(item["metrics"]["data_accuracy"]["value"], .999)

    def test_state_only_case_can_pass_without_data_coverage(self):
        o = bundle(); oracle = o["oracles"]["oracle-one"]
        oracle["semantic_assertions"] = [{"path": "clarification", "operator": "exact", "expected": "need detail", "tolerance": None, "weight": 1, "fatal": True}]
        oracle["data_assertions"] = []; oracle["independence"] = "unavailable"; oracle["data_review_status"] = "not_applicable"; oracle["state_review_status"] = "approved"
        oracle["state_assertions"] = [{"path": "status", "operator": "exact", "expected": "needs_clarification", "tolerance": None, "weight": 1, "fatal": True}]
        p = policy(ranked=False); value = manifest(p, o); value["cases"][0]["score_case"].update({"case_type": "boundary", "expected_status": ["success"]})
        value["cases"][0]["score_case"]["expected_status"] = ["needs_clarification"]
        value["scoring_contract"] = scoring_contract(p, o)
        response_value = {"schema_version": "get-response/v1", "status": "needs_clarification", "clarification": "need detail", "meta": response()["meta"]}
        store = RunStore(self.tmp.name); service = RunService(store, clients(response_value, response_value)); service.create_run(value); service.execute("score-run")
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual(item["case_pass_rate"]["value"], 1.0)
        self.assertEqual(item["metrics"]["semantic_accuracy"]["value"], 1.0)
        self.assertEqual(item["oracle_coverage"]["denominator"], 0)

    def test_nonfatal_semantic_assertion_still_gates_case_pass(self):
        o = bundle(); o["oracles"]["oracle-one"]["semantic_assertions"][0].update({"expected": "ZZZ", "fatal": False})
        store, p, o, _ = self.execute_run(policy_value=policy(), oracle_value=o)
        record = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual(record["case_pass_rate"]["value"], 0.0)
        self.assertIn("SEMANTIC_ASSERTION_FAILED", record["completeness_reasons"] + self.scorer(store, p, o).get_projection("score-run")["public_failure_summaries"])

    def test_nonfatal_semantic_and_data_failures_gate_case_but_keep_weighted_credit(self):
        o = bundle(); oracle = o["oracles"]["oracle-one"]
        oracle["semantic_assertions"].append({"path": "resolved_request.symbol", "operator": "exact", "expected": "ZZZ", "tolerance": None, "weight": 1, "fatal": False})
        oracle["data_assertions"][0].update({"expected": 9, "weight": 1, "fatal": False})
        oracle["data_assertions"].append({"path": "data.close", "operator": "exact", "expected": 10, "tolerance": None, "weight": 999, "fatal": False})
        store, p, o, _ = self.execute_run(policy_value=policy(), oracle_value=o)
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual((item["case_pass_rate"]["value"], item["metrics"]["data_accuracy"]["value"]), (0.0, .999))

    def test_projection_with_wrong_execution_tail_is_rejected(self):
        store, p, o, _ = self.execute_run()
        scorer = self.scorer(store, p, o); projection = scorer.score("score-run")
        projection["bindings"]["execution_tail_hash"] = "0" * 64
        projection["projection_hash"] = _score_projection_hash(projection)
        store.write_score_projection("score-run", projection)
        with self.assertRaises(BenchmarkScoreError): scorer.get_projection("score-run")

    def test_untrusted_or_unbound_usage_receipt_is_unavailable(self):
        value = response(); value["meta"]["usage"]["issuer"] = "other"
        store, p, o, _ = self.execute_run(value, policy_value=policy(), oracle_value=bundle())
        record = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual(record["receipt_coverage"]["value"], 0.0)
        self.assertIn("USAGE_UNAVAILABLE", record["completeness_reasons"])

    def test_partial_and_error_can_never_be_expected_statuses(self):
        for status in ("partial", "error"):
            p, o, value = policy(), bundle(), manifest(policy(), bundle())
            value["cases"][0]["score_case"]["expected_status"] = [status]
            value["scoring_contract"] = scoring_contract(p, o)
            store = RunStore(self.tmp.name + "-" + status)
            service = RunService(store, clients(response(), response()))
            with self.assertRaises(RunBackendError): service.create_run(value)

    def test_partial_usage_receipt_is_unknown(self):
        partial = response(); partial["meta"]["usage"].pop("receipt_id")
        store, p, o, _ = self.execute_run(partial, policy_value=policy(), oracle_value=bundle())
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual(item["receipt_coverage"]["value"], 0.0)
        self.assertIn("USAGE_UNAVAILABLE", item["completeness_reasons"])

    def test_zero_usage_receipt_is_known_and_all_percentiles_are_present(self):
        zero = response(); zero["meta"]["usage"].update({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        store, p, o, _ = self.execute_run(zero, zero, policy_value=policy(), oracle_value=bundle())
        usage = self.scorer(store, p, o).score("score-run")["variants"][0]["metrics"]["token_usage"]
        self.assertEqual({key: usage[key] for key in usage if key.endswith(("_mean", "_p50", "_p95"))}, {"input_mean": 0.0, "input_p50": 0.0, "input_p95": 0.0, "output_mean": 0.0, "output_p50": 0.0, "output_p95": 0.0, "total_mean": 0.0, "total_p50": 0.0, "total_p95": 0.0})

    def test_policy_requires_response_schema_version(self):
        p = policy(); p.pop("response_schema_version")
        store, _, o, _ = self.execute_run(policy_value=p, oracle_value=bundle())
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_policy_requires_allowed_statuses(self):
        p = policy(); p["response_status_contracts"].pop("error")
        store, _, o, _ = self.execute_run(policy_value=p, oracle_value=bundle())
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_policy_requires_error_disabled(self):
        p = policy(); p["error"] = "enabled"
        store, _, o, _ = self.execute_run(policy_value=p, oracle_value=bundle())
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_policy_requires_explicit_timeout_treatment(self):
        p = policy(); p["timeout_latency_treatment"] = "guess"
        store, _, o, _ = self.execute_run(policy_value=p, oracle_value=bundle())
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_ranking_rejects_coverage_below_one(self):
        p = policy(); p["eligibility"]["receipt_coverage_min"] = .5
        store, _, o, _ = self.execute_run(policy_value=p, oracle_value=bundle())
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_ranking_order_is_not_configurable(self):
        p = policy(); p["ranking"]["directions"][0] = "asc"
        store, _, o, _ = self.execute_run(policy_value=p, oracle_value=bundle())
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_missing_realtime_reference_is_incomplete_and_not_data_eligible(self):
        p, o, value = policy(), bundle(), manifest(policy(), bundle())
        o["oracles"]["oracle-one"].update({"independence": "independent_dynamic", "reference_evidence": {"before_hash": "a" * 64, "after_hash": "a" * 64, "source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}})
        value["scoring_contract"] = scoring_contract(p, o)
        value["cases"][0].update({"suite": "realtime_quote", "reference_contract": {"source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}})
        store = RunStore(self.tmp.name); service = RunService(store, clients(response(), response()))
        service.create_run(value); service.execute("score-run")
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual((item["oracle_coverage"]["available"], item["oracle_coverage"]["denominator"]), (0, 1))
        self.assertIn("ORACLE_UNAVAILABLE", item["completeness_reasons"])

    def test_noncomparable_realtime_reference_is_not_data_eligible(self):
        p, o, value = policy(), bundle(), manifest(policy(), bundle())
        reference_hash = _digest({"as_of": "now", "source": "ref", "comparability": "not_comparable"})
        o["oracles"]["oracle-one"].update({"independence": "independent_dynamic", "reference_evidence": {"before_hash": reference_hash, "after_hash": reference_hash, "source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}})
        value["scoring_contract"] = scoring_contract(p, o)
        value["cases"][0].update({"suite": "realtime_quote", "reference_contract": {"source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}})
        class Hook:
            source_contract_hash = "b" * 64
            window_rule_version = "window-rule.v1"
            def __call__(self, _case, _phase): return {"source": "ref", "as_of": "now", "comparability": "not_comparable"}
        store = RunStore(self.tmp.name); service = RunService(store, clients(response(), response()), reference_hook=Hook())
        service.create_run(value); service.execute("score-run")
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual(item["metrics"]["data_accuracy"]["eligible_weight"], 0.0)
        self.assertIn("ORACLE_UNAVAILABLE", item["completeness_reasons"])

    def test_terminal_error_codes_remain_distinct(self):
        class FailingClient:
            def run(self, *_args, **_kwargs): raise RuntimeError("no detail")
        p, o = policy(), bundle(); store = RunStore(self.tmp.name)
        service = RunService(store, {"variant-a": FailingClient(), "variant-b": FailingClient()})
        service.create_run(manifest(p, o)); service.execute("score-run")
        failures = self.scorer(store, p, o).score("score-run")["public_failure_summaries"]
        self.assertIn("TRANSPORT_ERROR", failures)

    def test_normal_without_approved_data_oracle_is_unscored_and_fails(self):
        p, o = policy(), bundle(); o["oracles"]["oracle-one"].update({"data_assertions": [], "data_review_status": "unavailable", "independence": "unavailable"})
        store, p, o, _ = self.execute_run(policy_value=p, oracle_value=o)
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual((item["case_pass_rate"]["value"], item["oracle_coverage"]["value"]), (0.0, 0.0))

    def test_boundary_without_semantic_oracle_loses_semantic_coverage(self):
        p, o = policy(), bundle(); oracle = o["oracles"]["oracle-one"]
        oracle.update({"semantic_assertions": [], "semantic_review_status": "unavailable", "data_assertions": [], "data_review_status": "not_applicable", "independence": "unavailable", "state_review_status": "approved", "state_assertions": [{"path": "status", "operator": "exact", "expected": "needs_clarification", "tolerance": None, "weight": 1, "fatal": True}]})
        value = manifest(p, o); value["cases"][0]["score_case"].update({"case_type": "boundary", "expected_status": ["needs_clarification"]}); value["scoring_contract"] = scoring_contract(p, o)
        answer = {"schema_version": "get-response/v1", "status": "needs_clarification", "clarification": "need detail", "meta": response()["meta"]}
        store = RunStore(self.tmp.name); service = RunService(store, clients(answer, answer)); service.create_run(value); service.execute("score-run")
        item = self.scorer(store, p, o).score("score-run")["variants"][0]
        self.assertEqual((item["semantic_oracle_coverage"]["value"], item["case_pass_rate"]["value"]), (0.0, 0.0))
        record = next(event["record"] for event in store.score_events("score-run") if event["event_type"] == "score_record")
        self.assertFalse(record["semantic_pass"])
        self.assertIn("SEMANTIC_ORACLE_UNAVAILABLE", record["failure_codes"])

    def test_response_terminal_text_contract_requires_nonempty_strings_directly(self):
        for status, field in (("needs_clarification", "clarification"), ("unsupported", "terminal_reason"), ("no_data", "terminal_reason"), ("error", "terminal_reason")):
            for invalid in (None, "", {}, [], 1):
                with self.subTest(status=status, invalid=invalid):
                    self.assertFalse(BenchmarkScorer._response_valid({"schema_version": "get-response/v1", "status": status, field: invalid}, policy()))

    def test_response_source_is_a_nonempty_string_directly(self):
        value = response(usage=False)
        self.assertTrue(BenchmarkScorer._response_valid(value, policy()))
        for invalid in (None, "", ["frozen"], [], 1):
            with self.subTest(invalid=invalid):
                invalid_value = dict(value, source=invalid)
                self.assertFalse(BenchmarkScorer._response_valid(invalid_value, policy()))

    def test_boundary_data_assertions_are_rejected(self):
        p, o = policy(), bundle(); oracle = o["oracles"]["oracle-one"]
        oracle.update({"independence": "unavailable", "data_review_status": "not_applicable", "state_review_status": "approved", "state_assertions": [{"path": "status", "operator": "exact", "expected": "needs_clarification", "tolerance": None, "weight": 1, "fatal": True}], "semantic_assertions": [{"path": "clarification", "operator": "exact", "expected": "need detail", "tolerance": None, "weight": 1, "fatal": True}]})
        value = manifest(p, o); value["cases"][0]["score_case"].update({"case_type": "boundary", "expected_status": ["needs_clarification"]}); value["scoring_contract"] = scoring_contract(p, o)
        store = RunStore(self.tmp.name); service = RunService(store, clients(response(), response())); service.create_run(value); service.execute("score-run")
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_boundary_unsupported_terminal_reason_semantics_can_pass(self):
        p, o = policy(ranked=False), bundle(); oracle = o["oracles"]["oracle-one"]
        oracle.update({"semantic_assertions": [{"path": "terminal_reason", "operator": "exact", "expected": "unsupported request", "tolerance": None, "weight": 1, "fatal": True}], "data_assertions": [], "independence": "unavailable", "data_review_status": "not_applicable", "state_review_status": "approved", "state_assertions": [{"path": "status", "operator": "exact", "expected": "unsupported", "tolerance": None, "weight": 1, "fatal": True}]})
        value = manifest(p, o); value["cases"][0]["score_case"].update({"case_type": "boundary", "expected_status": ["unsupported"]}); value["scoring_contract"] = scoring_contract(p, o)
        answer = {"schema_version": "get-response/v1", "status": "unsupported", "terminal_reason": "unsupported request", "meta": response()["meta"]}
        store = RunStore(self.tmp.name); service = RunService(store, clients(answer, answer)); service.create_run(value); service.execute("score-run")
        self.assertEqual(self.scorer(store, p, o).score("score-run")["variants"][0]["case_pass_rate"]["value"], 1.0)

    def test_valid_partial_is_a_status_mismatch_not_schema_failure(self):
        partial = response(); partial["status"] = "partial"
        store, p, o, _ = self.execute_run(partial, policy_value=policy(), oracle_value=bundle())
        record = next(event["record"] for event in self.scorer(store, p, o).score("score-run") and store.score_events("score-run") if event["event_type"] == "score_record")
        self.assertEqual((record["schema_valid"], record["status_correct"]), (True, False))

    def test_scorer_digest_mismatch_blocks_scoring(self):
        p, o = policy(), bundle(); value = manifest(p, o); value["scoring_contract"]["scorer_digest"] = "0" * 64
        store = RunStore(self.tmp.name); service = RunService(store, clients(response(), response())); service.create_run(value); service.execute("score-run")
        with self.assertRaises(BenchmarkScoreError): self.scorer(store, p, o).score("score-run")

    def test_realtime_window_exceeding_policy_is_not_comparable(self):
        p, o, value = policy(), bundle(), manifest(policy(), bundle()); p["max_reference_window_seconds"] = 0
        ref_hash = _digest({"as_of": "now", "source": "ref", "comparability": "comparable"})
        o["oracles"]["oracle-one"].update({"independence": "independent_dynamic", "reference_evidence": {"before_hash": ref_hash, "after_hash": ref_hash, "source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}})
        value["scoring_contract"] = scoring_contract(p, o); value["cases"][0].update({"suite": "realtime_quote", "reference_contract": {"source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}})
        ticks = iter(range(1, 100)); hook = type("Hook", (), {"source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1", "__call__": lambda self, *_: {"source": "ref", "as_of": "now"}})()
        store = RunStore(self.tmp.name); service = RunService(store, clients(response(), response()), reference_hook=hook, wall_clock=lambda: next(ticks)); service.create_run(value); service.execute("score-run")
        self.assertIn("ORACLE_UNAVAILABLE", self.scorer(store, p, o).score("score-run")["variants"][0]["completeness_reasons"])

    def test_alternative_assertion_sets_require_one_complete_coherent_answer(self):
        def atom(path, expected): return {"path": path, "operator": "exact", "expected": expected, "tolerance": None, "weight": 1, "fatal": True}
        p, o = policy(), bundle(); o["schema_version"] = "oracle-bundle/v2"; oracle = o["oracles"]["oracle-one"]
        oracle.update({"semantic_assertions": [], "data_assertions": [], "alternative_assertion_sets": [
            {"semantic_assertions": [atom("resolved_request.symbol", "ABC"), atom("resolved_request.accepted_variant_id", "source-a")], "data_assertions": [atom("data.accepted_variant_id", "source-a"), atom("data.bars[0].open", 1), atom("data.bars[0].close", 10)], "state_assertions": []},
            {"semantic_assertions": [atom("resolved_request.symbol", "ABC"), atom("resolved_request.accepted_variant_id", "source-b")], "data_assertions": [atom("data.accepted_variant_id", "source-b"), atom("data.bars[0].open", 2), atom("data.bars[0].close", 20)], "state_assertions": []},
        ]})
        for index, (identity, data, case_pass, accuracy) in enumerate((("source-a", {"accepted_variant_id": "source-a", "bars": [{"open": 1, "close": 10}]}, 1.0, 1.0), ("source-b", {"accepted_variant_id": "source-b", "bars": [{"open": 2, "close": 20}]}, 1.0, 1.0), ("source-a", {"accepted_variant_id": "source-a", "bars": [{"open": 1, "close": 20}]}, 0.0, 2 / 3), ("source-a", {"accepted_variant_id": "source-a", "bars": [{"open": 9, "close": 99}]}, 0.0, 1 / 3))):
            answer = response(); answer["data"] = data
            answer["resolved_request"]["accepted_variant_id"] = identity
            store = RunStore(self.tmp.name + "-alternatives-%d" % index); service = RunService(store, clients(answer, answer)); service.create_run(manifest(p, o)); service.execute("score-run")
            item = self.scorer(store, p, o).score("score-run")["variants"][0]
            self.assertEqual((item["case_pass_rate"]["value"], item["metrics"]["data_accuracy"]["value"]), (case_pass, accuracy))

    def test_legacy_input_emits_canonical_latency_and_standard_data_paths_score(self):
        for index, (path, data, expected) in enumerate((("data.facts[0].value", {"facts": [{"value": 10}]}, 10), ("data.bars[0].close", {"bars": [{"close": 20}]}, 20), ("data.quote.last", {"quote": {"last": 30}}, 30))):
            p, o = policy(), bundle(); o["oracles"]["oracle-one"]["data_assertions"] = [{"path": path, "operator": "exact", "expected": expected, "tolerance": None, "weight": 1, "fatal": True}]
            answer = response(); answer["data"] = data
            store = RunStore(self.tmp.name + "-container-%d" % index); service = RunService(store, clients(answer, answer)); service.create_run(manifest(p, o)); service.execute("score-run")
            projection = self.scorer(store, p, o).score("score-run")
            self.assertEqual(projection["variants"][0]["metrics"]["data_accuracy"]["value"], 1.0)
            self.assertIn("end_to_end_latency", projection["variants"][0]["metrics"])
            self.assertNotIn("e2e_latency", projection["variants"][0]["metrics"])
            self.assertEqual({item["rank"] for item in projection["ranked_results"]}, {1, 2})

    def test_normal_success_allows_status_only_semantic_oracle(self):
        p, o = policy(), bundle(); o["oracles"]["oracle-one"]["semantic_assertions"] = [{"path": "status", "operator": "exact", "expected": "success", "tolerance": None, "weight": 1, "fatal": True}]
        store, _, _, _ = self.execute_run(policy_value=p, oracle_value=o)
        self.scorer(store, p, o).score("score-run")
        record = next(event["record"] for event in store.score_events("score-run") if event["event_type"] == "score_record")
        self.assertEqual((record["semantic_pass"], record["case_pass"]), (True, True))


if __name__ == "__main__": unittest.main()

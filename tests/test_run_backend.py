import json
import importlib
import os
import pathlib
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunBackendError, RunService, RunStore, _digest, _validate_event, _variant_contract_digest, _variant_identity
from qveris_benchmark.benchmark_scorer import SCORER_DIGEST, SCORER_VERSION


def reference_contract():
    return {"source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}


def variants():
    identity = {"agent_version": "v1", "get_variant_id": "public-get", "get_version": "v1", "model_identifier": "test-model", "model_version": "v1", "model_config_digest": "e" * 64}
    return [{"variant_id": "variant-b", "stable_display_order": 2, "agent_variant_id": "agent-b", **identity}, {"variant_id": "variant-a", "stable_display_order": 1, "agent_variant_id": "agent-a", **identity}]


def evidence(*, variant_id="variant-a", tools=("get",), agent_invocations=1, tool_executions=1, structured_outputs=1, **identity):
    identity = {**_variant_identity(next(item for item in variants() if item["variant_id"] == variant_id)), **identity}
    return ExecutionEvidence(**identity, agent_invocations=agent_invocations, tool_executions=tool_executions, structured_outputs=structured_outputs, tools_used=tools)


def public_response(status="success", *, reason="failed", clarification="which period?", usage=None):
    if status in {"success", "partial"}:
        value = {"schema_version": "get-response/v1", "status": status, "resolved_request": {"suite": "realtime_quote", "accepted_variant_id": "variant-1"}, "data": {"kind": "realtime_quote", "quote": {"instrument": {"symbol": "AAPL", "market": "US"}, "fields": {"close": {"value": "1", "unit": "USD_per_share", "as_of": "2026-09-04T00:00:00Z", "nil": False}}}}, "as_of": "2026-09-04T00:00:00Z", "source": "official", "clarification": None, "terminal_reason": None}
    elif status == "needs_clarification":
        value = {"schema_version": "get-response/v1", "status": status, "data": None, "clarification": clarification, "terminal_reason": None}
    else:
        value = {"schema_version": "get-response/v1", "status": status, "data": None, "clarification": None, "terminal_reason": reason}
    if usage is not None:
        value["meta"] = {"usage": usage}
    return value


def scoring_contract(policy_digest="c" * 64, oracle_digest="d" * 64):
    return {"policy_digest": policy_digest, "oracle_bundle_digest": oracle_digest, "scorer_version": SCORER_VERSION, "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(variants())}


class ReferenceHook:
    def __init__(self, callback, *, source_contract_hash="b" * 64, window_rule_version="window-rule.v1"):
        self.callback, self.calls = callback, []
        self._source_contract_hash = source_contract_hash
        self._window_rule_version = window_rule_version

    @property
    def source_contract_hash(self): return self._source_contract_hash

    @property
    def window_rule_version(self): return self._window_rule_version

    def __call__(self, case, phase):
        self.calls.append((case["case_id"], phase))
        return self.callback(case, phase)


class MissingIdentityHook:
    def __init__(self): self.calls = []

    def __call__(self, case, phase):
        self.calls.append((case["case_id"], phase))
        return {"source": "ref"}


def manifest(*, official=False, reference=False):
    if official:
        cases = [{"case_id": "%s-%03d" % (suite, i), "suite": suite, "query": "q", "score_case": {"expected_status": ["success"] if i < 80 else ["no_data"], "oracle_id": "oracle-%s-%03d" % (suite, i), "case_type": "normal" if i < 80 else "boundary"}} for suite in ("realtime_quote", "historical_price", "financial_statements") for i in range(100)]
        for case in cases:
            if case["suite"] == "realtime_quote":
                case["reference_contract"] = reference_contract()
    elif reference:
        cases = [{"case_id": "case-%s" % suite, "suite": suite, "query": "query-%s" % suite} for suite in ("realtime_quote", "historical_price", "financial_statements")]
        cases[0]["reference_contract"] = reference_contract()
    else:
        cases = [
            {"case_id": "case-historical_price", "suite": "historical_price", "query": "query-historical_price"},
            {"case_id": "case-financial_statements", "suite": "financial_statements", "query": "query-financial_statements"},
            {"case_id": "case-financial_statements-2", "suite": "financial_statements", "query": "query-financial_statements-2"},
        ]
    value = {"run_id": "run-1", "mode": "official" if official else "diagnostic", "freeze_digest": "a" * 64, "policy": {"version": "v1"}, "timeout_ms": 100, "concurrency": 1, "variants": variants(), "cases": cases}
    if official:
        value["schema_version"] = "runner-run-manifest/v1"
    return value


def v2_manifest(*, template=False):
    counts = {
        "financial_statements": {"success": 88, "needs_clarification": 5, "no_data": 7},
        "historical_price": {"success": 82, "needs_clarification": 2, "no_data": 6, "unsupported": 10},
        "realtime_quote": {"success": 90, "needs_clarification": 6, "no_data": 2, "unsupported": 2},
    }
    value = manifest(official=True)
    value["schema_version"] = "runner-run-manifest-template/v2" if template else "runner-run-manifest/v2"
    value["expected_status_counts"] = counts
    value["cases"] = []
    for suite, statuses in counts.items():
        index = 0
        for status, count in statuses.items():
            for _ in range(count):
                case = {"case_id": "%s-%03d" % (suite, index), "suite": suite, "query": "q", "score_case": {"expected_status": [status], "oracle_id": "oracle-%s-%03d" % (suite, index), "case_type": "normal" if status == "success" else "boundary"}}
                if suite == "realtime_quote":
                    case["reference_contract"] = reference_contract()
                value["cases"].append(case)
                index += 1
    if template:
        value["variants"] = []
    return value


def realtime_manifest():
    value = manifest(reference=True)
    value["cases"] = [value["cases"][0]]
    return value


class Client:
    def __init__(self, result=None, error=None, *, variant_id="variant-a"): self.calls, self.result, self.error, self.variant_id = [], result or public_response(), error, variant_id
    def run(self, query, *, request_id, idempotency_key):
        self.calls.append((query, request_id, idempotency_key))
        if self.error: raise self.error
        return PublicGetResult(self.result, evidence(variant_id=self.variant_id))


class ResultClient:
    def __init__(self, result): self.calls, self.result = [], result
    def run(self, query, *, request_id, idempotency_key):
        self.calls.append((query, request_id, idempotency_key))
        return self.result


class RunBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clients = {"variant-a": Client(variant_id="variant-a"), "variant-b": Client(variant_id="variant-b")}
        self.service = RunService(RunStore(self.directory.name), self.clients)

    def tearDown(self): self.directory.cleanup()

    def test_diagnostic_success_stable_order_and_unscored(self):
        self.service.create_run(manifest())
        result = self.service.execute("run-1")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["internal_status"], "execution_complete")
        self.assertEqual(result["projection_status"], "UNSCORED")
        self.assertEqual([v["variant_id"] for v in result["variants"]], ["variant-a", "variant-b"])
        self.assertEqual(result["execution"]["completed"], 6)
        self.assertEqual(result["scoring"]["data_accuracy"], "UNSCORED")
        self.assertIsNone(result["scoring"]["rank"])
        self.assertEqual(len(self.clients["variant-a"].calls), 3)
        terminal = next(event for event in self.service.get_events("run-1") if event["event_type"] == "terminal")
        self.assertEqual((terminal["transport_status"], terminal["transport_completed"], terminal["execution_outcome"]), ("completed", True, "success"))

    def test_diagnostic_accepts_one_variant_but_official_rejects_it(self):
        diagnostic = manifest()
        diagnostic["variants"] = [diagnostic["variants"][0]]
        RunService(RunStore(self.directory.name + "-single-diagnostic"), {"variant-b": self.clients["variant-b"]}).create_run(diagnostic)
        official = manifest(official=True)
        official["variants"] = [official["variants"][0]]
        with self.assertRaises(RunBackendError):
            RunService(RunStore(self.directory.name + "-single-official"), {"variant-b": self.clients["variant-b"]}).create_run(official)

    def test_canonical_public_statuses_need_no_legacy_interface(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("qveris_benchmark.get_interface")
        responses = {
            "success": public_response(), "partial": public_response("partial"),
            "needs_clarification": public_response("needs_clarification"),
            "unsupported": public_response("unsupported", reason="unsupported"),
            "no_data": public_response("no_data", reason="no data"), "error": public_response("error"),
        }
        expected = {"success": "success", "partial": "incomplete", "needs_clarification": "blocked", "unsupported": "blocked", "no_data": "blocked", "error": "failed"}
        for status, response in responses.items():
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                tool_executions, tools = (0, ()) if status in {"needs_clarification", "unsupported"} else (1, ("get",))
                service = RunService(RunStore(directory), {
                    variant_id: ResultClient(PublicGetResult(response, evidence(variant_id=variant_id, tool_executions=tool_executions, tools=tools)))
                    for variant_id in ("variant-a", "variant-b")
                })
                service.create_run(manifest())
                snapshot = service.execute("run-1")
                self.assertEqual(snapshot["execution"][expected[status]], 6)

    def test_pre_dispatch_statuses_require_zero_public_get_and_all_others_require_one(self):
        responses = {
            "needs_clarification": public_response("needs_clarification"),
            "unsupported": public_response("unsupported", reason="unsupported"),
            "success": public_response(), "partial": public_response("partial"),
            "no_data": public_response("no_data", reason="no data"), "error": public_response("error"),
        }
        for status, response in responses.items():
            with self.subTest(status=status):
                tool_executions, tools = (0, ()) if status in {"needs_clarification", "unsupported"} else (1, ("get",))
                _, _, projected = RunService._project_result(PublicGetResult(response, evidence(tool_executions=tool_executions, tools=tools)), variants()[1])
                self.assertEqual((projected["agent_invocations"], projected["tool_executions"], projected["structured_outputs"], projected["tools_used"]), (1, tool_executions, 1, list(tools)))
                with self.assertRaises(RunBackendError):
                    RunService._project_result(PublicGetResult(response, evidence(tool_executions=1 - tool_executions, tools=("get",) if not tools else ())), variants()[1])

    def test_semantic_pre_dispatch_error_requires_zero_public_get(self):
        response = public_response("error", reason="semantic_schema_invalid")
        _, _, projected = RunService._project_result(PublicGetResult(response, evidence(tool_executions=0, tools=())), variants()[1])
        self.assertEqual((projected["agent_invocations"], projected["tool_executions"], projected["structured_outputs"], projected["tools_used"]), (1, 0, 1, []))
        with self.assertRaises(RunBackendError):
            RunService._project_result(PublicGetResult(response, evidence()), variants()[1])

    def test_pre_dispatch_rejects_list_form_forbidden_tools(self):
        response = public_response("needs_clarification")
        for tools in (["Search"], ["qveris.inspect"]):
            with self.subTest(tools=tools), self.assertRaises(RunBackendError):
                RunService._project_result(PublicGetResult(response, evidence(tool_executions=0, tools=tools)), variants()[1])

    def test_terminal_replay_rejects_status_evidence_mismatch(self):
        self.service.create_run(manifest())
        response = {"status": "needs_clarification", "clarification": "which period?"}
        cell = "cell-" + _digest(["run-1", "variant-a", "case-historical_price", 1])[:48]
        attempt = "attempt-" + _digest(["run-1", "variant-a", "case-historical_price", 1, "get"])[:48]
        self.service.store.append("run-1", {"event_type": "dispatch_intent", "cell_id": cell, "attempt_id": attempt, "trial": 1, "input_hash": "1" * 64, "request_hash": "2" * 64, "variant_identity": _variant_identity(variants()[1])})
        with self.assertRaises(RunBackendError):
            self.service.store.append("run-1", {"event_type": "terminal", "cell_id": cell, "attempt_id": attempt, "elapsed_ms": 0, "transport_status": "completed", "public_response": response, "response_hash": _digest(response), "usage": "unknown", "usage_source": "unknown", "variant_identity": _variant_identity(variants()[1]), "execution_evidence": {**_variant_identity(variants()[1]), "agent_invocations": 1, "tool_executions": 1, "structured_outputs": 1, "tools_used": ["get"]}})

    def test_scoring_contract_and_score_case_are_validated_without_changing_old_runs(self):
        value = manifest()
        value["scoring_contract"] = scoring_contract()
        value["cases"][0]["score_case"] = {"expected_status": ["success"], "oracle_id": "oracle-one", "case_type": "normal"}
        self.service.create_run(value)
        self.assertEqual(self.service.get_snapshot("run-1")["projection_status"], "UNSCORED")
        invalid = manifest(); invalid["scoring_contract"] = {"policy_digest": "bad", "oracle_bundle_digest": "d" * 64}
        with self.assertRaises(RunBackendError): self.service.store.create(invalid)

    def test_manifest_rejects_duplicate_complete_variant_identity(self):
        value = manifest()
        for field, field_value in _variant_identity(value["variants"][0]).items():
            value["variants"][1][field] = field_value
        with self.assertRaises(RunBackendError): self.service.create_run(value)

    def test_usage_receipt_accepts_only_the_public_whitelist(self):
        response = public_response(usage={"receipt_id": "receipt-1", "measurement_version": "usage-v1", "cache_status": "miss", "request_id": "request-1", "issuer": "harness", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5})
        self.clients["variant-a"] = Client(response)
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        terminal = next(event for event in self.service.get_events("run-1") if event["event_type"] == "terminal" and event.get("usage") != "unknown")
        self.assertEqual(terminal["usage"], response["meta"]["usage"])
        response["meta"]["usage"]["provider"] = "no"
        self.assertEqual(RunService._project_response(response)[1], "unknown")

    def test_dispatch_trial_must_be_one(self):
        self.service.create_run(manifest())
        cell = "cell-" + _digest(["run-1", "variant-a", "case-historical_price", 1])[:48]
        attempt = "attempt-" + _digest(["run-1", "variant-a", "case-historical_price", 1, "get"])[:48]
        with self.assertRaises(RunBackendError):
            self.service.store.append("run-1", {"event_type": "dispatch_intent", "cell_id": cell, "attempt_id": attempt, "trial": 2, "input_hash": "1" * 64, "request_hash": "2" * 64})

    def _finished_scored_run(self):
        value = manifest()
        value["scoring_contract"] = scoring_contract()
        value["cases"][0]["score_case"] = {"expected_status": ["success"], "oracle_id": "oracle-one", "case_type": "normal"}
        self.service.create_run(value); self.service.execute("run-1")
        tail = self.service.store.events("run-1")[-1]["event_hash"]
        return {"execution_tail_hash": tail, **scoring_contract()}

    def test_score_journal_requires_manifest_contract_and_finished_execution_tail(self):
        bindings = self._finished_scored_run()
        bindings["policy_digest"] = "e" * 64
        with self.assertRaises(RunBackendError):
            self.service.store.append_score_event("run-1", {"event_type": "score_started", "bindings": bindings})

    def test_score_record_must_bind_to_the_expected_cell(self):
        bindings = self._finished_scored_run()
        self.service.store.append_score_event("run-1", {"event_type": "score_started", "bindings": bindings})
        with self.assertRaises(RunBackendError):
            self.service.store.append_score_event("run-1", {"event_type": "score_record", "bindings": bindings, "record": {"variant_id": "variant-a", "case_id": "case-historical_price", "cell_id": "cell-forged", "trial": 1}})

    def test_forged_self_consistent_score_projection_with_wrong_contract_is_rejected(self):
        bindings = self._finished_scored_run()
        bindings["policy_digest"] = "e" * 64
        manifest_hash = self.service.store.events("run-1")[0]["manifest_hash"]
        records = []
        previous = None
        for sequence, event_type, record in ((1, "score_started", None), (2, "score_record", {"variant_id": "variant-a", "case_id": "case-historical_price", "cell_id": "cell-" + _digest(["run-1", "variant-a", "case-historical_price", 1])[:48], "trial": 1}), (3, "score_record", {"variant_id": "variant-b", "case_id": "case-historical_price", "cell_id": "cell-" + _digest(["run-1", "variant-b", "case-historical_price", 1])[:48], "trial": 1})):
            item = {"event_type": event_type, "bindings": bindings, "sequence": sequence, "manifest_hash": manifest_hash, "previous_score_hash": previous}
            if record is not None: item["record"] = record
            item["score_event_hash"] = _digest(item); records.append(item); previous = item["score_event_hash"]
        projection = {"schema_version": "qveris-benchmark-score-projection/v1", "run_id": "run-1", "manifest_hash": manifest_hash, "bindings": bindings, "projection_status": "SCORED_NOT_RANKED", "variants": [], "ranked_results": [], "ineligible_results": [], "public_failure_summaries": []}
        projection["projection_hash"] = _digest(projection)
        final = {"event_type": "scorer_projection", "bindings": bindings, "projection_hash": projection["projection_hash"], "sequence": 4, "manifest_hash": manifest_hash, "previous_score_hash": previous}
        final["score_event_hash"] = _digest(final); records.append(final)
        projection["score_tail_hash"] = final["score_event_hash"]
        run_dir = pathlib.Path(self.directory.name) / "run-1"
        (run_dir / "score-events.jsonl").write_bytes(b"".join(json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n" for item in records))
        (run_dir / "score-projection.json").write_bytes(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode())
        with self.assertRaises(RunBackendError): self.service.get_snapshot("run-1")


    def test_official_requires_three_hundred_cases(self):
        self.service.create_run(manifest(official=True))
        bad = manifest(official=True); bad["cases"].pop()
        with self.assertRaises(RunBackendError): self.service.create_run(bad)

    def test_official_requires_canonical_suites_and_eighty_twenty_case_mix(self):
        bad_name = manifest(official=True)
        bad_name["cases"][-1]["suite"] = "financial_statement"
        with self.assertRaises(RunBackendError): RunService(RunStore(self.directory.name + "-bad-name"), self.clients).create_run(bad_name)
        bad_mix = manifest(official=True)
        bad_mix["cases"][0]["score_case"] = {"expected_status": ["no_data"], "oracle_id": "oracle-realtime_quote-000", "case_type": "boundary"}
        with self.assertRaises(RunBackendError): RunService(RunStore(self.directory.name + "-bad-mix"), self.clients).create_run(bad_mix)

    def test_official_v2_uses_declared_status_counts_instead_of_legacy_eighty_twenty(self):
        value = v2_manifest()
        value["freeze"] = {"candidate_manifest_hash": "f" * 64}
        value["oracle_bundle"] = {"digest": "d" * 64, "version": "v2"}
        self.service.create_run(value)
        snapshot = self.service.get_snapshot("run-1")
        self.assertEqual(snapshot["execution"]["total"], 600)

    def test_official_v2_declared_status_counts_must_match_cases(self):
        value = v2_manifest()
        value["expected_status_counts"]["financial_statements"]["success"] = 87
        value["expected_status_counts"]["financial_statements"]["no_data"] = 8
        with self.assertRaises(RunBackendError): self.service.create_run(value)
        value = v2_manifest()
        value.pop("expected_status_counts")
        with self.assertRaises(RunBackendError): self.service.create_run(value)

    def test_official_v2_accepts_candidate_style_suite_composition(self):
        value = v2_manifest()
        counts = value.pop("expected_status_counts")
        value["suite_composition"] = [
            {"suite": suite, "cases": 100, "expected_status_counts": statuses}
            for suite, statuses in counts.items()
        ]
        self.service.create_run(value)

    def test_v2_template_allows_empty_variants_but_cannot_execute(self):
        self.service.create_run(v2_manifest(template=True))
        self.assertEqual(self.service.get_snapshot("run-1")["execution"]["total"], 0)
        with self.assertRaises(RunBackendError): self.service.execute("run-1")

    def test_v2_realtime_without_complete_reference_contract_blocks_without_get(self):
        value = manifest()
        value["schema_version"] = "runner-run-manifest/v2"
        value["cases"] = [{"case_id": "case-realtime_quote", "suite": "realtime_quote", "query": "query", "score_case": {"expected_status": ["success"], "oracle_id": "oracle-one", "case_type": "normal"}}]
        self.service.create_run(value)
        result = self.service.execute("run-1")
        self.assertEqual([len(client.calls) for client in self.clients.values()], [0, 0])
        self.assertEqual(result["execution"]["blocked"], 2)
        self.assertEqual({event.get("error_class") for event in self.service.get_events("run-1") if event["event_type"] == "terminal"}, {"reference_contract_unavailable"})

    def test_v2_source_case_id_is_read_only_and_other_case_extensions_are_rejected(self):
        value = manifest()
        value["schema_version"] = "runner-run-manifest/v2"
        value["cases"][0]["source_case_id"] = "hist-港股-01"
        self.service.create_run(value)
        value = manifest()
        value["schema_version"] = "runner-run-manifest/v2"
        value["cases"][0]["source_case_id"] = "hist-港股-01"
        value["cases"][0]["adapter_hint"] = "must-not-reach-client"
        with self.assertRaises(RunBackendError): RunService(RunStore(self.directory.name + "-bad-v2-case"), self.clients).create_run(value)

    def test_runtime_evidence_rejects_bare_mapping_counts_tools_and_identity_mismatch(self):
        public = {"schema_version": "get-response/v1", "status": "success", "resolved_request": {}, "data": {}, "as_of": "t", "source": "s"}
        invalid = {
            "bare": public,
            "zero_count": PublicGetResult(public, evidence(agent_invocations=0)),
            "two_count": PublicGetResult(public, evidence(tool_executions=2)),
            "search": PublicGetResult(public, evidence(tools=("Search",))),
            "inspect_alias": PublicGetResult(public, evidence(tools=("qveris.inspect",))),
            "identity": PublicGetResult(public, evidence(model_version="wrong")),
        }
        for name, result in invalid.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                client = ResultClient(result)
                service = RunService(RunStore(root), {"variant-a": client, "variant-b": Client(variant_id="variant-b")})
                service.create_run(manifest()); snapshot = service.execute("run-1")
                self.assertEqual(len(client.calls), 3)
                self.assertEqual(snapshot["execution"]["failed"], 3)
                terminals = [event for event in service.get_events("run-1") if event["event_type"] == "terminal" and event["cell_id"] in {"cell-" + _digest(["run-1", "variant-a", case["case_id"], 1])[:48] for case in manifest()["cases"]}]
                self.assertEqual({event.get("error_class") for event in terminals}, {"runtime_evidence_invalid"})
                self.assertTrue(all("execution_evidence" not in event for event in terminals))

    def test_journal_rejects_dispatch_identity_that_does_not_match_manifest(self):
        self.service.create_run(manifest())
        cell = "cell-" + _digest(["run-1", "variant-a", "case-historical_price", 1])[:48]
        attempt = "attempt-" + _digest(["run-1", "variant-a", "case-historical_price", 1, "get"])[:48]
        wrong = dict(_variant_identity(variants()[1])); wrong["model_version"] = "wrong"
        with self.assertRaises(RunBackendError):
            self.service.store.append("run-1", {"event_type": "dispatch_intent", "cell_id": cell, "attempt_id": attempt, "trial": 1, "input_hash": "1" * 64, "request_hash": "2" * 64, "variant_identity": wrong})

    def test_realtime_contract_is_required_and_rejected_before_any_client_call(self):
        for contract in (None, {}, [], {"source_contract_hash": "b" * 64}, {"source_contract_hash": "B" * 64, "window_rule_version": "v1"}, {"source_contract_hash": "b" * 64, "window_rule_version": "bad version"}, {"source_contract_hash": "b" * 64, "window_rule_version": "v1", "api_key": "no"}):
            with self.subTest(contract=contract):
                value = manifest()
                value["cases"][0] = {"case_id": "case-realtime_quote", "suite": "realtime_quote", "query": "query-realtime_quote"}
                if contract is not None:
                    value["cases"][0]["reference_contract"] = contract
                with self.assertRaises(RunBackendError):
                    self.service.create_run(value)
                self.assertEqual(sum(len(client.calls) for client in self.clients.values()), 0)

    def test_manifest_precedes_call_and_is_private(self):
        class CheckingClient(Client):
            def run(client, *args, **kwargs):
                self.assertTrue((pathlib.Path(self.directory.name) / "run-1" / "manifest.json").exists())
                return super(CheckingClient, client).run(*args, **kwargs)
        self.clients["variant-a"] = CheckingClient(); self.service = RunService(self.service.store, self.clients)
        self.service.create_run(manifest()); self.service.execute("run-1")
        path = pathlib.Path(self.directory.name) / "run-1" / "manifest.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_no_retry_exception_and_timeout_are_facts(self):
        self.clients["variant-a"] = Client(error=TimeoutError())
        self.clients["variant-b"] = Client(error=RuntimeError("secret"), variant_id="variant-b")
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        events = self.service.get_events("run-1")
        terminals = [e for e in events if e["event_type"] == "terminal"]
        self.assertEqual(len(self.clients["variant-a"].calls), 3)
        self.assertIn("timeout", {e["transport_status"] for e in terminals})
        self.assertNotIn("secret", json.dumps(events))

    def test_realtime_reference_before_after_and_failures(self):
        phases = []
        def hook(case, phase): phases.append(phase); return {"as_of": "t", "source": "ref"}
        service = RunService(self.service.store, self.clients, reference_hook=ReferenceHook(hook))
        service.create_run(manifest(reference=True)); service.execute("run-1")
        self.assertEqual(phases.count("before"), 2); self.assertEqual(phases.count("after"), 2)
        self.assertNotIn("query-realtime_quote", json.dumps(service.get_events("run-1")))

        self.directory.cleanup(); self.directory = tempfile.TemporaryDirectory(); clients = {"variant-a": Client(variant_id="variant-a"), "variant-b": Client(variant_id="variant-b")}
        service = RunService(RunStore(self.directory.name), clients, reference_hook=ReferenceHook(lambda *_: (_ for _ in ()).throw(RuntimeError())))
        service.create_run(manifest(reference=True)); result = service.execute("run-1")
        self.assertEqual(len(clients["variant-a"].calls), 2)
        self.assertEqual(result["status"], "incomplete")

    def test_reference_contract_match_allows_reference_and_dispatch(self):
        hook = ReferenceHook(lambda *_: {"source": "ref"})
        service = RunService(self.service.store, self.clients, reference_hook=hook)
        service.create_run(realtime_manifest()); result = service.execute("run-1")
        self.assertEqual(len(hook.calls), 4)
        self.assertEqual([len(client.calls) for client in self.clients.values()], [1, 1])
        self.assertEqual(result["execution"]["success"], 2)

    def test_reference_contract_hash_mismatch_blocks_reference_and_get(self):
        hook = ReferenceHook(lambda *_: self.fail("reference hook must not run"), source_contract_hash="c" * 64)
        service = RunService(self.service.store, self.clients, reference_hook=hook)
        service.create_run(realtime_manifest()); result = service.execute("run-1")
        self.assertEqual(hook.calls, [])
        self.assertEqual([len(client.calls) for client in self.clients.values()], [0, 0])
        self.assertEqual(result["execution"]["incomplete"], 2)
        self.assertEqual({event.get("error_class") for event in service.get_events("run-1") if event["event_type"] == "terminal"}, {"reference_contract_mismatch"})

    def test_reference_contract_version_mismatch_blocks_reference_and_get(self):
        hook = ReferenceHook(lambda *_: self.fail("reference hook must not run"), window_rule_version="window-rule.v2")
        service = RunService(self.service.store, self.clients, reference_hook=hook)
        service.create_run(realtime_manifest()); result = service.execute("run-1")
        self.assertEqual(hook.calls, [])
        self.assertEqual([len(client.calls) for client in self.clients.values()], [0, 0])
        self.assertEqual(result["execution"]["incomplete"], 2)
        self.assertEqual({event.get("error_class") for event in service.get_events("run-1") if event["event_type"] == "terminal"}, {"reference_contract_mismatch"})

    def test_reference_hook_missing_identity_blocks_reference_and_get(self):
        hook = MissingIdentityHook()
        service = RunService(self.service.store, self.clients, reference_hook=hook)
        service.create_run(realtime_manifest()); result = service.execute("run-1")
        self.assertEqual(hook.calls, [])
        self.assertEqual([len(client.calls) for client in self.clients.values()], [0, 0])
        self.assertEqual(result["execution"]["incomplete"], 2)
        self.assertEqual({event.get("error_class") for event in service.get_events("run-1") if event["event_type"] == "terminal"}, {"reference_contract_mismatch"})

    def test_reference_contract_is_rechecked_before_get_dispatch(self):
        hook = ReferenceHook(lambda *_: {"source": "ref"})
        def mutate_contract(case, phase):
            if phase == "before": hook._source_contract_hash = "c" * 64
            return {"source": "ref"}
        hook.callback = mutate_contract
        service = RunService(self.service.store, self.clients, reference_hook=hook)
        service.create_run(realtime_manifest()); result = service.execute("run-1")
        self.assertEqual([len(client.calls) for client in self.clients.values()], [0, 0])
        self.assertEqual(result["execution"]["incomplete"], 2)
        self.assertEqual({event.get("error_class") for event in service.get_events("run-1") if event["event_type"] == "terminal"}, {"reference_contract_mismatch"})

    def test_reference_hook_rejects_multiple_case_contracts_at_create(self):
        value = realtime_manifest()
        value["cases"].append({"case_id": "case-realtime_quote-2", "suite": "realtime_quote", "query": "query-2", "reference_contract": {"source_contract_hash": "c" * 64, "window_rule_version": "window-rule.v1"}})
        service = RunService(self.service.store, self.clients, reference_hook=ReferenceHook(lambda *_: {"source": "ref"}))
        with self.assertRaises(RunBackendError): service.create_run(value)
        self.assertEqual([len(client.calls) for client in self.clients.values()], [0, 0])

    def test_realtime_reference_contract_without_hook_has_zero_get_calls(self):
        self.service.create_run(manifest(reference=True)); result = self.service.execute("run-1")
        self.assertEqual(len(self.clients["variant-a"].calls), 2)
        terminals = [event for event in self.service.get_events("run-1") if event["event_type"] == "terminal"]
        self.assertEqual(sum(event["transport_status"] == "reference_unavailable" for event in terminals), 2)
        self.assertEqual(result["status"], "incomplete")

    def test_after_failure_incomplete_but_get_terminal_remains(self):
        def hook(case, phase):
            if phase == "after": raise RuntimeError()
            return {"source": "ref"}
        service = RunService(self.service.store, self.clients, reference_hook=ReferenceHook(hook))
        service.create_run(manifest(reference=True)); result = service.execute("run-1")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len([e for e in service.get_events("run-1") if e["event_type"] == "terminal"]), 6)

    def test_crash_intent_recovery_append_and_rebuild(self):
        self.service.create_run(manifest())
        run = "run-1"
        cell = "cell-" + _digest([run, "variant-a", "case-historical_price", 1])[:48]
        attempt = "attempt-" + _digest([run, "variant-a", "case-historical_price", 1, "get"])[:48]
        self.service.store.append(run, {"event_type": "dispatch_intent", "cell_id": cell, "attempt_id": attempt, "trial": 1, "input_hash": "3" * 64, "request_hash": "4" * 64, "variant_identity": _variant_identity(variants()[1])})
        result = self.service.execute(run)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(self.clients["variant-a"].calls), 2)
        events_path = pathlib.Path(self.directory.name) / run / "events.jsonl"
        self.assertTrue(events_path.read_bytes().endswith(b"\n"))
        self.assertEqual(stat.S_IMODE(events_path.stat().st_mode), 0o600)
        rebuilt = self.service.get_snapshot(run)
        self.assertEqual(rebuilt["manifest_hash"], result["manifest_hash"])
        self.assertEqual(stat.S_IMODE((pathlib.Path(self.directory.name) / run / "snapshot.json").stat().st_mode), 0o600)

    def test_sensitive_manifest_is_rejected(self):
        value = manifest(); value["policy"]["api_key"] = "nope"
        with self.assertRaises(RunBackendError): self.service.create_run(value)
        value = manifest(); value["policy"]["latency_sla"] = "unknown"
        with self.assertRaises(RunBackendError): self.service.create_run(value)

    def test_public_response_drops_camel_case_secret(self):
        self.clients["variant-a"] = Client({"status": "success", "data": {"clientSecret": "nope"}})
        self.service = RunService(self.service.store, self.clients)
        self.service.create_run(manifest()); self.service.execute("run-1")
        records = [event for event in self.service.get_events("run-1") if event["event_type"] == "terminal"]
        self.assertNotIn("nope", json.dumps(records))
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in records})

    def test_public_response_rejects_raw_response_camel_case(self):
        self.clients["variant-a"] = Client({"status": "success", "data": {"rawResponse": "nope"}})
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in self.service.get_events("run-1") if event["event_type"] == "terminal"})

    def test_public_response_rejects_raw_response_kebab_case(self):
        self.clients["variant-a"] = Client({"status": "success", "data": {"raw-response": "nope"}})
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in self.service.get_events("run-1") if event["event_type"] == "terminal"})

    def test_public_response_rejects_raw_response_snake_case(self):
        self.clients["variant-a"] = Client({"status": "success", "data": {"raw_response": "nope"}})
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in self.service.get_events("run-1") if event["event_type"] == "terminal"})

    def test_public_response_rejects_provider_payload_camel_case(self):
        self.clients["variant-a"] = Client({"status": "success", "data": {"providerPayload": "nope"}})
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in self.service.get_events("run-1") if event["event_type"] == "terminal"})

    def test_public_response_rejects_provider_payload_kebab_case(self):
        self.clients["variant-a"] = Client({"status": "success", "data": {"provider-payload": "nope"}})
        self.service = RunService(self.service.store, self.clients); self.service.create_run(manifest()); self.service.execute("run-1")
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in self.service.get_events("run-1") if event["event_type"] == "terminal"})

    def test_store_rejects_forged_cell_event(self):
        self.service.create_run(manifest())
        with self.assertRaises(RunBackendError):
            self.service.store.append("run-1", {"event_type": "dispatch_intent", "cell_id": "cell-forged", "attempt_id": "attempt-forged", "input_hash": "1" * 64, "request_hash": "2" * 64})
        cell = "cell-" + _digest(["run-1", "variant-a", "case-historical_price", 1])[:48]
        attempt = "attempt-" + _digest(["run-1", "variant-a", "case-historical_price", 1, "get"])[:48]
        with self.assertRaises(RunBackendError):
            self.service.store.append("run-1", {"event_type": "terminal", "cell_id": cell, "attempt_id": attempt, "elapsed_ms": 0, "transport_status": "reference_unavailable", "error_class": "reference_before_unavailable", "usage": "unknown", "response_hash": None})

    def test_manifest_and_event_symlinks_fail_closed_without_touching_target(self):
        self.service.create_run(manifest())
        run_dir = pathlib.Path(self.directory.name) / "run-1"
        target = pathlib.Path(self.directory.name) / "outside.json"
        target.write_bytes(b"outside")
        for name, call in (("manifest.json", lambda: self.service.get_snapshot("run-1")), ("events.jsonl", lambda: self.service.get_events("run-1"))):
            path = run_dir / name
            original = path.read_bytes()
            path.unlink()
            os.symlink(target, path)
            with self.assertRaises(RunBackendError): call()
            self.assertEqual(target.read_bytes(), b"outside")
            path.unlink()
            path.write_bytes(original)

    def test_manifest_tamper_after_run_binding_makes_execute_fail_closed(self):
        self.service.create_run(manifest())
        path = pathlib.Path(self.directory.name) / "run-1" / "manifest.json"
        value = json.loads(path.read_bytes())
        value["timeout_ms"] = 101
        path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        with self.assertRaises(RunBackendError): self.service.execute("run-1")
        self.assertEqual(sum(len(client.calls) for client in self.clients.values()), 0)
        with self.assertRaises(RunBackendError): self.service.get_snapshot("run-1")

    def test_outer_timeout_is_terminal_and_has_one_call_per_cell(self):
        class SlowClient(Client):
            def run(client, *args, **kwargs):
                client.calls.append(args)
                time.sleep(.05)
        self.clients["variant-a"] = SlowClient()
        self.service = RunService(self.service.store, self.clients)
        value = manifest(); value["timeout_ms"] = 5
        self.service.create_run(value)
        started = time.monotonic(); self.service.execute("run-1")
        self.assertLess(time.monotonic() - started, .2)
        terminals = [event for event in self.service.get_events("run-1") if event["event_type"] == "terminal"]
        self.assertEqual(len(self.clients["variant-a"].calls), 3)
        self.assertEqual(sum(event["transport_status"] == "timeout" for event in terminals), 3)

    def test_threaded_execution_fails_closed_before_dispatch(self):
        self.service.create_run(manifest())
        errors = []
        thread = threading.Thread(target=lambda: errors.append(self._execute_error()))
        thread.start(); thread.join()
        self.assertIsInstance(errors[0], RunBackendError)
        self.assertEqual(sum(len(client.calls) for client in self.clients.values()), 0)

    def _execute_error(self):
        try:
            self.service.execute("run-1")
        except RunBackendError as error:
            return error
        return None

    def test_invalid_object_response_is_not_coerced_to_success(self):
        self.clients["variant-a"] = Client(result="success")
        self.service = RunService(self.service.store, self.clients)
        self.service.create_run(manifest()); self.service.execute("run-1")
        terminals = [event for event in self.service.get_events("run-1") if event["event_type"] == "terminal"]
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in terminals})

    def test_unknown_get_status_is_invalid_public_response(self):
        self.clients["variant-a"] = Client(result={"status": "invented", "data": {"close": 1}})
        self.service = RunService(self.service.store, self.clients)
        self.service.create_run(manifest()); self.service.execute("run-1")
        terminals = [event for event in self.service.get_events("run-1") if event["event_type"] == "terminal"]
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in terminals})

    def test_hash_chain_rejects_tampered_terminal_and_reference(self):
        service = RunService(self.service.store, self.clients, reference_hook=ReferenceHook(lambda *_: {"source": "ref"}))
        service.create_run(manifest(reference=True)); service.execute("run-1")
        path = pathlib.Path(self.directory.name) / "run-1" / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        terminal = next(event for event in events if event["event_type"] == "terminal" and event.get("public_response"))
        terminal["public_response"]["status"] = "failed"
        path.write_text("\n".join(json.dumps(event, sort_keys=True, separators=(",", ":")) for event in events) + "\n")
        with self.assertRaises(RunBackendError): service.get_events("run-1")

        self.directory.cleanup(); self.directory = tempfile.TemporaryDirectory()
        service = RunService(RunStore(self.directory.name), {"variant-a": Client(variant_id="variant-a"), "variant-b": Client(variant_id="variant-b")}, reference_hook=ReferenceHook(lambda *_: {"source": "ref"}))
        service.create_run(manifest(reference=True)); service.execute("run-1")
        path = pathlib.Path(self.directory.name) / "run-1" / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        reference = next(event for event in events if event["event_type"] == "reference_before")
        reference["reference"]["source"] = "changed"
        path.write_text("\n".join(json.dumps(event, sort_keys=True, separators=(",", ":")) for event in events) + "\n")
        with self.assertRaises(RunBackendError): service.get_events("run-1")

    def test_get_result_failure_and_raw_provider_data_are_not_execution_success(self):
        self.clients["variant-a"] = Client({"status": "failed", "data": {"close": 1}})
        self.clients["variant-b"] = Client({"status": "success", "data": {"provider_payload": {"secret": "no"}}}, variant_id="variant-b")
        self.service = RunService(self.service.store, self.clients)
        self.service.create_run(manifest()); result = self.service.execute("run-1")
        terminals = [event for event in self.service.get_events("run-1") if event["event_type"] == "terminal"]
        self.assertIn("invalid_public_response", {event.get("public_response", {}).get("status") for event in terminals})
        self.assertGreater(result["execution"]["failed"], 0)

    def test_recovery_from_reference_before_and_intent_marks_after_unavailable(self):
        hook = ReferenceHook(lambda *_: {"source": "ref"})
        service = RunService(self.service.store, self.clients, reference_hook=hook)
        service.create_run(manifest(reference=True))
        cell = "cell-" + _digest(["run-1", "variant-a", "case-realtime_quote", 1])[:48]
        attempt = "attempt-" + _digest(["run-1", "variant-a", "case-realtime_quote", 1, "get"])[:48]
        service.store.append("run-1", {"event_type": "reference_before", "cell_id": cell, "attempt_id": attempt, "reference": {"source": "ref", "comparability": "comparable"}})
        service.store.append("run-1", {"event_type": "dispatch_intent", "cell_id": cell, "attempt_id": attempt, "trial": 1, "input_hash": "1" * 64, "request_hash": "2" * 64, "variant_identity": _variant_identity(variants()[1])})
        result = service.execute("run-1")
        events = service.get_events("run-1")
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any(event["event_type"] == "reference_after_unavailable" and event["cell_id"] == cell for event in events))

    def test_recovery_from_reference_before_without_hook_makes_zero_get_calls(self):
        seed = RunService(self.service.store, self.clients, reference_hook=ReferenceHook(lambda *_: {"source": "ref"}))
        seed.create_run(manifest(reference=True))
        cell = "cell-" + _digest(["run-1", "variant-a", "case-realtime_quote", 1])[:48]
        attempt = "attempt-" + _digest(["run-1", "variant-a", "case-realtime_quote", 1, "get"])[:48]
        seed._append("run-1", {"event_type": "reference_before", "cell_id": cell, "attempt_id": attempt, "reference": {"source": "ref"}})
        service = RunService(self.service.store, self.clients)
        result = service.execute("run-1")
        events = service.get_events("run-1")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(self.clients["variant-a"].calls), 2)
        self.assertTrue(any(event["event_type"] == "reference_after_unavailable" and event["cell_id"] == cell for event in events))

    def test_concurrency_other_than_one_is_rejected_and_reads_do_not_write_cache(self):
        value = manifest(); value["concurrency"] = 2
        with self.assertRaises(RunBackendError): self.service.create_run(value)
        self.service.create_run(manifest())
        snapshot = pathlib.Path(self.directory.name) / "run-1" / "snapshot.json"
        self.assertFalse(snapshot.exists())
        self.service.get_snapshot("run-1"); self.service.list_runs()
        self.assertFalse(snapshot.exists())

    def test_top_level_usage_is_not_a_trusted_receipt(self):
        response = {"schema_version": "get-response/v1", "status": "success", "resolved_request": {}, "data": {}, "as_of": "t", "source": "s", "usage": {"receipt_id": "r"}}
        projected, usage = RunService._project_response(response)
        self.assertEqual((projected["status"], usage), ("invalid_public_response", "unknown"))

    def test_public_response_source_is_a_nonempty_string(self):
        response = public_response()
        self.assertEqual(RunService._project_response(response)[0]["status"], "success")
        for invalid in (None, "", ["s"], []):
            with self.subTest(invalid=invalid):
                self.assertEqual(RunService._project_response(dict(response, source=invalid))[0]["status"], "invalid_public_response")

    def test_terminal_usage_must_exactly_match_public_meta_receipt(self):
        response = {"schema_version": "get-response/v1", "status": "success", "resolved_request": {}, "data": {}, "as_of": "t", "source": "s", "meta": {"usage": {"receipt_id": "r", "measurement_version": "v", "cache_status": "miss", "request_id": "a", "issuer": "runner", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}}
        for source, forged_usage in (("public_meta_usage", dict(response["meta"]["usage"], total_tokens=4)), ("public_meta_usage", "unknown"), ("unknown", "unknown")):
            event = {"event_type": "terminal", "cell_id": "cell-1", "attempt_id": "attempt-1", "elapsed_ms": 0, "transport_status": "completed", "public_response": response, "response_hash": _digest(response), "usage": forged_usage, "usage_source": source, "sequence": 1, "manifest_hash": "a" * 64, "previous_event_hash": None}
            event["event_hash"] = _digest(event)
            with self.subTest(usage=forged_usage), self.assertRaises(RunBackendError): _validate_event(event, "run-1", 1, "a" * 64)
        without_meta = dict(response); without_meta.pop("meta")
        event = {"event_type": "terminal", "cell_id": "cell-1", "attempt_id": "attempt-1", "elapsed_ms": 0, "transport_status": "completed", "public_response": without_meta, "response_hash": _digest(without_meta), "usage": "unknown", "usage_source": "public_meta_usage", "sequence": 1, "manifest_hash": "a" * 64, "previous_event_hash": None}
        event["event_hash"] = _digest(event)
        with self.assertRaises(RunBackendError): _validate_event(event, "run-1", 1, "a" * 64)

    def test_ambiguous_external_action_is_limited_to_execute_or_gateway(self):
        def terminal(stage, external_cost="unknown"):
            event = RunService._terminal(
                "cell-1", "attempt-1", 0, "failed", "timeout", None, "unknown", "not_comparable",
                variant_identity=_variant_identity(variants()[0]), execution_profile="exploratory_ab",
                receipt_hashes={}, receipt_coverage={}, stage=stage, stage_attempted_count=1,
                stage_completed_count=0, stage_exception_class="TimeoutError", stage_error_code="timeout",
                external_action_may_have_occurred=True, external_cost=external_cost,
            )
            event.update({"sequence": 1, "manifest_hash": "a" * 64, "previous_event_hash": None})
            event["event_hash"] = _digest(event)
            return event

        for stage in ("qveris_execute", "gateway_completion"):
            with self.subTest(stage=stage):
                _validate_event(terminal(stage), "run-1", 1, "a" * 64)
        with self.assertRaises(RunBackendError):
            _validate_event(terminal("qveris_execute", external_cost=0), "run-1", 1, "a" * 64)
        for stage in ("model_preflight", "web_search", "qveris_search", "qveris_inspect"):
            with self.subTest(stage=stage), self.assertRaises(RunBackendError):
                _validate_event(terminal(stage), "run-1", 1, "a" * 64)

    def test_score_case_type_blocks_partial_and_error_before_execution(self):
        for status in ("partial", "error"):
            value = manifest(); value["scoring_contract"] = scoring_contract(); value["cases"][0]["score_case"] = {"expected_status": [status], "oracle_id": "oracle-one", "case_type": "normal"}
            with self.assertRaises(RunBackendError): self.service.create_run(value)


if __name__ == "__main__": unittest.main()

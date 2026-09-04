import copy
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.arena_http import main, make_server
from qveris_benchmark.benchmark_scorer import BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION
from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunService, RunStore, _digest, _variant_contract_digest, _variant_identity


def variants(prefix="variant"):
    identity = {"agent_version": "v1", "get_variant_id": "public-get", "get_version": "v1", "model_identifier": "test-model", "model_version": "v1", "model_config_digest": "e" * 64}
    return [{"variant_id": prefix + "-a", "stable_display_order": 1, "agent_variant_id": "agent-a", **identity}, {"variant_id": prefix + "-b", "stable_display_order": 2, "agent_variant_id": "agent-b", **identity}]


class FakeStore:
    def __init__(self):
        self.snapshot = {
            "schema_version": "arena-snapshot/v1",
            "run_id": "run-1",
            "status": "incomplete",
            "snapshot_sequence": 2,
            "event_cursor": 2,
            "variants": [
                {"variant_id": "alpha", "stable_display_order": 2, "metrics": {"data_accuracy": {"passed_weight": .9, "eligible_weight": 1, "value": .9}}},
                {"variant_id": "beta", "stable_display_order": 1},
            ],
        }
        self.events = [
            {"sequence": 1, "event": "variant_updated", "data": {"variant_id": "alpha"}},
            {"sequence": 2, "event": "run_completed", "data": {"run_id": "run-1"}},
        ]
        self.event_calls = []

    def list_runs(self):
        return [{"run_id": "run-1", "status": "incomplete"}]

    def get_snapshot(self, run_id):
        return self.snapshot if run_id == "run-1" else None

    def get_events(self, run_id, after_sequence=0):
        if run_id != "run-1":
            return None
        self.event_calls.append(after_sequence)
        return [event for event in self.events if event["sequence"] > after_sequence]


class ArenaHttpTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.server = make_server(self.store, heartbeat_interval=.01)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method="GET", headers=None):
        request = Request(self.base + path, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()

    def test_json_routes_pass_stable_order_through_store(self):
        status, headers, body = self.request("/v1/arena/runs")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(body)["runs"][0]["run_id"], "run-1")
        status, _, body = self.request("/v1/arena/runs/run-1/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual([v["variant_id"] for v in json.loads(body)["variants"]], ["alpha", "beta"])

    def test_variant_detail_uses_public_whitelist(self):
        status, _, body = self.request("/v1/arena/runs/run-1/variants/beta")
        self.assertEqual(status, 200)
        variant = json.loads(body)["variant"]
        self.assertEqual(variant["variant_id"], "beta")
        self.assertEqual(variant["stable_display_order"], 1)

    def test_not_found_bad_request_and_mutations(self):
        self.assertEqual(self.request("/v1/arena/runs/missing/snapshot")[0], 404)
        self.assertEqual(self.request("/v1/arena/runs/run-1/events?after=-1")[0], 400)
        self.assertEqual(self.request("/v1/arena/runs", method="POST")[0], 405)

    def test_sse_initial_snapshot_and_reconnect(self):
        status, headers, body = self.request("/v1/arena/runs/run-1/events")
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["Content-Type"])
        self.assertIn('"snapshot_sequence":2', text)
        self.assertNotIn("event: run_completed", text)
        status, _, body = self.request("/v1/arena/runs/run-1/events", headers={"Last-Event-ID": "1"})
        self.assertEqual(status, 200)
        self.assertNotIn("event: snapshot", body.decode())
        self.assertIn("id: 2", body.decode())
        self.assertIn(1, self.store.event_calls)

    def test_real_run_service_lists_safe_summary_over_http(self):
        with tempfile.TemporaryDirectory() as root:
            real_variants = variants("real")
            manifest = {"run_id": "run-real", "mode": "diagnostic", "freeze_digest": "a" * 64, "policy": {"version": "v1"}, "timeout_ms": 10, "concurrency": 1, "variants": real_variants, "cases": [{"case_id": "h", "suite": "historical_price", "query": "q"}]}
            service = RunService(RunStore(root), {"real-a": lambda *_args, **_kwargs: None, "real-b": lambda *_args, **_kwargs: None}, wall_clock=lambda: 1.0)
            service.create_run(manifest)
            server = make_server(service, heartbeat_interval=.01)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/v1/arena/runs", timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["runs"][0]["run_id"], "run-real")
                self.assertNotIn("variants", payload["runs"][0])
                self.assertEqual(payload["runs"][0]["projection_status"], "UNSCORED")
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_scored_run_is_safe_over_all_routes_and_sse(self):
        self._assert_scored_http(ranked=True, status="SCORED")

    def test_scored_not_ranked_run_is_safe_over_all_routes_and_sse(self):
        self._assert_scored_http(ranked=False, status="SCORED_NOT_RANKED")

    def _assert_scored_http(self, *, ranked, status):
        contracts = {"success": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]}, "partial": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]}, "needs_clarification": {"required_non_null_paths": ["clarification"], "required_null_paths": ["data", "terminal_reason"]}, "unsupported": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]}, "no_data": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]}, "error": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]}}
        policy = {"schema_version": "score-policy/v1", "metric_names": ["semantic_accuracy", "data_accuracy", "token_usage", "e2e_latency"], "percentile_method": "nearest_rank", "assertion_operators": ["exact", "within_abs"], "operator_registry": ["exact", "within_abs"], "case_pass_gate": ["schema_valid", "status_correct", "semantic_pass", "data_pass", "execution_complete"], "completeness": {}, "response_schema_version": "get-response/v1", "response_status_contracts": contracts, "max_reference_window_seconds": 60, "error": "disabled", "timeout_latency_treatment": "cap_at_timeout", "usage_receipt_required_fields": ["receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"], "trusted_receipt_issuers": ["runner"], "eligibility": {"semantic_coverage_min": 1, "oracle_coverage_min": 1, "receipt_coverage_min": 1, "require_complete_execution": True} if ranked else None, "ranking": {"ordered_keys": ["case_pass_rate", "data_accuracy", "semantic_accuracy", "e2e_p95_ms", "average_total_tokens"], "directions": ["desc", "desc", "desc", "asc", "asc"], "tie_break": "variant_id"} if ranked else None}
        oracle = {"schema_version": "oracle-bundle/v1", "oracles": {"oracle-one": {"oracle_id": "oracle-one", "case_id": "case-one", "independence": "independent_frozen", "semantic_assertions": [{"path": "resolved_request.symbol", "operator": "exact", "expected": "ABC", "tolerance": None, "weight": 1, "fatal": True}], "data_assertions": [{"path": "data.close", "operator": "exact", "expected": 10, "tolerance": None, "weight": 1, "fatal": True}], "state_assertions": [], "reference_evidence": None, "source_ref": "frozen", "version": "v1", "semantic_review_status": "approved", "data_review_status": "approved", "state_review_status": "not_applicable"}}}
        score_variants = variants()
        manifest = {"run_id": "score-http", "mode": "diagnostic", "freeze_digest": "a" * 64, "policy": {"version": "v1"}, "timeout_ms": 100, "concurrency": 1, "scoring_contract": {"policy_digest": _digest(policy), "oracle_bundle_digest": _digest(oracle), "scorer_version": SCORER_VERSION, "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(score_variants)}, "variants": score_variants, "cases": [{"case_id": "case-one", "suite": "historical_price", "query": "safe", "score_case": {"expected_status": ["success"], "oracle_id": "oracle-one", "case_type": "normal"}}]}
        class Client:
            def __init__(self, variant_id): self.variant_id = variant_id
            def run(self, _query, **kwargs):
                value = {"schema_version": "get-response/v1", "status": "success", "resolved_request": {"symbol": "ABC"}, "data": {"close": 10}, "as_of": "2026-09-03T00:00:00Z", "source": "frozen", "meta": {"usage": {"receipt_id": "receipt-secret", "measurement_version": "usage-v1", "cache_status": "miss", "request_id": kwargs["request_id"].replace("request-", "attempt-", 1), "issuer": "runner", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}}
                identity = _variant_identity(next(item for item in score_variants if item["variant_id"] == self.variant_id))
                return PublicGetResult(copy.deepcopy(value), ExecutionEvidence(**identity, agent_invocations=1, tool_executions=1, structured_outputs=1, tools_used=("get",)))
        with tempfile.TemporaryDirectory() as root:
            service = RunService(RunStore(root), {"variant-a": Client("variant-a"), "variant-b": Client("variant-b")})
            service.create_run(manifest); service.execute("score-http")
            BenchmarkScorer(service.store, policy=policy, oracle_bundle=oracle, approved_policy_digests={_digest(policy)}, approved_oracle_bundle_digests={_digest(oracle)}).score("score-http")
            execution_cursor = service.get_snapshot("score-http")["event_cursor"] - 1
            server = make_server(service, heartbeat_interval=.01)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f"http://127.0.0.1:{server.server_port}/v1/arena/runs/score-http"
            try:
                with urlopen(base.rsplit("/", 1)[0], timeout=2) as response: runs = json.loads(response.read())
                with urlopen(base + "/snapshot", timeout=2) as response: snapshot = json.loads(response.read())
                with urlopen(base + "/variants/variant-a", timeout=2) as response: variant = json.loads(response.read())
                with urlopen(Request(base + "/events", headers={"Last-Event-ID": str(execution_cursor)}), timeout=2) as response: events = "".join(response.readline().decode() for _ in range(4))
                self.assertEqual(runs["runs"][0]["projection_status"], status)
                self.assertEqual(snapshot["projection_status"], status)
                self.assertEqual(variant["variant"]["metrics"]["token_usage"]["total_mean"], 5.0)
                self.assertIn("end_to_end_latency", variant["variant"]["metrics"])
                self.assertNotIn("e2e_latency", variant["variant"]["metrics"])
                self.assertEqual(snapshot["scoring"]["end_to_end_latency"], "SCORED")
                self.assertIn("event: scorer_projection", events)
                self.assertIn('"projection_status":"' + status + '"', events)
                self.assertNotIn("oracle_id", json.dumps([runs, snapshot, variant, events]))
                self.assertNotIn("receipt-secret", json.dumps([runs, snapshot, variant, events]))
                self.assertNotIn("raw_response", json.dumps([runs, snapshot, variant, events]))
                self.assertNotIn("execution_evidence", json.dumps([runs, snapshot, variant, events]))
                self.assertNotIn("model_config_digest", json.dumps([runs, snapshot, variant, events]))
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_cli_help_parses_without_starting_server(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_sse_resync_does_not_invent_events(self):
        status, _, body = self.request("/v1/arena/runs/run-1/events?after=3")
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("event: resync_required", text)
        self.assertIn("/v1/arena/runs/run-1/snapshot", text)
        self.assertNotIn("id:", text)

    def test_live_sse_flushes_configured_heartbeat(self):
        self.store.snapshot["status"] = "running"
        response = urlopen(self.base + "/v1/arena/runs/run-1/events", timeout=1)
        try:
            lines = [response.readline().decode() for _ in range(6)]
            self.assertIn(": heartbeat\n", lines)
        finally:
            response.close()

    def test_sse_gap_requires_resync_without_events(self):
        self.store.snapshot["snapshot_sequence"] = 3
        self.store.events[1]["sequence"] = 3
        status, _, body = self.request("/v1/arena/runs/run-1/events?after=1")
        self.assertEqual(status, 200)
        self.assertIn("event: resync_required", body.decode())
        self.assertNotIn("id:", body.decode())

    def test_cors_exact_allowlist_only(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server = make_server(self.store, allowed_origin="https://arena.example")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.request("/v1/arena/runs", headers={"Origin": "https://arena.example"})[1]["Access-Control-Allow-Origin"], "https://arena.example")
        self.assertNotIn("Access-Control-Allow-Origin", self.request("/v1/arena/runs", headers={"Origin": "https://elsewhere.example"})[1])

    def test_sensitive_store_projection_is_rejected(self):
        self.store.snapshot["variants"][0]["raw_response"] = {"token": "do-not-leak"}
        status, _, body = self.request("/v1/arena/runs/run-1/snapshot")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body), {"error": "unsafe_projection"})

    def test_unknown_snapshot_variant_and_event_fields_are_rejected(self):
        self.store.snapshot["variants"][0]["unknown_field"] = True
        self.assertEqual(self.request("/v1/arena/runs/run-1/snapshot")[0], 500)
        self.store.snapshot["variants"][0].pop("unknown_field")
        self.store.events[0]["unknown_field"] = True
        self.assertEqual(self.request("/v1/arena/runs/run-1/events")[0], 500)

    def test_credential_like_nested_projection_is_rejected(self):
        self.store.snapshot["variants"][0]["metrics"]["privateKey"] = "nope"
        self.assertEqual(self.request("/v1/arena/runs/run-1/snapshot")[0], 500)

    def test_server_refuses_non_loopback_hosts(self):
        with self.assertRaises(ValueError):
            make_server(self.store, host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()

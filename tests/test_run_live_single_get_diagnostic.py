import importlib.util
import json
import pathlib
import stat
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("single_get_diagnostic", ROOT / "scripts" / "run_live_single_get_diagnostic.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
sys.path.insert(0, str(ROOT / "src"))

from qveris_benchmark.live_get_client import QVerisPublicGetConfig
from qveris_benchmark.qveris_model_gateway import SemanticGatewayError
from qveris_benchmark.qveris_tool_gateway import ToolCreditReceipt
from qveris_benchmark.benchmark_scorer import BenchmarkScoreError, BenchmarkScorer
from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunStore


class Preflight:
    def __init__(self, model, available):
        self.configured_model, self.available_model_ids = model, tuple(available)


class Resolver:
    def __init__(self, model, available):
        self.model, self.available, self.calls = model, available, []

    def preflight_models(self, *, request_id):
        self.calls.append(request_id)
        return Preflight(self.model, self.available)


class Client:
    def __init__(self, config, available=None, tool_receipt_sink=None, emit_receipt=False, tool_executions=1):
        self.identity = config.identity()
        self.semantic_resolver = Resolver(config.model, [config.model] if available is None else available)
        self.calls = []
        self.tool_receipt_sink, self.emit_receipt, self.tool_executions = tool_receipt_sink, emit_receipt, tool_executions

    def run(self, query, *, request_id, idempotency_key):
        self.calls.append((query, request_id, idempotency_key))
        if self.emit_receipt:
            self.tool_receipt_sink(ToolCreditReceipt(
                "alphavantage.global_quote.retrieve.v1.9b8a7c6d", request_id,
                "CANARY_PRIVATE_EXECUTION", 2.5, request_id, True,
            ))
        response = {
            "schema_version": "get-response/v1", "status": "success",
            "resolved_request": {"suite": "realtime_quote", "accepted_variant_id": "quote-snapshot"},
            "data": {"kind": "realtime_quote", "quote": {"instrument": {"symbol": "AAPL", "market": "US"}, "fields": {"last_price": {"value": "1", "unit": "USD_per_share", "as_of": "2026-09-05T00:00:00Z", "nil": False}}}},
            "as_of": "2026-09-05T00:00:00Z", "source": "mock",
            "clarification": None, "terminal_reason": None,
            "meta": {"usage": {"receipt_id": "a" * 64, "measurement_version": "v1", "cache_status": "not_reported", "request_id": request_id, "issuer": "qveris_model_gateway", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
        }
        return PublicGetResult(response, ExecutionEvidence(**self.identity, agent_invocations=1, tool_executions=self.tool_executions, structured_outputs=1, tools_used=("get",) if self.tool_executions else ()))


class SingleGetDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name)
        self.config = QVerisPublicGetConfig("model-key", "tool-key", "deepseek-v4-flash")
        self.runtime_case = self.path / "runtime-case.json"
        self.runtime_case.write_text(json.dumps({
            "case_id": "RTQ-025", "suite": "realtime_quote", "query": "苹果当前正常交易时段价格是多少美元？",
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_preflight_then_exactly_one_agent_and_get_without_oracle_or_score(self):
        client = Client(self.config)
        result = MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=lambda _config, **_kwargs: client)
        self.assertEqual((len(client.semantic_resolver.calls), len(client.calls)), (1, 1))
        self.assertEqual((result["projection_status"], result["ranking"], result["internal_status"]), ("UNSCORED", "non-ranking", "execution_complete"))
        store = RunStore(self.path / "outside")
        events = store.events(result["run_id"])
        self.assertEqual([event["event_type"] for event in events], ["run_started", "dispatch_intent", "terminal", "run_finished"])
        terminal = events[-2]
        self.assertEqual((terminal["comparability"], terminal["scoring_status"], terminal["ranking"]), ("not_applicable", "UNSCORED", "non-ranking"))
        manifest = store.load_manifest(result["run_id"])
        self.assertNotIn("scoring_contract", manifest)
        self.assertNotIn("score_case", manifest["cases"][0])
        self.assertEqual(store.score_events(result["run_id"]), [])
        self.assertEqual(stat.S_IMODE((self.path / "outside").stat().st_mode), 0o700)
        for name in ("manifest.json", "events.jsonl", "snapshot.json"):
            self.assertEqual(stat.S_IMODE((self.path / "outside" / result["run_id"] / name).stat().st_mode), 0o600)
        self.assertFalse(result["private_tool_receipt_written"])
        self.assertFalse((self.path / "outside" / result["run_id"] / "tool-receipt.json").exists())

    def test_unavailable_model_stops_before_agent_or_get_and_creates_no_run(self):
        client = Client(self.config, available=[])
        with self.assertRaisesRegex(MODULE.DiagnosticError, "^model_unavailable$"):
            MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=lambda _config, **_kwargs: client)
        self.assertEqual((len(client.semantic_resolver.calls), len(client.calls)), (1, 0))
        self.assertFalse((self.path / "outside").exists())

    def test_preflight_failure_codes_are_safe_and_stop_before_get(self):
        for code in ("model_preflight_timeout", "model_preflight_http_401", "invalid_json"):
            with self.subTest(code=code):
                client = Client(self.config)

                class FailureResolver:
                    def preflight_models(self, *, request_id):
                        raise SemanticGatewayError(code)

                client.semantic_resolver = FailureResolver()
                with self.assertRaisesRegex(MODULE.DiagnosticError, "^" + code + "$") as raised:
                    MODULE.run_once(runtime_case=self.runtime_case, output=self.path / ("outside-" + code), config=self.config, client_builder=lambda _config, **_kwargs: client)
                self.assertNotIn("CANARY", str(raised.exception))
                self.assertEqual(client.calls, [])

    def test_invalid_preflight_object_is_internal_error_without_get_or_raw_message(self):
        client = Client(self.config)

        class FailureResolver:
            def preflight_models(self, *, request_id):
                raise RuntimeError("CANARY_GATEWAY_BODY")

        client.semantic_resolver = FailureResolver()
        with self.assertRaisesRegex(MODULE.DiagnosticError, "^internal_error$") as raised:
            MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=lambda _config, **_kwargs: client)
        self.assertNotIn("CANARY", str(raised.exception))
        self.assertEqual(client.calls, [])

    def test_manifest_budget_covers_model_tool_and_margin(self):
        manifest = MODULE._manifest(MODULE._public_case(self.runtime_case, "RTQ-025"), self.config.identity())
        self.assertEqual(MODULE.SINGLE_GET_TIMEOUT_SECONDS, 90.0)
        self.assertGreaterEqual(MODULE.SINGLE_GET_TIMEOUT_SECONDS, 60.0 + 15.0 + MODULE.SINGLE_GET_TIMEOUT_MARGIN_SECONDS)
        self.assertEqual(manifest["timeout_ms"], 90_000)

    def test_selected_case_projects_only_public_runtime_fields(self):
        case = MODULE._public_case(self.runtime_case, "RTQ-025")
        self.assertEqual(set(case), {"case_id", "suite", "query"})
        self.assertNotIn("expected_status", MODULE._manifest(case, self.config.identity())["cases"][0])

    def test_private_tool_receipt_is_whitelisted_hashed_and_mode_0600(self):
        box = {}

        def build(config, *, tool_receipt_sink):
            client = Client(config, tool_receipt_sink=tool_receipt_sink, emit_receipt=True)
            box["client"] = client
            return client

        result = MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=build)
        receipt = self.path / "outside" / result["run_id"] / "tool-receipt.json"
        value = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertTrue(result["private_tool_receipt_written"])
        self.assertEqual(set(value), {"schema_version", "tool_id", "request_id_sha256", "execution_id_sha256", "actual_credits"})
        self.assertEqual((value["schema_version"], value["actual_credits"]), ("single-get-tool-receipt/v1", 2.5))
        self.assertEqual(len(value["request_id_sha256"]), 64)
        self.assertEqual(len(value["execution_id_sha256"]), 64)
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        public_run = "\n".join((self.path / "outside" / result["run_id"] / name).read_text(encoding="utf-8") for name in ("manifest.json", "events.jsonl", "snapshot.json"))
        self.assertNotIn("CANARY_PRIVATE_EXECUTION", public_run)
        self.assertNotIn("CANARY_PRIVATE_EXECUTION", json.dumps(value))

    def test_no_tool_call_does_not_create_a_private_receipt(self):
        client = Client(self.config, tool_executions=0)
        result = MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=lambda _config, **_kwargs: client)
        self.assertEqual(result["internal_status"], "execution_failed")
        self.assertFalse(result["private_tool_receipt_written"])
        self.assertFalse((self.path / "outside" / result["run_id"] / "tool-receipt.json").exists())

    def test_extra_runtime_case_field_fails_before_preflight_or_client(self):
        self.runtime_case.write_text(json.dumps({"case_id": "RTQ-025", "suite": "realtime_quote", "query": "q", "expected": "CANARY"}), encoding="utf-8")
        client = Client(self.config)
        with self.assertRaisesRegex(MODULE.DiagnosticError, "only the selected public case"):
            MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=lambda _config, **_kwargs: client)
        self.assertEqual((client.semantic_resolver.calls, client.calls), ([], []))

    def test_scorer_rejects_diagnostic_public_get_profile(self):
        client = Client(self.config)
        result = MODULE.run_once(runtime_case=self.runtime_case, output=self.path / "outside", config=self.config, client_builder=lambda _config, **_kwargs: client)
        scorer = BenchmarkScorer(RunStore(self.path / "outside"), policy={}, oracle_bundle={}, approved_policy_digests=set(), approved_oracle_bundle_digests=set())
        with self.assertRaisesRegex(BenchmarkScoreError, "diagnostic runs"):
            scorer.score(result["run_id"])


if __name__ == "__main__":
    unittest.main()

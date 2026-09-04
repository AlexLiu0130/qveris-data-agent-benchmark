import json
import pathlib
import sys
import tempfile
import time
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.benchmark_scorer import BenchmarkScoreError, BenchmarkScorer
from qveris_benchmark.model_gateway import GatewayBilling, GatewayChatCompletion, GatewayModel, GatewayUsage, ModelGatewayProtocolError, ModelGatewayTransportError
from qveris_benchmark.qveris_search import SearchCatalog, SearchTool, ToolExecution, ToolInspection
from qveris_benchmark.run_backend import RunBackendError, RunService, RunStore, _digest
from qveris_benchmark.runner_gateway_agent import AGENT_VERSION, MODEL_ID, MODEL_MAX_TOKENS, OUTPUT_SCHEMA_VERSION, RunnerGatewayAgent, ToolFreeze, WebSearchResult, WebSource, _facts, _messages, _schema, output_contract_digests, tool_freeze


CASE = {"case_id": "FS-049", "suite": "financial_statements", "query": "NVIDIA FY2026 income statement", "canonical_request": {"entity": {"symbol": "NVDA"}, "data_type": "financial_statement", "statement_type": "income_statement", "time_or_period": {"fiscal_year": 2026}}}
METADATA = [{"assertion_id": "income-1", "label": "Revenue", "currency": "USD", "unit": "USD_millions", "period": "FY2026"}]
SELECTOR = {"schema_version": "fs049-result-selector/v1", "parameters": {"symbol": "NVDA", "period": "FY", "limit": 5}, "target_symbol": "NVDA", "target_period": "FY", "target_fiscal_year": 2026, "target_date": "2026-01-25", "statement_type": "income_statement", "consolidated": True}
SELECTOR_PARAMS = (
    {"name": "symbol", "type": "string", "required": True, "description": "ticker"},
    {"name": "period", "type": "string", "required": True, "description": "period", "enum": ["FY"]},
    {"name": "limit", "type": "number", "required": True, "description": "row limit"},
)
TOOL = SELECTOR_TOOL = SearchTool("tool.financial", "Financial", "bounded", SELECTOR_PARAMS, "0.1", None)
FREEZE = tool_freeze(tool=TOOL, data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026)
SELECTOR_FREEZE = tool_freeze(tool=TOOL, data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026, result_selector=SELECTOR)


def variant(name, order):
    return {"variant_id": name, "stable_display_order": order, "agent_variant_id": "gateway-agent-" + name, "agent_version": AGENT_VERSION, "get_variant_id": "not-a-get", "get_version": "v1", "model_identifier": MODEL_ID, "model_version": "gateway-v1", "model_config_digest": _digest({"model_id": MODEL_ID, "temperature": 0.0, "max_tokens": MODEL_MAX_TOKENS, "response_format": "json_object"}), **output_contract_digests(METADATA, CASE["query"])}


def manifest():
    return {"run_id": "fs049-exploratory-ab-v1", "mode": "diagnostic", "execution_profile": "exploratory_ab", "freeze_digest": "a" * 64, "policy": {"version": "exploratory-ab/v1", "scope": "exploratory_nonranking"}, "timeout_ms": 1000, "concurrency": 1, "variants": [variant("web-model", 1), variant("qveris-model", 2)], "cases": [CASE]}


class Gateway:
    def __init__(self, models=(MODEL_ID,), usage=None):
        self.models, self.usage, self.list_calls, self.chat_calls = models, usage, [], []

    def list_models(self, *, request_id):
        self.list_calls.append(request_id)
        return tuple(GatewayModel(model_id=model) for model in self.models)

    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        values = {item["assertion_id"]: 1 for item in METADATA}
        return GatewayChatCompletion(200, MODEL_ID, kwargs["request_id"], "call-%d" % len(self.chat_calls), None, json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": values}), "stop", self.usage, GatewayBilling("call-%d" % len(self.chat_calls), .1, .01, False))


class Web:
    def __init__(self):
        self.calls = []
        self.last_receipt = type("Receipt", (), {"request_id": "tavily-request-1", "credits": 1.0})()
    def search(self, **kwargs):
        self.calls.append(kwargs)
        return WebSearchResult(kwargs["query"], "2026-09-04T00:00:00Z", (WebSource("https://example.test/nvda", "Source", "bounded"),))


class Search:
    def __init__(self, failure=None, incomplete=False, drift_cost=None, drift_schema=False, wrong_inspect=False, actual_cost=.1): self.calls, self.failure, self.incomplete, self.drift_cost, self.drift_schema, self.wrong_inspect, self.actual_cost = [], failure, incomplete, drift_cost, drift_schema, wrong_inspect, actual_cost
    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        if self.failure: raise self.failure
        params = None if self.incomplete else (({"name": "ticker", "type": "string", "required": True, "description": "ticker"},) if self.drift_schema else TOOL.params)
        return SearchCatalog("search-1", (SearchTool("tool.financial", "Financial", "bounded", params, self.drift_cost or "0.1", None),), None, "search-call")
    def inspect(self, **kwargs):
        self.calls.append(("inspect", kwargs))
        if not self.incomplete: raise AssertionError("complete schema must not inspect")
        return ToolInspection(SearchTool("wrong-tool" if self.wrong_inspect else TOOL.tool_id, TOOL.name, TOOL.description, TOOL.params, TOOL.expected_cost, TOOL.billing_rule), None, "inspect-call")
    def execute(self, **kwargs):
        self.calls.append(("execute", kwargs))
        return ToolExecution(kwargs["tool_id"], "execution-1", self.actual_cost, 9.9, {"safe": True}, "execute-call")


class StageFailureSearch(Search):
    def __init__(self, stage):
        super().__init__(incomplete=stage == "qveris_inspect")
        self.stage = stage

    def inspect(self, **kwargs):
        if self.stage == "qveris_inspect":
            self.calls.append(("inspect", kwargs)); raise RuntimeError("inspect down")
        return super().inspect(**kwargs)

    def search(self, **kwargs):
        if self.stage == "qveris_search":
            self.calls.append(("search", kwargs)); raise RuntimeError("search down")
        return super().search(**kwargs)

    def execute(self, **kwargs):
        if self.stage == "qveris_execute":
            self.calls.append(("execute", kwargs)); raise RuntimeError("execute outcome unknown")
        return super().execute(**kwargs)


class FrozenContractSearch:
    def __init__(self, search_tool, inspect_tool):
        self.search_tool, self.inspect_tool, self.calls = search_tool, inspect_tool, []

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return SearchCatalog("search-1", (self.search_tool,), None, "search-call")

    def inspect(self, **kwargs):
        self.calls.append(("inspect", kwargs))
        return ToolInspection(self.inspect_tool, None, "inspect-call")

    def execute(self, **kwargs):
        raise AssertionError("ToolFreeze mismatch must stop before Execute")


class FailingGateway(Gateway):
    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        raise RuntimeError("gateway down")


class TimeoutGateway(Gateway):
    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        raise ModelGatewayTransportError("raw-provider-secret", error_code="timeout")


class InvalidJsonGateway(Gateway):
    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        raise ModelGatewayProtocolError(
            "raw-model-body-must-not-persist",
            status_code=502,
            error_code="invalid_json",
            call_id="call-diagnostic",
            gateway_diagnostic={
                "http_status": 502,
                "content_type_class": "html",
                "content_encoding_class": "identity",
                "charset_class": "utf8",
                "declared_body_bytes": 31,
                "observed_body_bytes": 31,
                "body_state": "invalid_json",
                "body_sha256": "d" * 64,
                "call_id_sha256": "e" * 64,
            },
        )


class OuterTimeoutGateway(Gateway):
    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        time.sleep(.05)


class OuterTimeoutSearch(Search):
    def execute(self, **kwargs):
        self.calls.append(("execute", kwargs))
        time.sleep(.05)


class InvalidSchemaGateway(Gateway):
    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        return GatewayChatCompletion(200, MODEL_ID, kwargs["request_id"], "schema-call", None, "raw-model-content-must-not-persist", "stop", GatewayUsage(3, 5, 8), GatewayBilling("schema-call", .3, .03, False))


class LengthGateway(Gateway):
    def chat_completions(self, **kwargs):
        self.chat_calls.append(kwargs)
        return GatewayChatCompletion(200, MODEL_ID, kwargs["request_id"], "length-call", None, '{"truncated":', "length", GatewayUsage(3, 4096, 4099), GatewayBilling("length-call", .3, .03, False))


class SelectorSearch(Search):
    def execute(self, **kwargs):
        self.calls.append(("execute", kwargs))
        return ToolExecution(kwargs["tool_id"], "execution-1", self.actual_cost, 9.9, [{"symbol": "NVDA", "period": "FY", "fiscalYear": 2026, "date": "2026-01-25", "statement_type": "income_statement", "consolidated": True}], "execute-call")


class RunnerGatewayAgentTests(unittest.TestCase):
    def test_runservice_calls_each_gateway_agent_once_with_honest_topology(self):
        gateway, web, search = Gateway(), Web(), Search()
        variants = manifest()["variants"]
        clients = {"web-model": RunnerGatewayAgent(variant=variants[0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web), "qveris-model": RunnerGatewayAgent(variant=variants[1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE)}
        with tempfile.TemporaryDirectory() as directory:
            service = RunService(RunStore(directory), clients); service.create_run(manifest()); snapshot = service.execute("fs049-exploratory-ab-v1")
            events = service.get_events("fs049-exploratory-ab-v1")
            terminals = [event for event in events if event["event_type"] == "terminal"]
        self.assertEqual(snapshot["execution"]["success"], 2)
        self.assertEqual((len(web.calls), [item[0] for item in search.calls], len(gateway.chat_calls)), (1, ["search", "execute"], 2))
        self.assertEqual([event["execution_evidence"]["tools_used"] for event in terminals], [["web_search"], ["qveris_search", "qveris_execute"]])
        self.assertEqual(len(gateway.list_calls), 1)
        self.assertEqual(len([event for event in events if event["event_type"] == "model_preflight"]), 1)
        self.assertEqual(set(terminals[0]["external_receipts"]), {"tavily"})
        self.assertEqual(set(terminals[1]["external_receipts"]), {"qveris_execute"})
        self.assertTrue(all(event["execution_profile"] == "exploratory_ab" and event["gateway_receipt"]["request_id"] == event["attempt_id"] for event in terminals))
        self.assertTrue(all(event["usage"] == "unknown" and event["gateway_receipt"]["usage"] == "unknown" for event in terminals))
        self.assertTrue(all(call["model_id"] == MODEL_ID and call["temperature"] == 0.0 and call["max_tokens"] == MODEL_MAX_TOKENS and call["response_format"] == "json_object" for call in gateway.chat_calls))
        messages = gateway.chat_calls[0]["messages"]
        self.assertIn("values maps every", messages[0]["content"])
        self.assertIn("Status semantics are strict", messages[0]["content"])
        self.assertIn("statement_lines", messages[0]["content"])
        self.assertIn("no markdown, analysis", messages[0]["content"])
        prompt = json.loads(messages[1]["content"])
        schema = prompt["output_schema"]
        self.assertEqual((schema["additionalProperties"], schema["properties"]["values"]["minProperties"], schema["properties"]["values"]["maxProperties"], schema["properties"]["values"]["additionalProperties"]), (False, 1, 1, False))
        self.assertNotIn("required_metadata", schema)
        self.assertNotIn("Revenue", json.dumps(schema))

    def test_requested_values_expose_only_output_metadata_not_oracle_values(self):
        metadata = [{**METADATA[0], "expected": "secret", "tolerance": "secret"}]
        prompt = json.loads(_messages(CASE["query"], _schema(metadata), metadata, {})[1]["content"])
        self.assertEqual(prompt["requested_values"], [METADATA[0]])
        self.assertNotIn("expected", json.dumps(prompt))
        self.assertNotIn("tolerance", json.dumps(prompt))

    def test_prompt_digest_binds_static_user_envelope_without_changing_schema_digest(self):
        relabeled = [{**METADATA[0], "label": "Revenue relabeled"}]
        original, changed = output_contract_digests(METADATA, CASE["query"]), output_contract_digests(relabeled, CASE["query"])
        self.assertNotEqual(original["prompt_contract_digest"], changed["prompt_contract_digest"])
        self.assertEqual(original["output_schema_digest"], changed["output_schema_digest"])
        self.assertNotIn("expected", json.dumps(_messages(CASE["query"], _schema([{**METADATA[0], "expected": "secret"}]), [{**METADATA[0], "expected": "secret"}], {})[1]))

    def test_v5_prompt_contract_digest_is_frozen(self):
        self.assertEqual(
            output_contract_digests(METADATA, CASE["query"])["prompt_contract_digest"],
            "f084e711f7746b6e69551bea6f44da81d9a5d18900a8372f5bdbd9385e7590d9",
        )

    def test_output_contract_enforces_status_value_distribution_with_safe_code(self):
        two_metadata = METADATA + [{"assertion_id": "income-2", "label": "Operating income", "currency": "USD", "unit": "USD_millions", "period": "FY2026"}]
        valid = {
            "success": {"income-1": " 1.25 ", "income-2": 2},
            "partial": {"income-1": " 1.25 ", "income-2": None},
            "no_data": {"income-1": None, "income-2": None},
            "needs_clarification": {"income-1": None, "income-2": None},
            "unsupported": {"income-1": None, "income-2": None},
            "error": {"income-1": None, "income-2": None},
        }
        for status, status_values in valid.items():
            with self.subTest(status=status):
                self.assertEqual(
                    [fact["value"] for fact in _facts(json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": status, "values": status_values}), two_metadata)["facts"]],
                    list(status_values.values()),
                )
        for status, status_values in (
            ("success", {"income-1": 1, "income-2": None}),
            ("partial", {"income-1": 1, "income-2": 2}),
            ("partial", {"income-1": None, "income-2": None}),
            ("no_data", {"income-1": 1, "income-2": None}),
            ("needs_clarification", {"income-1": 1, "income-2": None}),
            ("unsupported", {"income-1": 1, "income-2": None}),
            ("error", {"income-1": 1, "income-2": None}),
        ):
            with self.subTest(status=status, values=status_values), self.assertRaisesRegex(ValueError, "status_value_mismatch"):
                _facts(json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": status, "values": status_values}), two_metadata)

    def test_output_contract_accepts_and_projects_compact_values_with_safe_codes(self):
        values = {"income-1": " 1.25 "}
        two_metadata = METADATA + [{"assertion_id": "income-2", "label": "Operating income", "currency": "USD", "unit": "USD_millions", "period": "FY2026"}]
        self.assertEqual(_facts(json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": values}), METADATA)["facts"], [{**METADATA[0], "value": " 1.25 "}])
        invalid = {
            "invalid_json": "{",
            "top_level_schema": json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "facts": []}),
            "value_count": json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {}}),
            "extra_value_count": json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": None, "other": None}}),
            "unknown_or_missing_id": json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": None, "other": None}}),
            "value_type": json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": []}}),
        }
        for code, content in invalid.items():
            expected_code = "value_count" if code == "extra_value_count" else code
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, expected_code):
                _facts(content, two_metadata if code == "unknown_or_missing_id" else METADATA)
        with self.assertRaisesRegex(ValueError, "status_value_mismatch"):
            _facts(json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": None}}), METADATA)
        self.assertEqual(_facts(json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "needs_clarification", "values": {"income-1": None}}), METADATA)["facts"], [{**METADATA[0], "value": None}])
        for content in (
            '{"schema_version":"exploratory-financial-answer/v3","status":"success","values":{"income-1":1,"income-1":2}}',
            '{"schema_version":"exploratory-financial-answer/v3","status":"success","values":{"income-1":NaN}}',
            json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": float("inf")}}),
            json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": ""}}),
            json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": True}}),
            json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "status": "success", "values": {"income-1": None}, "statement_lines": []}),
        ):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "invalid_json|value_type|top_level_schema"):
                _facts(content, METADATA)

    def test_v5_config_and_contract_digest_drift_fail_closed(self):
        value = manifest()["variants"][0]
        self.assertEqual(MODEL_MAX_TOKENS, 8192)
        for stale_version in ("exploratory-ab-gateway-v3", "exploratory-ab-gateway-v4"):
            with self.subTest(stale_version=stale_version), self.assertRaisesRegex(ValueError, "agent version"):
                RunnerGatewayAgent(variant=dict(value, agent_version=stale_version), case=CASE, metadata=METADATA, gateway=Gateway(), web_search=Web())
        with self.assertRaisesRegex(ValueError, "model configuration"):
            RunnerGatewayAgent(variant=dict(value, model_config_digest="0" * 64), case=CASE, metadata=METADATA, gateway=Gateway(), web_search=Web())
        with self.assertRaisesRegex(ValueError, "model configuration"):
            RunnerGatewayAgent(variant=dict(value, model_config_digest=_digest({"model_id": MODEL_ID, "temperature": 0.0, "max_tokens": 2048, "response_format": "json_object"})), case=CASE, metadata=METADATA, gateway=Gateway(), web_search=Web())
        with self.assertRaisesRegex(ValueError, "output contract"):
            RunnerGatewayAgent(variant=dict(value, metadata_digest="0" * 64), case=CASE, metadata=METADATA, gateway=Gateway(), web_search=Web())

    def test_runservice_rejects_output_contract_digest_drift_before_model_preflight(self):
        value, gateway, web = manifest(), Gateway(), Web()
        client = RunnerGatewayAgent(variant=value["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        value["variants"] = [dict(value["variants"][0], output_schema_digest="0" * 64)]
        with tempfile.TemporaryDirectory() as directory:
            service = RunService(RunStore(directory), {"web-model": client})
            service.create_run(value)
            with self.assertRaisesRegex(RunBackendError, "output contract digest"):
                service._preflight_exploratory(value, [])
        self.assertEqual((gateway.list_calls, web.calls), ([], []))

    def test_qveris_inspect_is_used_once_only_when_search_omits_the_schema(self):
        gateway, search = Gateway(), Search(incomplete=True)
        client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][1]]; service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
            terminal = next(event for event in service.get_events(value["run_id"]) if event["event_type"] == "terminal")
        self.assertEqual(([item[0] for item in search.calls], terminal["execution_evidence"]["tools_used"], len(gateway.chat_calls)), (["search", "inspect", "execute"], ["qveris_search", "qveris_inspect", "qveris_execute"], 1))

    def test_model_preflight_missing_has_no_fallback_or_completion_retry(self):
        gateway, web = Gateway(("other-model",)), Web()
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        with tempfile.TemporaryDirectory() as directory:
            service = RunService(RunStore(directory), {"web-model": client})
            value = manifest(); value["variants"] = [value["variants"][0]]; service.create_run(value); service.execute(value["run_id"])
            terminal = next(event for event in service.get_events(value["run_id"]) if event["event_type"] == "terminal")
        self.assertEqual((len(gateway.list_calls), len(gateway.chat_calls), len(web.calls), terminal["transport_status"]), (1, 0, 0, "failed"))

    def test_qveris_failure_does_not_retry_or_call_gateway(self):
        gateway, search = Gateway(), Search(RuntimeError("down"))
        client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][1]]; service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
        self.assertEqual((len(gateway.list_calls), len(search.calls), len(gateway.chat_calls)), (1, 1, 0))

    def test_tool_contract_drift_or_wrong_inspect_fails_before_execute(self):
        for search in (Search(drift_cost="0.2"), Search(drift_schema=True), Search(incomplete=True, wrong_inspect=True)):
            with self.subTest(search=search):
                gateway = Gateway()
                client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE)
                with tempfile.TemporaryDirectory() as directory:
                    value = manifest(); value["variants"] = [value["variants"][1]]; service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
                self.assertEqual(([item[0] for item in search.calls], len(gateway.chat_calls)), (["search", "inspect"] if search.incomplete else ["search"], 0))

    def test_tool_freeze_mismatches_have_redacted_stable_terminal_codes(self):
        freeze = tool_freeze(tool=SELECTOR_TOOL, data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026, result_selector=SELECTOR)
        symbol_only = SearchTool("tool.financial", "Financial", "bounded", SELECTOR_PARAMS[:1], "0.1", None)
        raw_billing = {"private": "raw-provider-secret-a"}
        mismatches = (
            ("tool_id_mismatch", tool_freeze(tool=SELECTOR_TOOL, data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026, result_selector=SELECTOR), FrozenContractSearch(SearchTool("other-tool", "Financial", "bounded", SELECTOR_PARAMS, "0.1", None), SELECTOR_TOOL), "qveris_search"),
            ("param_schema_digest_mismatch", freeze, FrozenContractSearch(SELECTOR_TOOL, SearchTool("tool.financial", "Financial", "bounded", SELECTOR_PARAMS[:1], "0.1", None)), "qveris_inspect"),
            ("expected_cost_mismatch", freeze, FrozenContractSearch(SELECTOR_TOOL, SearchTool("tool.financial", "Financial", "bounded", SELECTOR_PARAMS, "0.2", None)), "qveris_inspect"),
            ("billing_rule_digest_mismatch", tool_freeze(tool=SearchTool("tool.financial", "Financial", "bounded", SELECTOR_PARAMS, "0.1", raw_billing), data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026, result_selector=SELECTOR), FrozenContractSearch(SELECTOR_TOOL, SearchTool("tool.financial", "Financial", "bounded", SELECTOR_PARAMS, "0.1", {"private": "raw-provider-secret-b"})), "qveris_inspect"),
            ("result_selector_digest_mismatch", ToolFreeze(freeze.tool_id, freeze.data_type, freeze.entity_symbol, freeze.statement_type, freeze.fiscal_year, freeze.parameter_schema_digest, freeze.expected_cost, freeze.billing_rule_digest, "0" * 64), FrozenContractSearch(SELECTOR_TOOL, SELECTOR_TOOL), "qveris_inspect"),
            ("parameter_binding_mismatch", tool_freeze(tool=symbol_only, data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026, result_selector=SELECTOR), FrozenContractSearch(symbol_only, symbol_only), "qveris_inspect"),
            ("inspect_tool_mismatch", freeze, FrozenContractSearch(SELECTOR_TOOL, SearchTool("other-tool", "Financial", "bounded", SELECTOR_PARAMS, "0.1", None)), "qveris_inspect"),
        )
        for code, frozen_tool, search, stage in mismatches:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                value = manifest(); value["variants"] = [value["variants"][1]]
                gateway = Gateway()
                client = RunnerGatewayAgent(variant=value["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=frozen_tool, result_selector=SELECTOR, force_inspect=True)
                service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
                events = service.get_events(value["run_id"])
                terminal = next(event for event in events if event["event_type"] == "terminal")
            self.assertEqual((terminal["transport_status"], terminal["stage"], terminal["stage_error_code"], len(gateway.chat_calls)), ("failed", stage, code, 0))
            self.assertNotIn("raw-provider-secret", json.dumps(terminal))
            self.assertTrue(all(event["previous_event_hash"] == (events[index - 1]["event_hash"] if index else None) for index, event in enumerate(events)))

    def test_known_gateway_usage_must_match_hashed_call_and_receipt_tokens(self):
        gateway, web = Gateway(usage=GatewayUsage(1, 2, 3)), Web()
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        client.preflight(request_id="preflight-usage")
        result = client.run(CASE["query"], request_id="attempt-usage", idempotency_key="idem-usage")
        RunService._project_result(result, manifest()["variants"][0], "exploratory_ab")
        result.public_response["meta"]["usage"]["receipt_id"] = "0" * 64
        with self.assertRaises(RunBackendError):
            RunService._project_result(result, manifest()["variants"][0], "exploratory_ab")

    def test_gateway_usage_requires_fixed_issuer_measurement_and_cache_contract(self):
        gateway, web = Gateway(usage=GatewayUsage(1, 2, 3)), Web()
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        client.preflight(request_id="preflight-usage-contract")
        result = client.run(CASE["query"], request_id="attempt-usage-contract", idempotency_key="idem-usage-contract")
        result.public_response["meta"]["usage"]["issuer"] = "other"
        with self.assertRaises(RunBackendError):
            RunService._project_result(result, manifest()["variants"][0], "exploratory_ab")

    def test_execute_over_cap_is_a_terminal_charged_failure_with_safe_receipt(self):
        gateway, search = Gateway(), Search(actual_cost=.2)
        client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE, max_execute_cost=Decimal("0.1"))
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][1]]
            service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
            terminal = next(event for event in service.get_events(value["run_id"]) if event["event_type"] == "terminal")
        receipt = terminal["external_receipts"]["qveris_execute"]
        self.assertEqual((terminal["transport_status"], terminal["error_class"], terminal["external_action_occurred"], len(gateway.chat_calls)), ("failed", "execute_cost_exceeded_after_charge", True, 0))
        self.assertEqual((receipt["actual_cost"], receipt["remaining_credits"], receipt["request_id"]), (.2, 9.9, terminal["attempt_id"]))
        self.assertTrue(all(receipt[field] is None or len(receipt[field]) == 64 for field in ("execution_id_sha256", "execute_call_id_sha256", "result_sha256", "tool_freeze_sha256")))

    def test_schema_failure_after_b_stages_keeps_selector_execute_and_gateway_receipts(self):
        gateway, search = InvalidSchemaGateway(), SelectorSearch(actual_cost=.2)
        client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=SELECTOR_FREEZE, result_selector=SELECTOR)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][1]]
            service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
            events = service.get_events(value["run_id"])
        terminal = next(event for event in events if event["event_type"] == "terminal")
        receipts = {event["stage"]: event for event in events if event["event_type"] == "stage_complete"}
        self.assertEqual((terminal["transport_status"], terminal["stage"], terminal["stage_attempted_count"], terminal["stage_completed_count"]), ("failed", "gateway_completion", 4, 4))
        self.assertEqual((terminal["external_receipts"]["qveris_execute"]["actual_cost"], terminal["external_receipts"]["qveris_execute"]["remaining_credits"]), (.2, 9.9))
        self.assertEqual(terminal["gateway_receipt"]["usage"], {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8})
        self.assertEqual(terminal["gateway_receipt"]["finish_reason"], "stop")
        self.assertEqual(receipts["result_selector"]["stage_receipt"], {"schema_version": "fs049-result-selector-receipt/v1", "selector_sha256": _digest(SELECTOR), "result_sha256": terminal["external_receipts"]["qveris_execute"]["result_sha256"], "entity": "NVDA", "fiscal_year": 2026, "date": "2026-01-25", "unique_match": True, "validation_status": "passed", "failure_code": None})
        self.assertEqual(terminal["receipt_hashes"], {stage: event["receipt_sha256"] for stage, event in receipts.items()})
        self.assertEqual(terminal["receipt_coverage"]["qveris_execute"]["actual_cost"], "reported")
        self.assertEqual(terminal["receipt_coverage"]["gateway_completion"], {"billing": "reported", "usage": "reported"})
        rendered = json.dumps(events)
        self.assertNotIn("raw-model-content-must-not-persist", rendered)
        self.assertNotIn("search-1", rendered)
        self.assertNotIn("execute-1", rendered)
        self.assertTrue(all(event["previous_event_hash"] == (events[index - 1]["event_hash"] if index else None) for index, event in enumerate(events)))

    def test_length_finish_reason_keeps_gateway_receipt_without_parsing_partial_output(self):
        gateway, web = LengthGateway(), Web()
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][0]]
            service = RunService(RunStore(directory), {"web-model": client}); service.create_run(value); service.execute(value["run_id"])
            events = service.get_events(value["run_id"])
        terminal = next(event for event in events if event["event_type"] == "terminal")
        receipt = next(event["stage_receipt"] for event in events if event["event_type"] == "stage_complete" and event["stage"] == "gateway_completion")
        self.assertEqual((terminal["transport_status"], terminal["error_class"], terminal["stage"], terminal["stage_completed_count"]), ("failed", "output_truncated", "gateway_completion", 2))
        self.assertEqual((receipt["finish_reason"], terminal["gateway_receipt"]["finish_reason"]), ("length", "length"))
        self.assertNotIn("public_response", terminal)
        self.assertNotIn('"truncated"', json.dumps(events))

    def test_gateway_http_parse_failure_persists_only_diagnostic_and_never_completes_stage(self):
        gateway, web = InvalidJsonGateway(), Web()
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][0]]
            service = RunService(RunStore(directory), {"web-model": client}); service.create_run(value); service.execute(value["run_id"])
            events = service.get_events(value["run_id"])
        terminal = next(event for event in events if event["event_type"] == "terminal")
        stages = [event for event in events if event["event_type"] == "stage_complete"]
        self.assertEqual((terminal["transport_status"], terminal["error_class"], terminal["stage"], terminal["stage_attempted_count"], terminal["stage_completed_count"], terminal["usage"]), ("failed", "invalid_json", "gateway_completion", 2, 1, "unknown"))
        self.assertEqual(terminal["gateway_diagnostic_receipt"]["body_state"], "invalid_json")
        self.assertNotIn("gateway_receipt", terminal)
        self.assertEqual([event["stage"] for event in stages], ["web_search"])
        rendered = json.dumps(events)
        self.assertNotIn("raw-model-body-must-not-persist", rendered)
        self.assertNotIn("billing", json.dumps(terminal["gateway_diagnostic_receipt"]))

    def test_selector_failure_is_durably_recorded_after_execute_without_gateway_call(self):
        gateway, search = Gateway(), Search()
        client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=SELECTOR_FREEZE, result_selector=SELECTOR)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][1]]
            service = RunService(RunStore(directory), {"qveris-model": client}); service.create_run(value); service.execute(value["run_id"])
            events = service.get_events(value["run_id"])
        terminal = next(event for event in events if event["event_type"] == "terminal")
        selector = next(event["stage_receipt"] for event in events if event["event_type"] == "stage_complete" and event["stage"] == "result_selector")
        self.assertEqual((terminal["transport_status"], terminal["error_class"], terminal["stage"], terminal["stage_attempted_count"], terminal["stage_completed_count"], len(gateway.chat_calls)), ("failed", "result_selector_failed_after_execute", "result_selector", 3, 3, 0))
        self.assertEqual((selector["entity"], selector["fiscal_year"], selector["date"], selector["unique_match"], selector["validation_status"], selector["failure_code"]), ("NVDA", 2026, "2026-01-25", False, "failed", "unique_match_required"))
        self.assertEqual(terminal["receipt_hashes"]["result_selector"], _digest(selector))
        self.assertTrue(all(event["previous_event_hash"] == (events[index - 1]["event_hash"] if index else None) for index, event in enumerate(events)))

    def test_runner_rejects_model_max_tokens_above_hard_cap(self):
        with self.assertRaisesRegex(ValueError, "8192"):
            RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=Gateway(), web_search=Web(), max_tokens=8193)

    def test_execute_receipt_must_bind_the_runner_idempotency_key(self):
        gateway, search = Gateway(), Search()
        client = RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE)
        client.preflight(request_id="preflight-idempotency")
        result = client.run(CASE["query"], request_id="attempt-idempotency", idempotency_key="idem-idempotency")
        with self.assertRaises(RunBackendError):
            RunService._project_result(result, manifest()["variants"][1], "exploratory_ab", idempotency_key="other-idempotency")

    def test_official_profile_and_scorer_reject_exploratory_path(self):
        value = manifest(); value["mode"] = "official"
        with self.assertRaises(RunBackendError):
            RunStore(tempfile.mkdtemp()).create(value)
        gateway, web = Gateway(), Web(); client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][0]]; store = RunStore(directory); service = RunService(store, {"web-model": client}); service.create_run(value); service.execute(value["run_id"])
            with self.assertRaisesRegex(BenchmarkScoreError, "exploratory A/B"):
                BenchmarkScorer(store, policy={}, oracle_bundle={}, approved_policy_digests=set(), approved_oracle_bundle_digests=set()).score(value["run_id"])

    def test_stage_ledger_is_durable_hashed_and_never_contains_raw_external_inputs(self):
        gateway, web = Gateway(), Web()
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web)
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][0]]
            service = RunService(RunStore(directory), {"web-model": client}); service.create_run(value); service.execute(value["run_id"])
            events = service.get_events(value["run_id"])
        stages = [event for event in events if event["event_type"].startswith("stage_")]
        self.assertEqual([(event["event_type"], event["stage"], event["ordinal"]) for event in stages], [("stage_intent", "web_search", 1), ("stage_complete", "web_search", 1), ("stage_intent", "gateway_completion", 2), ("stage_complete", "gateway_completion", 2)])
        self.assertTrue(all(event["previous_event_hash"] == (events[index - 1]["event_hash"] if index else None) for index, event in enumerate(events)))
        rendered = json.dumps(stages, ensure_ascii=False)
        self.assertNotIn(CASE["query"], rendered)
        self.assertNotIn("idempotency_key\"", rendered)
        self.assertNotIn("parameters\"", rendered)
        self.assertNotIn("search_id\"", rendered)

    def test_each_external_stage_failure_has_a_terminal_safe_stage_ledger(self):
        cases = []
        bad_web = type("BadWeb", (), {"search": lambda self, **_kwargs: (_ for _ in ()).throw(RuntimeError("web down"))})()
        cases.append(("web_search", Gateway(), RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=Gateway(), web_search=bad_web), "web-model", 1, 0, False))
        for stage, attempted, completed in (("qveris_search", 1, 0), ("qveris_inspect", 2, 1), ("qveris_execute", 2, 1)):
            gateway = Gateway()
            search = StageFailureSearch(stage)
            cases.append((stage, gateway, RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=gateway, qveris_search=search, tool_freeze=FREEZE), "qveris-model", attempted, completed, stage == "qveris_execute"))
        gateway, web = FailingGateway(), Web()
        cases.append(("gateway_completion", gateway, RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=gateway, web_search=web), "web-model", 2, 1, False))
        for stage, _gateway, client, variant_id, attempted, completed, ambiguous_execute in cases:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                value = manifest(); value["variants"] = [next(item for item in value["variants"] if item["variant_id"] == variant_id)]
                service = RunService(RunStore(directory), {variant_id: client}); service.create_run(value); service.execute(value["run_id"])
                terminal = next(event for event in service.get_events(value["run_id"]) if event["event_type"] == "terminal")
            self.assertEqual((terminal["transport_status"], terminal["stage"], terminal["stage_attempted_count"], terminal["stage_completed_count"]), ("failed", stage, attempted, completed))
            self.assertRegex(terminal["stage_exception_class"], r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
            self.assertRegex(terminal["stage_error_code"], r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
            self.assertEqual(terminal.get("external_action_may_have_occurred", False), ambiguous_execute)
            if ambiguous_execute:
                self.assertEqual(terminal["external_cost"], "unknown")

    def test_gateway_timeout_preserves_safe_code_without_raw_message(self):
        client = RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=TimeoutGateway(), web_search=Web())
        with tempfile.TemporaryDirectory() as directory:
            value = manifest(); value["variants"] = [value["variants"][0]]
            service = RunService(RunStore(directory), {"web-model": client}); service.create_run(value); service.execute(value["run_id"])
            terminal = next(event for event in service.get_events(value["run_id"]) if event["event_type"] == "terminal")
        self.assertEqual((terminal["error_class"], terminal["stage_error_code"], terminal["stage"]), ("timeout", "timeout", "gateway_completion"))
        self.assertEqual((terminal["external_action_may_have_occurred"], terminal["external_cost"]), (True, "unknown"))
        self.assertNotIn("raw-provider-secret", json.dumps(terminal))

    def test_outer_timeout_after_execute_or_gateway_intent_marks_cost_unknown(self):
        cases = (
            ("qveris_execute", "qveris-model", RunnerGatewayAgent(variant=manifest()["variants"][1], case=CASE, metadata=METADATA, gateway=Gateway(), qveris_search=OuterTimeoutSearch(), tool_freeze=FREEZE)),
            ("gateway_completion", "web-model", RunnerGatewayAgent(variant=manifest()["variants"][0], case=CASE, metadata=METADATA, gateway=OuterTimeoutGateway(), web_search=Web())),
        )
        for stage, variant_id, client in cases:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                value = manifest(); value["timeout_ms"] = 5; value["variants"] = [next(item for item in value["variants"] if item["variant_id"] == variant_id)]
                service = RunService(RunStore(directory), {variant_id: client}); service.create_run(value); service.execute(value["run_id"])
                terminal = next(event for event in service.get_events(value["run_id"]) if event["event_type"] == "terminal")
            self.assertEqual((terminal["transport_status"], terminal["stage"], terminal["external_action_may_have_occurred"], terminal["external_cost"]), ("failed", stage, True, "unknown"))


if __name__ == "__main__":
    unittest.main()

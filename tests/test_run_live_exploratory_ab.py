import importlib.util
import io
import json
import os
import pathlib
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("live_exploratory_ab", ROOT / "scripts" / "run_live_exploratory_ab.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

from qveris_benchmark.model_gateway import GatewayBilling, GatewayChatCompletion, GatewayModel
from qveris_benchmark.qveris_search import SearchCatalog, SearchTool, ToolExecution, ToolInspection
from qveris_benchmark.runner_gateway_agent import WebSearchResult, WebSource
from qveris_benchmark.web_search import TavilyReceipt


PARAMS = (
    {"name": "symbol", "type": "string", "required": True, "description": "ticker"},
    {"name": "period", "type": "string", "required": True, "description": "period", "enum": ["FY"]},
    {"name": "limit", "type": "integer", "required": True, "description": "row limit", "minimum": 1, "maximum": 5},
)
SELECTOR = {"schema_version": "fs049-result-selector/v1", "parameters": {"symbol": "NVDA", "period": "FY", "limit": 5}, "target_symbol": "NVDA", "target_period": "FY", "target_fiscal_year": 2026, "target_date": "2026-01-25", "statement_type": "income_statement", "consolidated": True}


def freeze_file(directory: pathlib.Path) -> pathlib.Path:
    issued_at = MODULE._format_utc(MODULE._utc_now())
    expires_at = MODULE._format_utc(MODULE._parse_utc(issued_at, "issued_at") + timedelta(minutes=15))
    value = {
        "schema_version": MODULE.FREEZE_SCHEMA_VERSION, "tool_id": "tool.financial",
        "data_type": "financial_statement", "entity_symbol": "NVDA", "statement_type": "income_statement", "fiscal_year": 2026,
        "parameter_schema_digest": MODULE._digest(list(PARAMS)), "expected_cost": "0.1", "billing_rule_digest": MODULE._digest(None),
        "result_selector": SELECTOR, "result_selector_digest": MODULE._digest(SELECTOR),
        "absolute_execute_cost_cap": str(MODULE.ABSOLUTE_EXECUTE_COST_CAP), "absolute_execute_cost_cap_digest": MODULE.ABSOLUTE_EXECUTE_COST_CAP_DIGEST,
        "issued_at": issued_at, "expires_at": expires_at,
    }
    path = directory / "freeze.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class Gateway:
    def __init__(self, calls): self.calls, self.requests = calls, []
    def list_models(self, *, request_id):
        self.calls.append("models")
        return (GatewayModel(MODULE.MODEL_ID),)
    def chat_completions(self, **kwargs):
        self.calls.append("chat")
        self.requests.append(kwargs)
        schema = json.loads(kwargs["messages"][1]["content"])["output_schema"]
        output = {"schema_version": "exploratory-financial-answer/v3", "status": "success", "values": {assertion_id: 1 for assertion_id in schema["assertion_ids_exactly_once"]}}
        index = self.calls.count("chat")
        return GatewayChatCompletion(200, MODULE.MODEL_ID, kwargs["request_id"], "call-%d" % index, None, json.dumps(output), "stop", None, GatewayBilling("call-%d" % index, 0.0, 0.0, False))


class Web:
    def __init__(self, calls):
        self.calls = calls
        self.last_receipt = TavilyReceipt("tavily-request-1", 1.0)
    def search(self, **kwargs):
        self.calls.append("web")
        return WebSearchResult(kwargs["query"], "2026-09-04T00:00:00Z", (WebSource("https://example.test/nvda", "source", "bounded"),))


class QVeris:
    def __init__(self, calls, execution_cost=.1, result=None): self.calls, self.execution_cost, self.result = calls, execution_cost, result
    def search(self, **kwargs):
        self.calls.append("search")
        return SearchCatalog("search-1", (SearchTool("tool.financial", "Financial", "bounded", PARAMS, "0.1", None),), None, None)
    def inspect(self, **kwargs):
        self.calls.append("inspect")
        return ToolInspection(SearchTool("tool.financial", "Financial", "bounded", PARAMS, "0.1", None), None, None)
    def execute(self, **kwargs):
        self.calls.append("execute")
        result = self.result if self.result is not None else [{"symbol": "NVDA", "period": "FY", "fiscalYear": "2025", "date": "2025-01-26"}, {"symbol": "NVDA", "period": "FY", "fiscalYear": "2026", "date": "2026-01-25"}]
        return ToolExecution(kwargs["tool_id"], "execution-1", self.execution_cost, None, result, None)


class NoPeriodLimitQVeris(QVeris):
    def inspect(self, **kwargs):
        self.calls.append("inspect")
        return ToolInspection(SearchTool("tool.financial", "Financial", "bounded", PARAMS[:1], "0.1", None), None, None)


class DriftingInspectQVeris(QVeris):
    def inspect(self, **kwargs):
        self.calls.append("inspect")
        return ToolInspection(SearchTool("tool.financial", "Financial", "bounded", PARAMS, "0.2", None), None, None)


class LiveExploratoryABTests(unittest.TestCase):
    def test_preflight_without_freeze_is_a_local_needs_tool_freeze_report(self):
        constructed = []
        with patch.dict(os.environ, {}, clear=True), patch.object(MODULE, "ModelGatewayClient", lambda **kwargs: constructed.append("gateway")), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: constructed.append("qveris")), patch.object(MODULE, "TavilyWebSearchClient", lambda **kwargs: constructed.append("tavily")), redirect_stdout(io.StringIO()) as captured:
            status = MODULE.main(["--preflight", "--max-execute-cost", "1"])
        value = json.loads(captured.getvalue())
        self.assertEqual((status, constructed, value["status"]), (0, [], "needs_tool_freeze"))
        self.assertEqual((value["static_checks"]["variants"]["qveris-api-plus-model"], value["static_checks"]["variants"]["public-web-plus-model"]), ("not_ready", "not_ready"))

    def test_preflight_with_freeze_is_ready_without_a_tavily_key(self):
        constructed = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True), patch.object(MODULE, "ModelGatewayClient", lambda **kwargs: constructed.append("gateway")), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: constructed.append("qveris")), redirect_stdout(io.StringIO()) as captured:
            status = MODULE.main(["--preflight", "--tool-freeze", str(freeze_file(pathlib.Path(directory))), "--max-execute-cost", "1"])
        value = json.loads(captured.getvalue())
        self.assertEqual((status, constructed, value["status"], value["static_checks"]["variants"]["public-web-plus-model"]), (0, [], "ready", "not_ready"))

    def test_missing_config_fails_before_any_client_is_constructed(self):
        constructed = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(MODULE, "ModelGatewayClient", lambda: constructed.append("gateway")):
            status = MODULE.main(["--preflight", "--tool-freeze", str(pathlib.Path(directory) / "missing.json"), "--max-execute-cost", "1"])
        self.assertEqual((status, constructed), (2, []))

    def test_preflight_is_local_only_and_run_uses_serial_runner_agents(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key", "TAVILY_API_KEY": "t-key"}, clear=True):
            root, freeze = pathlib.Path(directory), freeze_file(pathlib.Path(directory))
            output = root / "new-output"
            qveris_factory = Mock(side_effect=lambda **kwargs: QVeris(calls))
            gateway_factory = Mock(side_effect=lambda **kwargs: Gateway(calls))
            freeze_value = json.loads(freeze.read_text())
            with patch.object(MODULE, "ModelGatewayClient", gateway_factory), patch.object(MODULE, "TavilyWebSearchClient", lambda: Web(calls)), patch.object(MODULE, "QVerisSearchClient", qveris_factory):
                self.assertEqual(MODULE.main(["--preflight", "--tool-freeze", str(freeze), "--max-execute-cost", "1"]), 0)
                self.assertEqual(calls, [])
                self.assertEqual(MODULE.main(["--run", "--tool-freeze", str(freeze), "--max-execute-cost", "1", "--output-dir", str(output)]), 0)
            qveris_factory.assert_called_once_with(timeout_seconds=MODULE.QVERIS_TRANSPORT_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file="/etc/ssl/cert.pem")
            gateway_factory.assert_called_once_with(timeout_seconds=MODULE.LIVE_GATEWAY_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file="/etc/ssl/cert.pem")
            manifest = json.loads((output / MODULE.RUN_ID / "manifest.json").read_text())
            events = (output / MODULE.RUN_ID / "events.jsonl").read_text()
        self.assertEqual(calls, ["models", "web", "chat", "search", "inspect", "execute", "chat"])
        self.assertEqual((manifest["mode"], manifest["execution_profile"], manifest["concurrency"], len(manifest["cases"]), len(manifest["variants"])), ("diagnostic", "exploratory_ab", 1, 1, 2))
        self.assertEqual(manifest["timeout_ms"], (3 * MODULE.QVERIS_TRANSPORT_TIMEOUT_SECONDS + MODULE.LIVE_GATEWAY_TIMEOUT_SECONDS + MODULE.EXPLORATORY_TIMEOUT_MARGIN_SECONDS) * 1000)
        self.assertEqual(MODULE.MODEL_CONFIG["max_tokens"], 8192)
        self.assertEqual(manifest["variants"][0]["model_config_digest"], MODULE._digest({"model_id": MODULE.MODEL_ID, "temperature": 0.0, "max_tokens": 8192, "response_format": "json_object"}))
        self.assertEqual((manifest["policy"]["absolute_execute_cost_cap_digest"], manifest["freeze_digest"]), (MODULE.ABSOLUTE_EXECUTE_COST_CAP_DIGEST, MODULE._digest({
            "case_id": MODULE.CASE_ID, "canonical_request": manifest["cases"][0]["canonical_request"],
            "tool_freeze": {"tool_id": "tool.financial", "data_type": "financial_statement", "entity_symbol": "NVDA", "statement_type": "income_statement", "fiscal_year": 2026, "parameter_schema_digest": MODULE._digest(list(PARAMS)), "expected_cost": "0.1", "billing_rule_digest": MODULE._digest(None), "result_selector_digest": MODULE._digest(SELECTOR)},
            "result_selector": SELECTOR, "output_contract": {name: manifest["variants"][0][name] for name in ("prompt_contract_digest", "output_schema_digest", "metadata_digest")}, "issued_at": freeze_value["issued_at"], "expires_at": freeze_value["expires_at"], "max_execute_cost": "1", "absolute_execute_cost_cap": "25", "absolute_execute_cost_cap_digest": MODULE.ABSOLUTE_EXECUTE_COST_CAP_DIGEST, "model_config": MODULE.MODEL_CONFIG,
        })))
        self.assertEqual(set(manifest["variants"][0]) & {"prompt_contract_digest", "output_schema_digest", "metadata_digest"}, {"prompt_contract_digest", "output_schema_digest", "metadata_digest"})
        self.assertNotIn("scoring_contract", manifest)
        self.assertNotIn("score-events", events)

    def test_discovery_calls_only_search_then_inspect_and_writes_no_search_id(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            output = pathlib.Path(directory) / "freeze.json"
            qveris_factory = Mock(side_effect=lambda **kwargs: QVeris(calls))
            with patch.object(MODULE, "QVerisSearchClient", qveris_factory), patch.object(MODULE, "ModelGatewayClient", lambda: (_ for _ in ()).throw(AssertionError("Gateway must not be constructed"))):
                status = MODULE.main(["--discover-b-freeze", "--tool-id", "tool.financial", "--max-execute-cost", "1", "--freeze-output", str(output)])
            qveris_factory.assert_called_once_with(use_environment_proxy=False, ca_file="/etc/ssl/cert.pem")
            value = json.loads(output.read_text())
            mode = stat.S_IMODE(output.stat().st_mode)
        self.assertEqual((status, calls), (0, ["search", "inspect"]))
        self.assertEqual((value["tool_id"], value["expected_cost"]), ("tool.financial", "0.1"))
        self.assertEqual((value["absolute_execute_cost_cap"], value["absolute_execute_cost_cap_digest"]), ("25", MODULE.ABSOLUTE_EXECUTE_COST_CAP_DIGEST))
        self.assertLess(value["issued_at"], value["expires_at"])
        self.assertEqual(mode, 0o600)
        self.assertNotIn("search-1", json.dumps(value))

    def test_discovery_accepts_structured_inspect_types_and_null_optional_fields(self):
        calls = []
        structured = (
            {"name": "symbol", "type": "string", "required": False, "description": ""},
            {"name": "period", "type": "string", "required": False, "description": "", "enum": ["FY"]},
            {"name": "limit", "type": "number", "required": False, "description": ""},
        )
        class StructuredInspectQVeris(QVeris):
            def inspect(self, **kwargs):
                self.calls.append("inspect")
                return ToolInspection(SearchTool("tool.financial", "Financial", "bounded", structured, "0.1", None), None, None)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            output = pathlib.Path(directory) / "freeze.json"
            with patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: StructuredInspectQVeris(calls)):
                self.assertEqual(MODULE.main(["--discover-b-freeze", "--tool-id", "tool.financial", "--max-execute-cost", "1", "--freeze-output", str(output)]), 0)
        self.assertEqual(calls, ["search", "inspect"])

    def test_discovery_binding_rejects_unknown_or_conflicting_params(self):
        with self.assertRaises(ValueError):
            MODULE.bind_fs049_parameters(PARAMS + ({"name": "date", "type": "string", "required": False, "description": ""},))
        with self.assertRaises(ValueError):
            MODULE.bind_fs049_parameters(PARAMS[:2] + (PARAMS[0],))

    def test_discovery_refuses_a_tool_without_a_period_limit_binding(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            output = pathlib.Path(directory) / "freeze.json"
            with patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: NoPeriodLimitQVeris(calls)):
                self.assertEqual(MODULE.main(["--discover-b-freeze", "--tool-id", "tool.financial", "--max-execute-cost", "1", "--freeze-output", str(output)]), 2)
            self.assertFalse(output.exists())
        self.assertEqual(calls, ["search", "inspect"])

    def test_run_b_needs_no_tavily_and_is_one_serial_qveris_runner_cell(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            root, freeze = pathlib.Path(directory), freeze_file(pathlib.Path(directory))
            output = root / "new-output"
            qveris_factory = Mock(side_effect=lambda **kwargs: QVeris(calls))
            gateway_factory = Mock(side_effect=lambda **kwargs: Gateway(calls))
            with patch.object(MODULE, "ModelGatewayClient", gateway_factory), patch.object(MODULE, "QVerisSearchClient", qveris_factory), patch.object(MODULE, "TavilyWebSearchClient", lambda: (_ for _ in ()).throw(AssertionError("Tavily must not be constructed for --run-b"))):
                self.assertEqual(MODULE.main(["--run-b", "--tool-freeze", str(freeze), "--max-execute-cost", "1", "--output-dir", str(output)]), 0)
            qveris_factory.assert_called_once_with(timeout_seconds=MODULE.QVERIS_TRANSPORT_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file="/etc/ssl/cert.pem")
            gateway_factory.assert_called_once_with(timeout_seconds=MODULE.LIVE_GATEWAY_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file="/etc/ssl/cert.pem")
            manifest = json.loads((output / MODULE.RUN_B_ID / "manifest.json").read_text())
        self.assertEqual(calls, ["models", "search", "inspect", "execute", "chat"])
        self.assertEqual((manifest["run_id"], len(manifest["variants"]), manifest["execution_profile"]), (MODULE.RUN_B_ID, 1, "exploratory_ab"))
        self.assertEqual(manifest["timeout_ms"], (3 * MODULE.QVERIS_TRANSPORT_TIMEOUT_SECONDS + MODULE.LIVE_GATEWAY_TIMEOUT_SECONDS + MODULE.EXPLORATORY_TIMEOUT_MARGIN_SECONDS) * 1000)

    def test_gateway_only_probe_uses_one_gateway_catalog_and_completion_with_zero_qveris(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            output = pathlib.Path(directory) / "new-output"
            gateway = Gateway(calls)
            qveris_factory = Mock(side_effect=AssertionError("QVeris must not be constructed by the Gateway-only probe"))
            with patch.object(MODULE, "ModelGatewayClient", Mock(return_value=gateway)), patch.object(MODULE, "QVerisSearchClient", qveris_factory), patch.object(MODULE, "TavilyWebSearchClient", lambda: (_ for _ in ()).throw(AssertionError("web client must not be constructed by the Gateway-only probe"))), redirect_stdout(io.StringIO()) as captured:
                status = MODULE.main(["--probe-gateway-only", "--output-dir", str(output)])
            payload = json.loads(captured.getvalue())
            manifest = json.loads((output / MODULE.RUN_GATEWAY_PROBE_ID / "manifest.json").read_text())
            events = [json.loads(line) for line in (output / MODULE.RUN_GATEWAY_PROBE_ID / "events.jsonl").read_text().splitlines()]
        qveris_factory.assert_not_called()
        self.assertEqual((status, calls), (0, ["models", "chat"]))
        self.assertEqual((payload["classification"], payload["scoring_status"], payload["projection_status"]), ("diagnostic_nonranking", "UNSCORED", "UNSCORED"))
        self.assertEqual((manifest["execution_profile"], manifest["timeout_ms"], len(manifest["variants"]), manifest["concurrency"]), ("exploratory_gateway_probe", (MODULE.LIVE_GATEWAY_TIMEOUT_SECONDS + MODULE.EXPLORATORY_TIMEOUT_MARGIN_SECONDS) * 1000, 1, 1))
        user_envelope = json.loads(gateway.requests[0]["messages"][1]["content"])
        synthetic = json.loads(gateway.requests[0]["messages"][2]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual((gateway.requests[0]["model_id"], gateway.requests[0]["max_tokens"], gateway.requests[0]["response_format"]), (MODULE.MODEL_ID, MODULE.MODEL_MAX_TOKENS, "json_object"))
        self.assertEqual([item["assertion_id"] for item in synthetic["synthetic_probe_result"]["fields"]], user_envelope["output_schema"]["assertion_ids_exactly_once"])
        self.assertEqual((len(user_envelope["requested_values"]), len(synthetic["synthetic_probe_result"]["fields"]), {item["value"] for item in synthetic["synthetic_probe_result"]["fields"]}), (18, 18, {None}))
        terminal = next(event for event in events if event["event_type"] == "terminal")
        self.assertEqual((terminal["execution_evidence"]["agent_invocations"], terminal["execution_evidence"]["tool_executions"], terminal["execution_evidence"]["tools_used"], terminal["external_receipts"], terminal.get("external_action_occurred")), (1, 0, [], {}, None))

    def test_run_b_actual_cost_over_cap_fails_closed_before_gateway_completion(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            root, freeze = pathlib.Path(directory), freeze_file(pathlib.Path(directory))
            output = root / "new-output"
            with patch.object(MODULE, "ModelGatewayClient", lambda **kwargs: Gateway(calls)), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: QVeris(calls, execution_cost=2.0)):
                self.assertEqual(MODULE.main(["--run-b", "--tool-freeze", str(freeze), "--max-execute-cost", "1", "--output-dir", str(output)]), 1)
        self.assertEqual(calls, ["models", "search", "inspect", "execute"])

    def test_run_b_nonmatching_multi_period_result_never_calls_gateway(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            root, freeze = pathlib.Path(directory), freeze_file(pathlib.Path(directory))
            output = root / "new-output"
            wrong = [{"symbol": "NVDA", "period": "FY", "fiscalYear": "2025", "date": "2025-01-26"}]
            with patch.object(MODULE, "ModelGatewayClient", lambda **kwargs: Gateway(calls)), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: QVeris(calls, result=wrong)):
                self.assertEqual(MODULE.main(["--run-b", "--tool-freeze", str(freeze), "--max-execute-cost", "1", "--output-dir", str(output)]), 1)
        self.assertEqual(calls, ["models", "search", "inspect", "execute"])

    def test_run_b_rechecks_the_inspect_contract_before_execute(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            root, freeze = pathlib.Path(directory), freeze_file(pathlib.Path(directory))
            output = root / "new-output"
            with patch.object(MODULE, "ModelGatewayClient", lambda **kwargs: Gateway(calls)), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: DriftingInspectQVeris(calls)):
                self.assertEqual(MODULE.main(["--run-b", "--tool-freeze", str(freeze), "--max-execute-cost", "1", "--output-dir", str(output)]), 1)
        self.assertEqual(calls, ["models", "search", "inspect"])

    def test_run_b_request_bound_entity_without_response_symbol_never_calls_gateway(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            root, freeze = pathlib.Path(directory), freeze_file(pathlib.Path(directory))
            output = root / "new-output"
            response_without_symbol = [{"period": "FY", "fiscalYear": "2026", "date": "2026-01-25"}]
            with patch.object(MODULE, "ModelGatewayClient", lambda **kwargs: Gateway(calls)), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: QVeris(calls, result=response_without_symbol)):
                self.assertEqual(MODULE.main(["--run-b", "--tool-freeze", str(freeze), "--max-execute-cost", "1", "--output-dir", str(output)]), 1)
            events = [json.loads(line) for line in (output / MODULE.RUN_B_ID / "events.jsonl").read_text().splitlines()]
        terminal = next(event for event in events if event["event_type"] == "terminal")
        self.assertEqual((calls, terminal["external_receipts"]["qveris_execute"]["entity_binding"]), (["models", "search", "inspect", "execute"], "request_parameter"))

    def test_cost_cap_above_25_fails_before_client_construction(self):
        constructed = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            freeze = freeze_file(pathlib.Path(directory))
            with patch.object(MODULE, "ModelGatewayClient", lambda: constructed.append("gateway")), patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: constructed.append("qveris")):
                status = MODULE.main(["--run-b", "--tool-freeze", str(freeze), "--max-execute-cost", "25.1"])
        self.assertEqual((status, constructed), (2, []))

    def test_expired_or_future_freeze_fails_before_client_construction(self):
        fixed_now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = freeze_file(pathlib.Path(directory))
            value = json.loads(path.read_text())
            value.update({"issued_at": "2026-09-04T05:00:00Z", "expires_at": "2026-09-04T06:00:00Z"})
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PreflightError, "has expired"):
                MODULE._read_freeze(str(path), clock=lambda: fixed_now)
            value.update({"issued_at": "2026-09-04T06:01:00Z", "expires_at": "2026-09-04T06:16:00Z"})
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PreflightError, "in the future"):
                MODULE._read_freeze(str(path), clock=lambda: fixed_now)

    def test_discovery_binds_clock_window_to_new_freeze(self):
        calls = []
        fixed_now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QVERIS_API_KEY": "q-key"}, clear=True):
            output = pathlib.Path(directory) / "freeze.json"
            with patch.object(MODULE, "QVerisSearchClient", lambda **kwargs: QVeris(calls)):
                MODULE.discover_b_freeze("tool.financial", "1", str(output), clock=lambda: fixed_now)
            value = json.loads(output.read_text())
        self.assertEqual((value["issued_at"], value["expires_at"]), ("2026-09-04T06:00:00Z", "2026-09-04T06:15:00Z"))


if __name__ == "__main__":
    unittest.main()

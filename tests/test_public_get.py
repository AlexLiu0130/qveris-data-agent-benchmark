import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.public_get import PublicGetAdapter
from qveris_benchmark.qveris_model_gateway import SemanticResolution
from qveris_benchmark.qveris_model_gateway import SemanticGatewayError
from qveris_benchmark.qveris_tool_gateway import ToolGatewayError
from qveris_benchmark.run_backend import RunService
from qveris_benchmark.response_contract import validate_response


IDENTITY = {
    "agent_variant_id": "semantic-agent",
    "agent_version": "v1",
    "get_variant_id": "public-get",
    "get_version": "v1",
    "model_identifier": "test-model",
    "model_version": "v1",
    "model_config_digest": "a" * 64,
}


def semantic(request):
    return {"schema_version": "public-get.semantic/v1", "request": request}


def hk_l1_request():
    return {
        "kind": "market_quote",
        "security": {"asset_class": "equity", "venue": "HKEX", "local_code": "00700"},
        "operation": "bid_ask_l1",
    }


def sse_dividend_request():
    return {
        "kind": "historical",
        "security": {"asset_class": "equity", "venue": "SSE", "local_code": "600519"},
        "operation": "corporate_actions",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    }


def hk_l1_raw():
    return {
        "data": {"data": {"rows": [{
            "stockName": "腾讯控股", "stockCode": "00700",
            "tradingTimestamp": "2026-09-04T10:00:00Z", "currency": "HKD",
            "tradeStatus": "NORMAL", "bidGrp": "买一价:1.4HKD,买一挂单量:2手",
            "offerGrp": "卖一价:1.5HKD,卖一挂单量:3手",
            "private_note": "TOP_SECRET_RAW",
        }]}},
    }


def sse_dividend_raw():
    event = {
        "symbol": "600519.SH", "exDate": "2024-01-02", "recordDate": "2024-01-01",
        "plan": "provider plan", "dividendPaidRate": 1.2,
    }
    return {
        "action": "dividends", "code": "0", "msg": "ok",
        "data": [event, {**event, "exDate": "2024-02-02", "recordDate": "2024-02-01", "dividendPaidRate": None}],
    }


def us_quote_request(operation):
    return {
        "kind": "market_quote",
        "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"},
        "operation": operation,
    }


def us_quote_raw():
    return {"Global Quote": {
        "01. symbol": "AAPL", "02. open": "1", "03. high": "2", "04. low": "0.5",
        "05. price": "1.5", "06. volume": "3", "07. latest trading day": "2026-09-04",
        "08. previous close": "1.2", "09. change": "0.3", "10. change percent": "25%",
    }}


class Gateway:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, tool_id, params, *, request_id, idempotency_key):
        self.calls.append((tool_id, params, request_id, idempotency_key))
        return self.response


class PublicGetAdapterTests(unittest.TestCase):
    def adapter(self, resolved, gateway):
        return PublicGetAdapter(lambda _query, **_kwargs: resolved, gateway, **IDENTITY)

    def execute_get(self, adapter):
        return adapter.run("test query", request_id="request-1", idempotency_key="key-1")

    def assert_public_contract(self, result):
        validate_response(result.public_response)

    def test_rejects_model_tool_and_provider_fields_before_dispatch(self):
        for forbidden in ("tool_id", "provider", "provider_params", "parser_id", "route"):
            with self.subTest(forbidden=forbidden):
                request = hk_l1_request()
                request[forbidden] = "model-must-not-control-this"
                gateway = Gateway(hk_l1_raw())
                result = self.execute_get(self.adapter(semantic(request), gateway))
                self.assertEqual(result.public_response["status"], "error")
                self.assertIsNone(result.public_response["data"])
                self.assert_public_contract(result)
                self.assertEqual(gateway.calls, [])
                self.assertEqual(result.execution_evidence.tool_executions, 0)
                self.assertEqual(result.execution_evidence.agent_invocations, 1)
                self.assertEqual(result.execution_evidence.structured_outputs, 1)

    def test_clarification_and_unsupported_never_dispatch(self):
        incomplete = hk_l1_request()
        incomplete["security"]["local_code"] = None
        unsupported = semantic({
            "kind": "historical",
            "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"},
            "operation": "corporate_actions", "start_date": "2024-01-01", "end_date": "2024-12-31",
        })
        for resolved, status in ((semantic(incomplete), "needs_clarification"), (unsupported, "unsupported")):
            with self.subTest(status=status):
                gateway = Gateway(hk_l1_raw())
                result = self.execute_get(self.adapter(resolved, gateway))
                self.assertEqual(result.public_response["status"], status)
                self.assertIsNone(result.public_response["data"])
                self.assert_public_contract(result)
                self.assertEqual(gateway.calls, [])
                self.assertEqual(result.execution_evidence.tool_executions, 0)

    def test_unsupported_keeps_model_usage_but_never_uses_data_gateway_usage(self):
        usage = {"receipt_id": "model-call", "measurement_version": "usage-v1", "cache_status": "not_reported", "request_id": "request-1", "issuer": "qveris_model_gateway", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
        unsupported = semantic({"kind": "historical", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "corporate_actions", "start_date": "2024-01-01", "end_date": "2024-12-31"})
        gateway = Gateway({"raw": {"must": "not dispatch"}, "usage": {"input_tokens": 999}})
        result = self.execute_get(self.adapter(SemanticResolution(unsupported, usage), gateway))
        self.assertEqual(result.public_response["meta"]["usage"], usage)
        self.assertEqual(gateway.calls, [])
        self.assert_public_contract(result)

    def test_cached_or_untrusted_usage_is_marked_unavailable(self):
        usage = {"receipt_id": "model-call", "measurement_version": "usage-v1", "cache_status": "miss", "request_id": "request-1", "issuer": "qveris_model_gateway", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
        for changed in ({"cache_status": "hit"}, {"issuer": "untrusted"}):
            with self.subTest(changed=changed):
                result = self.execute_get(self.adapter(SemanticResolution(semantic(hk_l1_request()), {**usage, **changed}), Gateway(hk_l1_raw())))
                self.assertEqual((result.public_response["meta"]["usage"]["issuer"], result.public_response["meta"]["usage"]["total_tokens"]), ("unavailable", 0))
                self.assert_public_contract(result)

    def test_offline_only_fmp_contract_is_catalogued_but_never_dispatched(self):
        request = {
            "kind": "financial_statement",
            "security": {"asset_class": "equity", "venue": "US", "local_code": "TSLA"},
            "statement": {"type": "income", "presentation": "as_reported", "period": {"kind": "specified_period", "fiscal_year": 2026, "fiscal_period": "Q2"}, "fields": ["revenue"]},
        }
        gateway = Gateway({"unexpected": "must not be read"})
        result = self.execute_get(self.adapter(semantic(request), gateway))
        self.assertEqual((result.public_response["status"], result.public_response["terminal_reason"]), ("unsupported", "route_unsupported"))
        self.assertEqual(gateway.calls, [])
        self.assert_public_contract(result)

    def test_cross_statement_financial_fields_never_dispatch(self):
        request = {
            "kind": "financial_statement",
            "security": {"asset_class": "equity", "venue": "US", "local_code": "TSLA"},
            "statement": {"type": "income", "presentation": "standardized", "period": {"kind": "specified_period", "fiscal_year": 2026, "fiscal_period": "Q2"}, "fields": ["revenue", "total_assets"]},
        }
        gateway = Gateway({"unexpected": "must not be read"})
        result = self.execute_get(self.adapter(semantic(request), gateway))
        self.assertEqual((result.public_response["status"], result.public_response["terminal_reason"]), ("unsupported", "route_unsupported"))
        self.assertEqual(gateway.calls, [])
        self.assert_public_contract(result)

    def test_predispatch_adapter_results_are_runner_compatible(self):
        request = hk_l1_request()
        request["tool_id"] = "model-must-not-control-this"
        result = self.execute_get(self.adapter(semantic(request), Gateway(hk_l1_raw())))
        _, _, evidence = RunService._project_result(result, IDENTITY)
        self.assertEqual((evidence["tool_executions"], evidence["tools_used"]), (0, []))

    def test_hk_l1_is_fixed_routed_parsed_and_hides_raw_payload(self):
        usage = {
            "receipt_id": "receipt-1", "measurement_version": "usage-v1", "cache_status": "miss",
            "request_id": "request-1", "issuer": "qveris_model_gateway", "input_tokens": 2,
            "output_tokens": 3, "total_tokens": 5,
        }
        gateway = Gateway({"raw": hk_l1_raw(), "as_of": "2026-09-04T10:00:00Z", "source": "qveris", "usage": {"input_tokens": 99}})
        result = self.execute_get(self.adapter(SemanticResolution(semantic(hk_l1_request()), usage), gateway))
        self.assertEqual(result.public_response["status"], "success")
        fields = result.public_response["data"]["quote"]["fields"]
        self.assertEqual(result.public_response["data"]["quote"]["instrument"]["symbol"], "00700.HK")
        self.assertEqual((fields["bid"]["value"], fields["ask"]["value"]), ("1.4", "1.5"))
        self.assertEqual(gateway.calls, [("hangseng_polysource.quote.hkshares.live.v2.dec427af", {"stockObject": ["00700.HK"], "pageNo": 1, "pageSize": 1}, "request-1", "key-1")])
        self.assertEqual(result.execution_evidence.tool_executions, 1)
        self.assertEqual(result.execution_evidence.tools_used, ("get",))
        self.assertEqual(result.public_response["meta"]["usage"], usage)
        self.assertEqual(usage["total_tokens"], usage["input_tokens"] + usage["output_tokens"])
        public_json = json.dumps(result.public_response, ensure_ascii=False)
        self.assertNotIn("TOP_SECRET_RAW", public_json)
        self.assertNotIn("provider_payload", public_json)
        self.assertNotIn("raw_response", public_json)
        self.assert_public_contract(result)

    def test_us_quote_and_last_price_have_fixed_alpha_route_and_source(self):
        for operation in ("quote_snapshot", "last_price"):
            with self.subTest(operation=operation):
                gateway = Gateway({"raw": us_quote_raw(), "source": "untrusted gateway source"})
                result = self.execute_get(self.adapter(semantic(us_quote_request(operation)), gateway))
                self.assertEqual(result.public_response["schema_version"], "get-response/v1")
                self.assertEqual(result.public_response["source"], "Alpha Vantage")
                self.assertEqual(result.public_response["as_of"], "2026-09-04")
                self.assertEqual(result.public_response["data"]["quote"]["instrument"]["symbol"], "AAPL")
                self.assertEqual(result.public_response["meta"]["usage"], {"receipt_id": "unavailable", "measurement_version": "not_measured", "cache_status": "unavailable", "request_id": "request-1", "issuer": "unavailable", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                if operation == "last_price":
                    self.assertEqual(result.public_response["data"]["quote"]["fields"]["last_price"]["value"], "1.5")
                self.assertEqual(gateway.calls, [("alphavantage.global_quote.retrieve.v1.9b8a7c6d", {"function": "GLOBAL_QUOTE", "symbol": "AAPL", "entitlement": "realtime"}, "request-1", "key-1")])
                self.assert_public_contract(result)

    def test_sse_dividends_without_contract_proven_source_time_are_not_dispatched(self):
        gateway = Gateway({
            "raw": sse_dividend_raw(), "as_of": "2026-09-04T10:00:00Z", "source": "qveris",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        })
        result = self.execute_get(self.adapter(semantic(sse_dividend_request()), gateway))
        self.assertEqual((result.public_response["status"], result.public_response["terminal_reason"]), ("unsupported", "route_unsupported"))
        self.assertEqual(gateway.calls, [])
        self.assertEqual(result.public_response["meta"]["usage"]["issuer"], "unavailable")
        self.assert_public_contract(result)

    def test_hkex_calendar_without_contract_proven_source_time_is_not_dispatched(self):
        request = {
            "kind": "historical",
            "security": {"asset_class": "equity", "venue": "HKEX", "local_code": "00700"},
            "operation": "trading_calendar", "start_date": "2024-01-02", "end_date": "2024-01-05",
        }
        raw = {"time": ["2024-01-02", "2024-01-03"], "metadata": {"marketcode": "212200", "date_type": "0", "has_results": True}}
        gateway = Gateway({"raw": raw, "as_of": "2024-01-05T00:00:00Z", "source": "qveris"})
        result = self.execute_get(self.adapter(semantic(request), gateway))
        self.assertEqual((result.public_response["status"], result.public_response["terminal_reason"]), ("unsupported", "route_unsupported"))
        self.assertEqual(gateway.calls, [])
        self.assert_public_contract(result)

    def test_stage_errors_use_only_safe_codes_and_private_timing(self):
        gateway = Gateway(hk_l1_raw())
        resolver = lambda _query, **_kwargs: (_ for _ in ()).throw(SemanticGatewayError("http_401"))
        result = self.execute_get(PublicGetAdapter(resolver, gateway, **IDENTITY))
        self.assertEqual(result.public_response["terminal_reason"], "semantic_http_401")
        self.assertEqual(gateway.calls, [])
        self.assertNotIn("semantic_ms", json.dumps(result.public_response))
        self.assertIsNotNone(result.execution_evidence.semantic_ms)
        self.assertEqual(result.execution_evidence.tool_ms, 0.0)
        self.assertGreaterEqual(result.execution_evidence.total_ms, result.execution_evidence.semantic_ms)
        self.assert_public_contract(result)

        def tool(*_args, **_kwargs):
            raise ToolGatewayError("http_429")

        result = self.execute_get(self.adapter(semantic(hk_l1_request()), tool))
        self.assertEqual(result.public_response["terminal_reason"], "tool_http_429")
        self.assertNotIn("http_429", json.dumps(result.public_response).replace("tool_http_429", ""))
        self.assertIsNotNone(result.execution_evidence.tool_ms)
        self.assert_public_contract(result)


if __name__ == "__main__":
    unittest.main()

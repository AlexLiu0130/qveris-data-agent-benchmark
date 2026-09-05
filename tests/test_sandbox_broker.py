import base64
import json
import pathlib
import tempfile
import unittest


import sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.sandbox_broker import SCHEMA_VERSION, SandboxBroker
from qveris_benchmark.sandbox_get_entry import run_stdio
from qveris_benchmark.qveris_model_gateway import MODEL_GATEWAY_MAX_TOKENS


REQUEST_ID = "request-1"


def observations(*, model=0, model_done=0, tool=0, tool_done=0, download=0, download_done=0):
    return {"model_dispatches": model, "model_completions": model_done, "tool_dispatches": tool, "tool_completions": tool_done, "result_download_dispatches": download, "result_download_completions": download_done}


class Response:
    status = 200

    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def frame(kind, url, headers, body=b"{}", timeout_ms=60000, method="POST"):
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "request_id": REQUEST_ID, "method": method, "url": url, "headers": headers, "body_b64": base64.b64encode(body).decode(), "timeout_ms": timeout_ms}


class SandboxBrokerTests(unittest.TestCase):
    def test_host_adds_auth_but_never_echoes_it(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return Response(b'{"choices":[]}', {"X-QVeris-Call-ID": "private-call-id", "Set-Cookie": "secret"})

        body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": MODEL_GATEWAY_MAX_TOKENS, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL quote"}]}).encode()
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_api_key="model-secret", model_opener=opener)
        reply = broker.reply(frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, body))
        self.assertEqual(calls[0][0].get_header("Authorization"), "Bearer model-secret")
        self.assertEqual(reply["headers"], {"X-QVeris-Call-ID": "private-call-id"})
        self.assertNotIn("model-secret", json.dumps(reply))
        self.assertNotIn("secret", json.dumps(reply))
        self.assertEqual(broker.observations(), observations(model=1, model_done=1))

    def test_denied_or_credentialless_request_never_calls_transport(self):
        calls = []
        body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": MODEL_GATEWAY_MAX_TOKENS, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL quote"}]}).encode()
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_opener=lambda *_: calls.append(True))
        denied = frame("model", "https://example.test/steal", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"})
        self.assertEqual(broker.reply(denied)["error"], "route_denied")
        allowed = frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, body)
        self.assertEqual(broker.reply(allowed)["error"], "credential_unavailable")
        self.assertEqual(calls, [])

    def test_exactly_one_model_then_one_approved_tool_can_dispatch(self):
        calls = []
        model_body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": MODEL_GATEWAY_MAX_TOKENS, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL quote"}]}).encode()
        tool_body = json.dumps({"parameters": {"function": "GLOBAL_QUOTE", "symbol": "AAPL", "entitlement": "realtime"}}).encode()
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "market_quote", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "quote_snapshot"}}
        model_reply = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "```json\n" + json.dumps(semantic) + "\n```"}}]}
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=lambda request, timeout: (calls.append(request), Response(json.dumps(model_reply).encode()))[1], tool_opener=lambda request, timeout: (calls.append(request), Response(b"{}"))[1])
        model = frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, model_body)
        tool = frame("tool", "https://qveris.ai/api/v1/tools/execute?tool_id=alphavantage.global_quote.retrieve.v1.9b8a7c6d", {"accept": "application/json", "content-type": "application/json", "idempotency-key": "idem-" + REQUEST_ID, "x-request-id": REQUEST_ID}, tool_body, 15000)
        self.assertEqual(broker.reply(model)["status"], 200)
        self.assertEqual(broker.reply(tool)["status"], 200)
        self.assertEqual(broker.reply(model)["error"], "route_denied")
        self.assertEqual(broker.reply(tool)["error"], "route_denied")
        self.assertEqual(len(calls), 2)
        self.assertEqual(broker.observations(), observations(model=1, model_done=1, tool=1, tool_done=1))

    def test_entry_and_host_broker_run_one_mocked_historical_get_end_to_end(self):
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "historical", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "daily_bars", "adjustment": "unadjusted", "start_date": "2026-08-03", "end_date": "2026-08-03"}}
        model = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}, "qveris_billing": {"call_id": "call-1", "usage_estimated": False}}
        tool = {"success": True, "result": {"data": {"result": {"data": [{"date": "2026-08-03", "open": 1, "high": 3, "low": 1, "close": 2, "volume": 4}]}}}}
        def opener(request, _timeout):
            return Response(json.dumps(model if "aigateway" in request.full_url else tool).encode(), {"X-QVeris-Call-ID": "call-1"} if "aigateway" in request.full_url else {})

        broker = SandboxBroker(REQUEST_ID, query="AAPL daily history", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=opener, tool_opener=opener)

        class Conversation:
            def __init__(self):
                self.pending = [json.dumps({"protocol_version": "sandbox-get-input/v1", "request_id": REQUEST_ID, "query": "AAPL daily history"}) + "\n"]
                self.lines = []

            def readline(self):
                return self.pending.pop(0) if self.pending else ""

            def write(self, value):
                parsed = json.loads(value)
                self.lines.append(parsed)
                if parsed.get("schema_version") == SCHEMA_VERSION:
                    self.pending.append(json.dumps(broker.reply(parsed)) + "\n")
                return len(value)

            def flush(self):
                pass

        stream = output = Conversation()
        descriptor = {"schema_version": "sandbox-get-runtime-config/v1", "model": "public-model", "agent_variant_id": "agent", "agent_version": "v1", "get_variant_id": "get", "get_version": "v1", "model_version": "v1", "model_config_digest": "a" * 64}
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "descriptor.json"; path.write_text(json.dumps(descriptor))
            self.assertEqual(run_stdio(str(path), stream, output), 0)
        lines = output.lines
        self.assertEqual([line["kind"] for line in lines[:-1]], ["model", "tool"])
        self.assertTrue(all("authorization" not in line["headers"] for line in lines[:-1]))
        self.assertEqual((lines[-1]["schema_version"], lines[-1]["status"]), ("get-response/v1", "success"))
        self.assertEqual(lines[-1]["data"]["bars"]["d20260803"]["fields"]["close"]["value"], "2")
        self.assertEqual(broker.observations(), observations(model=1, model_done=1, tool=1, tool_done=1))

    def test_financial_alpha_pointer_download_is_host_bound_once(self):
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "financial_statement", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "statement": {"type": "income", "presentation": "standardized", "period": {"kind": "specified_period", "fiscal_year": 2024, "fiscal_period": "FY"}, "fields": ["revenue"]}}}
        model = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}]}
        pointer_url = "https://oss.qveris.ai/private-result.json?redacted=1"
        pointer = {"status_code": 200, "message": "content is available", "full_content_file_url": pointer_url, "truncated_content": "{}", "content_schema": {}}
        tool = {"success": True, "result": pointer}
        calls = []

        def tool_open(request, _timeout):
            calls.append(request)
            return Response(json.dumps(tool).encode())

        def download_open(request, _timeout):
            calls.append(request)
            return Response(b'{"symbol":"AAPL","annualReports":[{"fiscalDateEnding":"2024-09-28","reportedCurrency":"USD","totalRevenue":"10"}]}')

        broker = SandboxBroker(REQUEST_ID, query="AAPL FY2024 revenue", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=lambda _request, _timeout: Response(json.dumps(model).encode()), tool_opener=tool_open, result_download_opener=download_open)
        model_body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": MODEL_GATEWAY_MAX_TOKENS, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL FY2024 revenue"}]}).encode()
        tool_body = json.dumps({"parameters": {"function": "INCOME_STATEMENT", "symbol": "AAPL"}}).encode()
        self.assertEqual(broker.reply(frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, model_body))["status"], 200)
        self.assertEqual(broker.reply(frame("tool", "https://qveris.ai/api/v1/tools/execute?tool_id=alphavantage.income_statement.retrieve.v1.7aca3c4a", {"accept": "application/json", "content-type": "application/json", "idempotency-key": "idem-" + REQUEST_ID, "x-request-id": REQUEST_ID}, tool_body, 15000))["status"], 200)
        download = frame("result_download", pointer_url, {"accept": "application/json"}, b"", 15000, "GET")
        self.assertEqual(json.loads(base64.b64decode(broker.reply(download)["body_b64"])), {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": "2024-09-28", "reportedCurrency": "USD", "totalRevenue": "10"}]})
        self.assertEqual(broker.reply(download)["error"], "route_denied")
        self.assertEqual(broker.observations(), observations(model=1, model_done=1, tool=1, tool_done=1, download=1, download_done=1))
        self.assertEqual([request.get_method() for request in calls], ["POST", "GET"])

    def test_entry_financial_pointer_uses_one_tool_and_one_host_download(self):
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "financial_statement", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "statement": {"type": "income", "presentation": "standardized", "period": {"kind": "specified_period", "fiscal_year": 2024, "fiscal_period": "FY"}, "fields": ["revenue"]}}}
        model = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}, "qveris_billing": {"call_id": "call-1", "usage_estimated": False}}
        pointer = {"status_code": 200, "message": "content is available", "full_content_file_url": "https://oss.qveris.ai/private-result.json?redacted=1", "truncated_content": "{}", "content_schema": {}}
        tool = {"success": True, "result": pointer}

        def opener(request, _timeout):
            return Response(json.dumps(model if "aigateway" in request.full_url else tool).encode(), {"X-QVeris-Call-ID": "call-1"} if "aigateway" in request.full_url else {})

        broker = SandboxBroker(REQUEST_ID, query="AAPL FY2024 revenue", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=opener, tool_opener=opener, result_download_opener=lambda _request, _timeout: Response(b'{"symbol":"AAPL","annualReports":[{"fiscalDateEnding":"2024-09-28","reportedCurrency":"USD","totalRevenue":"10"}]}'))

        class Conversation:
            def __init__(self):
                self.pending = [json.dumps({"protocol_version": "sandbox-get-input/v1", "request_id": REQUEST_ID, "query": "AAPL FY2024 revenue"}) + "\n"]
                self.lines = []

            def readline(self):
                return self.pending.pop(0) if self.pending else ""

            def write(self, value):
                parsed = json.loads(value)
                self.lines.append(parsed)
                if parsed.get("schema_version") == SCHEMA_VERSION:
                    self.pending.append(json.dumps(broker.reply(parsed)) + "\n")
                return len(value)

            def flush(self):
                pass

        descriptor = {"schema_version": "sandbox-get-runtime-config/v1", "model": "public-model", "agent_variant_id": "agent", "agent_version": "v1", "get_variant_id": "get", "get_version": "v1", "model_version": "v1", "model_config_digest": "a" * 64}
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "descriptor.json"; path.write_text(json.dumps(descriptor))
            stream = Conversation()
            self.assertEqual(run_stdio(str(path), stream, stream), 0)
        self.assertEqual([line["kind"] for line in stream.lines[:-1]], ["model", "tool", "result_download"])
        self.assertEqual(stream.lines[-1]["data"]["facts"]["revenue"]["value"], "10")
        self.assertEqual(broker.observations(), observations(model=1, model_done=1, tool=1, tool_done=1, download=1, download_done=1))

    def test_download_refuses_unregistered_or_non_oss_pointer_urls(self):
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key")
        denied = frame("result_download", "https://example.com/result.json", {"accept": "application/json"}, b"", 15000, "GET")
        self.assertEqual(broker.reply(denied)["error"], "route_denied")

    def test_alpha_tool_cannot_bind_a_non_oss_pointer(self):
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "financial_statement", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "statement": {"type": "income", "presentation": "standardized", "period": {"kind": "specified_period", "fiscal_year": 2024, "fiscal_period": "FY"}, "fields": ["revenue"]}}}
        model = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}]}
        bad_url = "https://example.com/result.json"
        bad_pointer = {"success": True, "result": {"status_code": 200, "message": "content is available", "full_content_file_url": bad_url, "truncated_content": "{}", "content_schema": {}}}
        broker = SandboxBroker(REQUEST_ID, query="AAPL FY2024 revenue", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=lambda _request, _timeout: Response(json.dumps(model).encode()), tool_opener=lambda _request, _timeout: Response(json.dumps(bad_pointer).encode()))
        model_body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": MODEL_GATEWAY_MAX_TOKENS, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL FY2024 revenue"}]}).encode()
        tool_body = json.dumps({"parameters": {"function": "INCOME_STATEMENT", "symbol": "AAPL"}}).encode()
        broker.reply(frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, model_body))
        broker.reply(frame("tool", "https://qveris.ai/api/v1/tools/execute?tool_id=alphavantage.income_statement.retrieve.v1.7aca3c4a", {"accept": "application/json", "content-type": "application/json", "idempotency-key": "idem-" + REQUEST_ID, "x-request-id": REQUEST_ID}, tool_body, 15000))
        self.assertEqual(broker.reply(frame("result_download", bad_url, {"accept": "application/json"}, b"", 15000, "GET"))["error"], "route_denied")


if __name__ == "__main__":
    unittest.main()

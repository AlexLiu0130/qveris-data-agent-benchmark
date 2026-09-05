import base64
import json
import pathlib
import tempfile
import unittest


import sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.sandbox_broker import SCHEMA_VERSION, SandboxBroker
from qveris_benchmark.sandbox_get_entry import run_stdio


REQUEST_ID = "request-1"


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


def frame(kind, url, headers, body=b"{}", timeout_ms=60000):
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "request_id": REQUEST_ID, "method": "POST", "url": url, "headers": headers, "body_b64": base64.b64encode(body).decode(), "timeout_ms": timeout_ms}


class SandboxBrokerTests(unittest.TestCase):
    def test_host_adds_auth_but_never_echoes_it(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return Response(b'{"choices":[]}', {"X-QVeris-Call-ID": "private-call-id", "Set-Cookie": "secret"})

        body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": 512, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL quote"}]}).encode()
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_api_key="model-secret", model_opener=opener)
        reply = broker.reply(frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, body))
        self.assertEqual(calls[0][0].get_header("Authorization"), "Bearer model-secret")
        self.assertEqual(reply["headers"], {"X-QVeris-Call-ID": "private-call-id"})
        self.assertNotIn("model-secret", json.dumps(reply))
        self.assertNotIn("secret", json.dumps(reply))
        self.assertEqual(broker.observations(), {"model_dispatches": 1, "model_completions": 1, "tool_dispatches": 0, "tool_completions": 0})

    def test_denied_or_credentialless_request_never_calls_transport(self):
        calls = []
        body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": 512, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL quote"}]}).encode()
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_opener=lambda *_: calls.append(True))
        denied = frame("model", "https://example.test/steal", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"})
        self.assertEqual(broker.reply(denied)["error"], "route_denied")
        allowed = frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, body)
        self.assertEqual(broker.reply(allowed)["error"], "credential_unavailable")
        self.assertEqual(calls, [])

    def test_exactly_one_model_then_one_approved_tool_can_dispatch(self):
        calls = []
        model_body = json.dumps({"model": "public-model", "stream": False, "temperature": 0, "max_tokens": 512, "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "AAPL quote"}]}).encode()
        tool_body = json.dumps({"parameters": {"function": "GLOBAL_QUOTE", "symbol": "AAPL", "entitlement": "realtime"}}).encode()
        broker = SandboxBroker(REQUEST_ID, query="AAPL quote", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=lambda request, timeout: (calls.append(request), Response(b"{}"))[1], tool_opener=lambda request, timeout: (calls.append(request), Response(b"{}"))[1])
        model = frame("model", "https://aigateway.qveris.ai/v1/chat/completions", {"content-type": "application/json", "x-request-id": REQUEST_ID, "x-qveris-source": "qveris-benchmark-public-get"}, model_body)
        tool = frame("tool", "https://qveris.ai/api/v1/tools/execute?tool_id=alphavantage.global_quote.retrieve.v1.9b8a7c6d", {"accept": "application/json", "content-type": "application/json", "idempotency-key": "idem-" + REQUEST_ID, "x-request-id": REQUEST_ID}, tool_body, 15000)
        self.assertEqual(broker.reply(model)["status"], 200)
        self.assertEqual(broker.reply(tool)["status"], 200)
        self.assertEqual(broker.reply(model)["error"], "route_denied")
        self.assertEqual(broker.reply(tool)["error"], "route_denied")
        self.assertEqual(len(calls), 2)
        self.assertEqual(broker.observations(), {"model_dispatches": 1, "model_completions": 1, "tool_dispatches": 1, "tool_completions": 1})

    def test_entry_and_host_broker_run_one_mocked_public_get_end_to_end(self):
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "market_quote", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "last_price"}}
        model = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}, "qveris_billing": {"call_id": "call-1", "usage_estimated": False}}
        tool = {"success": True, "result": {"data": {"Global Quote": {"01. symbol": "AAPL", "02. open": "1", "03. high": "2", "04. low": "0.5", "05. price": "1.5", "06. volume": "3", "07. latest trading day": "2026-09-04", "08. previous close": "1.2", "09. change": "0.3", "10. change percent": "25%"}}}}
        def opener(request, _timeout):
            return Response(json.dumps(model if "aigateway" in request.full_url else tool).encode(), {"X-QVeris-Call-ID": "call-1"} if "aigateway" in request.full_url else {})

        broker = SandboxBroker(REQUEST_ID, query="AAPL last price", model_identifier="public-model", model_api_key="model-key", tool_api_key="tool-key", model_opener=opener, tool_opener=opener)

        class Conversation:
            def __init__(self):
                self.pending = [json.dumps({"protocol_version": "sandbox-get-input/v1", "request_id": REQUEST_ID, "query": "AAPL last price"}) + "\n"]
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
        self.assertEqual(lines[-1]["data"]["quote"]["fields"]["last_price"]["value"], "1.5")
        self.assertEqual(broker.observations(), {"model_dispatches": 1, "model_completions": 1, "tool_dispatches": 1, "tool_completions": 1})


if __name__ == "__main__":
    unittest.main()

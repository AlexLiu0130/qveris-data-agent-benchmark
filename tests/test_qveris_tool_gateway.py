import io
import json
import pathlib
import socket
import sys
import unittest
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.qveris_tool_gateway import QVerisToolGateway, TOOL_GATEWAY_ERROR_CODES, ToolGatewayError
from qveris_benchmark.public_get import PublicGetAdapter


_IDENTITY = {
    "agent_variant_id": "semantic-agent", "agent_version": "v1", "get_variant_id": "public-get",
    "get_version": "v1", "model_identifier": "test-model", "model_version": "v1",
    "model_config_digest": "a" * 64,
}


class Response:
    status = 200

    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self, _size):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class QVerisToolGatewayTests(unittest.TestCase):
    def call(self, gateway):
        return gateway("fiu.quote.v1", {"symbol": "AAPL.US"}, request_id="request-1", idempotency_key="key-1")

    def test_posts_fixed_tool_contract_and_returns_only_private_data_envelope(self):
        opener, receipts = Opener(Response(json.dumps({"success": True, "result": {"data": {"price": 1}, "as_of": "2026-09-04T10:00:00Z"}, "execution_id": "private-execution", "actual_credits": 2}).encode())), []
        result = self.call(QVerisToolGateway(api_key="test-key", timeout_seconds=3, receipt_sink=receipts.append, opener=opener))
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.full_url, "https://qveris.ai/api/v1/tools/execute?tool_id=fiu.quote.v1")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Idempotency-key"), "key-1")
        self.assertEqual(request.get_header("X-request-id"), "request-1")
        self.assertEqual(json.loads(request.data), {"parameters": {"symbol": "AAPL.US"}})
        self.assertEqual(result, {"raw": {"price": 1}, "as_of": "2026-09-04T10:00:00Z"})
        self.assertEqual((receipts[0].tool_id, receipts[0].request_id, receipts[0].execution_id, receipts[0].actual_credits), ("fiu.quote.v1", "request-1", "private-execution", 2))
        self.assertEqual((receipts[0].correlation_id, receipts[0].server_correlated), ("request-1", True))
        self.assertNotIn("actual_credits", result)

    def test_rejects_non_success_and_malformed_result_without_returning_payload(self):
        for body, code in (
            ({"success": False, "error": {"code": "credits"}}, "rejected"),
            ({"success": False}, "response_shape_invalid"),
            ({"success": True, "result": {}}, "response_shape_invalid"),
            ({"success": True, "error": "bad", "result": {"data": {"price": 1}}}, "response_shape_invalid"),
        ):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ToolGatewayError, "^" + code + "$"):
                    self.call(QVerisToolGateway(api_key="test-key", opener=Opener(Response(json.dumps(body).encode()))))

    def test_enforces_http_size_and_single_timeout_without_retry(self):
        cases = (
            (Opener(Response(b"{}", status=201)), "http_other"),
            (Opener(Response(b"x" * (1024 * 1024 + 1))), "response_too_large"),
            (Opener(error=socket.timeout()), "timeout"),
            (Opener(error=URLError("down")), "transport_error"),
        )
        for opener, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(ToolGatewayError, "^" + code + "$"):
                    self.call(QVerisToolGateway(api_key="test-key", opener=opener))
                self.assertEqual(len(opener.calls), 1)

    def test_known_http_statuses_and_unknown_error_text_are_safely_normalized(self):
        for status in (400, 401, 402, 429, 503):
            with self.subTest(status=status):
                error = HTTPError("https://qveris.ai/api/v1/tools/execute", status, "secret", {}, io.BytesIO(b"secret response"))
                with self.assertRaisesRegex(ToolGatewayError, "^http_%d$" % status):
                    self.call(QVerisToolGateway(api_key="test-key", opener=Opener(error=error)))
        self.assertEqual(ToolGatewayError("api-key=secret").code, "internal_error")
        self.assertTrue({"http_400", "invalid_json", "timeout", "response_too_large"}.issubset(TOOL_GATEWAY_ERROR_CODES))

    def test_is_directly_compatible_with_public_get_gateway_injection(self):
        raw = {"Global Quote": {
            "01. symbol": "AAPL", "02. open": "1", "03. high": "2", "04. low": "0.5",
            "05. price": "1.5", "06. volume": "3", "07. latest trading day": "2026-09-04",
            "08. previous close": "1.2", "09. change": "0.3", "10. change percent": "25%",
        }}
        tool = QVerisToolGateway(api_key="test-key", opener=Opener(Response(json.dumps({"success": True, "result": {"data": raw}}).encode())))
        semantic = {"schema_version": "public-get.semantic/v1", "request": {
            "kind": "market_quote", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "quote_snapshot",
        }}
        result = PublicGetAdapter(lambda _query, **_kwargs: semantic, tool, **_IDENTITY).run("AAPL quote", request_id="request-1", idempotency_key="key-1")
        self.assertEqual((result.public_response["status"], result.public_response["data"]["quote"]["fields"]["close"]["value"]), ("success", "1.5"))


if __name__ == "__main__":
    unittest.main()

import io
import json
import pathlib
import socket
import ssl
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.qveris_tool_gateway import QVerisToolGateway, TOOL_GATEWAY_ERROR_CODES, ToolGatewayError
from qveris_benchmark.public_get import PublicGetAdapter


_IDENTITY = {
    "agent_variant_id": "semantic-agent", "agent_version": "v1", "get_variant_id": "public-get",
    "get_version": "v1", "model_identifier": "test-model", "model_version": "v1",
    "model_config_digest": "a" * 64,
}
_ALPHA_POINTER_TOOL_IDS = (
    "alphavantage.income_statement.retrieve.v1.7aca3c4a",
    "alphavantage.balance_sheet.retrieve.v1.467a92c0",
    "alphavantage.cash_flow.retrieve.v1.7aca3c4a",
)
_ALPHA_POINTER = {
    "status_code": 200,
    "message": "content is available",
    "full_content_file_url": "https://oss.qveris.ai/private-result.json?redacted=1",
    "truncated_content": "{}",
    "content_schema": {},
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
    def call(self, gateway, tool_id="fiu.quote.v1"):
        return gateway(tool_id, {"symbol": "AAPL.US"}, request_id="request-1", idempotency_key="key-1")

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

    def test_uses_verified_direct_tls_for_default_pointer_downloads(self):
        with patch("qveris_benchmark.qveris_tool_gateway.DirectHTTPSOpener") as transport:
            gateway = QVerisToolGateway(api_key="test-key")
        transport.assert_called_once_with(ssl_context=None, ca_file=None, environment_ca_file="QVERIS_TOOL_RESULT_CA_BUNDLE")
        self.assertIs(gateway._download_open, transport.return_value)

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
        self.assertEqual((result.public_response["status"], result.public_response["data"]["quote"]["fields"]["last_price"]["value"]), ("success", "1.5"))

    def test_downloads_frozen_alpha_pointers_once_without_authorization(self):
        for tool_id in _ALPHA_POINTER_TOOL_IDS:
            with self.subTest(tool_id=tool_id):
                final = {"symbol": "AAPL", "annualReports": [], "quarterlyReports": []}
                execute, download, receipts = Opener(Response(json.dumps({"success": True, "result": _ALPHA_POINTER, "execution_id": "private-execution", "actual_credits": 1}).encode())), Opener(Response(json.dumps(final).encode())), []
                result = self.call(QVerisToolGateway(api_key="test-key", timeout_seconds=3, receipt_sink=receipts.append, opener=execute, download_opener=download), tool_id)
                request, timeout = download.calls[0]
                self.assertEqual((result, timeout, len(execute.calls), len(download.calls)), ({"raw": final, "as_of": None}, 3.0, 1, 1))
                self.assertIsNone(request.get_header("Authorization"))
                self.assertEqual(request.get_header("Accept"), "application/json")
                self.assertEqual((receipts[0].tool_id, receipts[0].actual_credits), (tool_id, 1))

    def test_rejects_alpha_pointer_from_other_tools_private_hosts_and_redirects(self):
        cases = (
            ("fiu.quote.v1", _ALPHA_POINTER, None, "response_shape_invalid"),
            (_ALPHA_POINTER_TOOL_IDS[0], {**_ALPHA_POINTER, "full_content_file_url": "https://example.com/result.json"}, None, "download_rejected"),
            (_ALPHA_POINTER_TOOL_IDS[0], {**_ALPHA_POINTER, "full_content_file_url": "https://127.0.0.1/result.json"}, None, "download_rejected"),
            (_ALPHA_POINTER_TOOL_IDS[0], {**_ALPHA_POINTER, "full_content_file_url": "https://oss.qveris.ai.example.com/result.json"}, None, "download_rejected"),
            (_ALPHA_POINTER_TOOL_IDS[0], _ALPHA_POINTER, HTTPError("https://oss.qveris.ai/redirect", 302, "redirect", {}, io.BytesIO()), "download_rejected"),
            (_ALPHA_POINTER_TOOL_IDS[0], _ALPHA_POINTER, URLError(ssl.SSLCertVerificationError(20, "certificate rejected")), "download_transport_error"),
        )
        for tool_id, pointer, error, code in cases:
            with self.subTest(code=code):
                execute, download = Opener(Response(json.dumps({"success": True, "result": pointer}).encode())), Opener(error=error)
                with self.assertRaisesRegex(ToolGatewayError, "^" + code + "$"):
                    self.call(QVerisToolGateway(api_key="test-key", opener=execute, download_opener=download), tool_id)
                self.assertEqual(len(execute.calls), 1)
                self.assertEqual(len(download.calls), 0 if error is None else 1)


if __name__ == "__main__":
    unittest.main()

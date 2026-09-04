import json
import os
import pathlib
import sys
import threading
import time
import unittest
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import ProxyHandler
from unittest.mock import patch


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.qveris_search import (
    QVerisSearchClient,
    QVerisSearchHttpError,
    QVerisSearchProtocolError,
    QVerisSearchTransportError,
)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        response = self.server.response_for(self.command, self.path, body) if hasattr(self.server, "response_for") else self.server.response
        status, payload, headers, delay = response
        if delay:
            time.sleep(delay)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        try:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def log_message(self, *_args):
        pass


def _tool(tool_id="tool.quote"):
    return {
        "tool_id": tool_id,
        "name": "Quote Search",
        "description": "Catalog metadata, not a data response.",
        "params": [{"name": "symbol", "type": "string", "required": True, "description": "Ticker"}],
        "expected_cost": "0.2",
        "billing_rule": {"metering_mode": "per_call"},
    }


class SearchClientTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.response = (200, {"search_id": "search-1", "results": [_tool()], "remaining_credits": 9}, {"X-Qveris-Call-ID": "call-1"}, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d/api/v1" % self.server.server_port
        self.client = QVerisSearchClient(api_key="secret-key", base_url=self.base_url, timeout_seconds=.1)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_one_search_projects_only_safe_catalog_fields(self):
        catalog = self.client.search(query="NVDA FY2026 income statement", limit=3, session_id="session-1")
        self.assertEqual((catalog.search_id, catalog.call_id, catalog.remaining_credits), ("search-1", "call-1", 9.0))
        self.assertEqual(catalog.results[0].tool_id, "tool.quote")
        method, path, headers, body = self.server.requests[0]
        self.assertEqual((method, path, headers["Authorization"]), ("POST", "/api/v1/search", "Bearer secret-key"))
        self.assertEqual(json.loads(body), {"query": "NVDA FY2026 income statement", "limit": 3, "session_id": "session-1"})

    def test_results_are_bounded_and_strict(self):
        self.server.response = (200, {"search_id": "search-1", "results": [_tool("one"), _tool("two")]}, {}, 0)
        with self.assertRaises(QVerisSearchProtocolError) as raised:
            self.client.search(query="q", limit=1, session_id="session-2")
        self.assertEqual(raised.exception.error_code, "invalid_response")
        self.assertEqual(len(self.server.requests), 1)

        self.server.response = (200, {"search_id": "search-1", "results": [{"tool_id": "bad"}]}, {}, 0)
        with self.assertRaises(QVerisSearchProtocolError):
            self.client.search(query="q", limit=1, session_id="session-3")
        self.assertEqual(len(self.server.requests), 2)

        malformed = _tool("three")
        malformed["params"][0]["minimum"] = float("nan")
        self.server.response = (200, {"search_id": "search-1", "results": [malformed]}, {}, 0)
        with self.assertRaises(QVerisSearchProtocolError):
            self.client.search(query="q", limit=1, session_id="session-3a")

    def test_redirect_is_not_followed_or_retried(self):
        self.server.response = (302, {}, {"Location": "http://127.0.0.1:1/second-hop"}, 0)
        with self.assertRaises(QVerisSearchHttpError) as raised:
            self.client.search(query="q", limit=1, session_id="session-4")
        self.assertEqual(raised.exception.status_code, 302)
        self.assertEqual(len(self.server.requests), 1)

    def test_http_error_does_not_expose_server_message_or_key(self):
        self.server.response = (429, {"error": {"code": "rate_limited", "message": "raw provider detail"}}, {}, 0)
        with self.assertRaises(QVerisSearchHttpError) as raised:
            self.client.search(query="q", limit=1, session_id="session-5")
        self.assertEqual(raised.exception.error_code, "rate_limited")
        self.assertNotIn("raw provider detail", str(raised.exception))
        self.assertNotIn("secret-key", repr(self.client))
        self.assertEqual(len(self.server.requests), 1)

    def test_request_response_caps_and_timeout_fail_closed(self):
        capped = QVerisSearchClient(api_key="key", base_url=self.base_url, max_request_bytes=40)
        with self.assertRaisesRegex(ValueError, "size limit"):
            capped.search(query="q" * 100, limit=1, session_id="session-6")
        self.assertEqual(self.server.requests, [])

        tiny = QVerisSearchClient(api_key="key", base_url=self.base_url, max_response_bytes=8)
        with self.assertRaises(QVerisSearchProtocolError) as oversized:
            tiny.search(query="q", limit=1, session_id="session-7")
        self.assertEqual(oversized.exception.error_code, "response_too_large")

        self.server.response = (200, {"search_id": "search-1", "results": []}, {}, .3)
        with self.assertRaises(QVerisSearchTransportError) as timed_out:
            self.client.search(query="q", limit=1, session_id="session-8")
        self.assertEqual(timed_out.exception.error_code, "timeout")

    def test_only_official_bases_or_explicit_local_test_server_are_allowed(self):
        with self.assertRaisesRegex(ValueError, "official"):
            QVerisSearchClient(api_key="key", base_url="https://example.test/api/v1")
        self.assertEqual(QVerisSearchClient(api_key="key", base_url="https://qveris.cn/api/v1/")._base_url, "https://qveris.cn/api/v1")

    def test_explicitly_disabling_environment_proxy_uses_direct_single_request(self):
        with patch("qveris_benchmark.qveris_search.ProxyHandler", wraps=ProxyHandler) as proxy_handler:
            direct = QVerisSearchClient(api_key="secret-key", base_url=self.base_url, use_environment_proxy=False)
        proxy_handler.assert_called_once_with({})
        direct.search(query="q", limit=1, session_id="direct-1")
        self.assertEqual(len(self.server.requests), 1)

    def test_custom_verified_tls_context_is_used_and_invalid_trust_config_fails_closed(self):
        context = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
        with patch("qveris_benchmark.qveris_search.build_opener", wraps=__import__("qveris_benchmark.qveris_search", fromlist=["build_opener"]).build_opener) as opener:
            client = QVerisSearchClient(api_key="secret-key", base_url=self.base_url, ssl_context=context)
        self.assertIs(client._ssl_context, context)
        self.assertTrue(any(getattr(handler, "_context", None) is context for handler in opener.call_args.args))
        with self.assertRaisesRegex(ValueError, "verification"):
            QVerisSearchClient(api_key="key", base_url=self.base_url, ssl_context=ssl._create_unverified_context())
        with self.assertRaisesRegex(ValueError, "certificate bundle"):
            QVerisSearchClient(api_key="key", base_url=self.base_url, ca_file="/definitely/missing-ca.pem")

    def test_ssl_cert_file_environment_is_a_verified_context(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/etc/ssl/cert.pem"}, clear=True):
            client = QVerisSearchClient(api_key="key", base_url=self.base_url)
        self.assertEqual(client._ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(client._ssl_context.check_hostname)

    def test_inspect_and_execute_follow_the_local_go_client_contract_once(self):
        summary = _tool("tool.financial")
        del summary["params"]
        def responses(_method, path, _body):
            if path == "/api/v1/search":
                return 200, {"search_id": "search-1", "results": [summary]}, {}, 0
            if path == "/api/v1/tools/by-ids":
                return 200, {"success": True, "results": [_tool("tool.financial")], "remaining_credits": 5}, {}, 0
            if path == "/api/v1/tools/execute?tool_id=tool.financial":
                return 200, {"success": True, "execution_id": "exec-1", "cost": 1, "remaining_credits": 4, "result": {"data": {"safe": True}}}, {"X-Qveris-Call-ID": "call-exec"}, 0
            raise AssertionError(path)
        self.server.response_for = responses
        catalog = self.client.search(query="q", limit=1, session_id="session-9")
        self.assertIsNone(catalog.results[0].params)
        inspection = self.client.inspect(tool_id="tool.financial", search_id=catalog.search_id, session_id="session-9")
        execution = self.client.execute(tool_id=inspection.tool.tool_id, parameters={"symbol": "NVDA"}, search_id=catalog.search_id, session_id="session-9", idempotency_key="once-1")
        self.assertEqual((execution.execution_id, execution.cost, execution.remaining_credits, execution.result), ("exec-1", 1.0, 4.0, {"data": {"safe": True}}))
        self.assertEqual(len(self.server.requests), 3)
        method, path, headers, body = self.server.requests[-1]
        self.assertEqual((method, path, headers["Idempotency-Key"]), ("POST", "/api/v1/tools/execute?tool_id=tool.financial", "once-1"))
        self.assertEqual(json.loads(body)["parameters"], {"symbol": "NVDA"})

    def test_inspect_accepts_the_go_envelope_and_parameter_shape(self):
        # Go's InspectResponse makes success optional and ToolParam tolerates
        # structured types plus omitted description/required fields.
        inspected = {"tool_id": "tool.financial", "params": [
            {"name": "symbols", "type": {"type": "array", "items": "string"}},
            {"name": "period", "type": {"type": "string", "enum": ["FY"]}, "enum": ["FY"]},
        ]}
        self.server.response_for = lambda _method, path, _body: (200, {"results": [inspected]}, {}, 0) if path == "/api/v1/tools/by-ids" else (_ for _ in ()).throw(AssertionError(path))
        inspection = self.client.inspect(tool_id="tool.financial", search_id="search-3", session_id="session-11")
        self.assertEqual(inspection.tool.name, "")
        self.assertEqual(inspection.tool.description, "")
        self.assertEqual(inspection.tool.params, (
            {"name": "symbols", "type": "array<string>", "required": False, "description": ""},
            {"name": "period", "type": "string", "required": False, "description": "", "enum": ["FY"]},
        ))

    def test_inspect_projects_structured_enum_and_null_optional_fields(self):
        inspected = {"tool_id": "tool.financial", "params": [
            {"name": "symbol", "type": {"type": "string"}, "required": None, "description": None},
            {"name": "period", "type": {"type": "string", "enum": ["FY"]}, "required": None, "description": None},
            {"name": "limit", "type": {"type": "number"}, "required": None, "description": None},
        ]}
        self.server.response_for = lambda _method, path, _body: (200, {"results": [inspected]}, {}, 0) if path == "/api/v1/tools/by-ids" else (_ for _ in ()).throw(AssertionError(path))
        inspection = self.client.inspect(tool_id="tool.financial", search_id="search-3", session_id="session-11")
        self.assertEqual(inspection.tool.params, (
            {"name": "symbol", "type": "string", "required": False, "description": ""},
            {"name": "period", "type": "string", "required": False, "description": "", "enum": ["FY"]},
            {"name": "limit", "type": "number", "required": False, "description": ""},
        ))

    def test_inspect_rejects_conflicting_structured_and_top_level_enum(self):
        inspected = {"tool_id": "tool.financial", "params": [
            {"name": "period", "type": {"type": "string", "enum": ["FY"]}, "enum": ["Q"]},
        ]}
        self.server.response_for = lambda _method, path, _body: (200, {"results": [inspected]}, {}, 0) if path == "/api/v1/tools/by-ids" else (_ for _ in ()).throw(AssertionError(path))
        with self.assertRaisesRegex(QVerisSearchProtocolError, "conflicting enum"):
            self.client.inspect(tool_id="tool.financial", search_id="search-3", session_id="session-11")

    def test_execute_accepts_go_compatible_implicit_success_string_cost_and_scalar_result(self):
        self.server.response_for = lambda _method, path, _body: (200, {"execution_id": "exec-2", "cost": "1.25", "remaining_credits": "3.75", "result": ["data", 1]}, {}, 0) if path.startswith("/api/v1/tools/execute?") else (_ for _ in ()).throw(AssertionError(path))
        execution = self.client.execute(tool_id="tool.financial", parameters={"symbol": "NVDA"}, search_id="search-2", session_id="session-10", idempotency_key="once-2")
        self.assertEqual((execution.cost, execution.remaining_credits, execution.result), (1.25, 3.75, ["data", 1]))


if __name__ == "__main__":
    unittest.main()

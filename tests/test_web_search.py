import json
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.web_search import (
    TavilySearchHttpError,
    TavilySearchProtocolError,
    TavilySearchTransportError,
    TavilyWebSearchClient,
)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        status, payload, headers, delay = self.server.response
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


class TavilyWebSearchClientTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.response = self._success()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = TavilyWebSearchClient(api_key="tvly-secret-value", base_url="http://127.0.0.1:%d" % self.server.server_port, timeout_seconds=.1)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    @staticmethod
    def _success():
        return 200, {"query": "NVIDIA FY2026 income statement", "results": [{"url": "https://example.test/nvda", "title": "Untrusted title", "content": "Untrusted source text"}], "usage": {"credits": 1}, "request_id": "request-1"}, {}, 0

    def test_one_post_projects_bounded_untrusted_sources_and_safe_receipt(self):
        result = self.client.search(query="NVIDIA FY2026 income statement", limit=1)
        self.assertEqual((result.query, len(result.sources), result.sources[0].url), ("NVIDIA FY2026 income statement", 1, "https://example.test/nvda"))
        self.assertTrue(result.as_of.endswith("Z"))
        self.assertEqual((self.client.last_receipt.request_id, self.client.last_receipt.credits, self.client.last_receipt.cost_usd), ("request-1", 1.0, None))
        method, path, headers, raw = self.server.requests[0]
        self.assertEqual((method, path, headers["Authorization"]), ("POST", "/search", "Bearer tvly-secret-value"))
        self.assertEqual(json.loads(raw), {"query": "NVIDIA FY2026 income statement", "search_depth": "basic", "max_results": 1, "include_answer": False, "include_raw_content": False, "include_images": False, "include_favicon": False, "auto_parameters": False, "include_usage": True})
        self.assertNotIn("tvly-secret-value", repr(self.client))

    def test_redirect_is_one_failed_request_without_following(self):
        self.server.response = 302, {}, {"Location": "http://127.0.0.1:1/second-hop"}, 0
        with self.assertRaises(TavilySearchHttpError) as raised:
            self.client.search(query="NVIDIA FY2026 income statement", limit=1)
        self.assertEqual((raised.exception.status_code, raised.exception.error_code, len(self.server.requests)), (302, "http_302", 1))

    def test_oversize_malformed_and_timeout_fail_closed_without_retry(self):
        tiny = TavilyWebSearchClient(api_key="key", base_url="http://127.0.0.1:%d" % self.server.server_port, max_response_bytes=8)
        with self.assertRaises(TavilySearchProtocolError) as oversize:
            tiny.search(query="NVIDIA FY2026 income statement", limit=1)
        self.assertEqual(oversize.exception.error_code, "response_too_large")
        self.assertEqual(len(self.server.requests), 1)

        self.server.response = 200, b"not-json", {}, 0
        with self.assertRaises(TavilySearchProtocolError) as malformed:
            self.client.search(query="NVIDIA FY2026 income statement", limit=1)
        self.assertEqual(malformed.exception.error_code, "invalid_json")
        self.assertEqual(len(self.server.requests), 2)

        self.server.response = 200, self._success()[1], {}, .3
        with self.assertRaises(TavilySearchTransportError) as timeout:
            self.client.search(query="NVIDIA FY2026 income statement", limit=1)
        self.assertEqual(timeout.exception.error_code, "timeout")
        self.assertEqual(len(self.server.requests), 3)

    def test_rejects_oversized_or_non_https_sources_and_invalid_limits(self):
        self.server.response = 200, {"query": "NVIDIA FY2026 income statement", "results": [{"url": "http://example.test", "title": "title", "content": "content"}]}, {}, 0
        with self.assertRaises(TavilySearchProtocolError):
            self.client.search(query="NVIDIA FY2026 income statement", limit=1)
        with self.assertRaises(ValueError):
            self.client.search(query="x", limit=6)


if __name__ == "__main__":
    unittest.main()

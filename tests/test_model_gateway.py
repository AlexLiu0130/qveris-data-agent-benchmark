import json
import os
import pathlib
import sys
import threading
import time
import unittest
import ssl
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import ProxyHandler

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.model_gateway import (
    ModelGatewayClient,
    ModelGatewayHttpError,
    ModelGatewayProtocolError,
    ModelGatewayTransportError,
)


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.command, self.path, dict(self.headers), None))
        self.server.respond(self, self.server.response_for(self.command, self.path, None))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        self.server.respond(self, self.server.response_for(self.command, self.path, body))

    def log_message(self, *_args):
        pass


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.responses = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
        self.server.requests = []
        self.server.response_for = lambda method, path, body: self.responses[(method, path)]
        self.server.respond = self._respond
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, timeout_seconds=0.2)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    @staticmethod
    def _respond(handler, response):
        status, payload, headers, delay = response
        if delay:
            time.sleep(delay)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        try:
            handler.send_response(status)
            for key, value in headers.items():
                handler.send_header(key, value)
            handler.send_header("Content-Length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
        except BrokenPipeError:
            pass

    @staticmethod
    def _models():
        return 200, {"object": "list", "data": [{"id": "qveris-model-a"}]}, {}, 0

    @staticmethod
    def _completion(*, usage=True, call_id="call-1", billing_call_id="call-1", model="qveris-model-a", finish_reason="stop"):
        payload = {
            "model": model,
            "choices": [{"message": {"content": "structured output"}, "finish_reason": finish_reason}],
            "qveris_billing": {"call_id": billing_call_id, "credits_charged": 1.25, "cost_usd": 0.02, "usage_estimated": False},
        }
        if usage:
            payload["usage"] = {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
        return 200, payload, {"X-Qveris-Call-ID": call_id}, 0

    def _freeze_model(self):
        self.responses[("GET", "/v1/models")] = self._models()
        self.assertEqual([item.model_id for item in self.client.list_models(request_id="models-1")], ["qveris-model-a"])

    def test_list_then_non_streaming_completion_uses_gateway_headers_and_safe_receipt(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion()
        result = self.client.chat_completions(
            model_id="qveris-model-a",
            messages=[{"role": "user", "content": "hello"}],
            request_id="attempt-1",
        )
        self.assertEqual((result.model_id, result.request_id, result.call_id, result.content), ("qveris-model-a", "attempt-1", "call-1", "structured output"))
        self.assertEqual((result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens), (3, 5, 8))
        self.assertFalse(result.usage_estimated)
        self.assertEqual(result.finish_reason, "stop")
        method, path, headers, body = self.server.requests[-1]
        self.assertEqual((method, path), ("POST", "/v1/chat/completions"))
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(headers["X-Request-Id"], "attempt-1")
        self.assertEqual(headers["X-Qveris-Source"], "qveris-benchmark")
        self.assertFalse(json.loads(body)["stream"])

    def test_usage_is_explicitly_unknown_when_gateway_omits_standard_usage(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion(usage=False)
        result = self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-2")
        self.assertIsNone(result.usage)
        self.assertFalse(result.usage_estimated)

    def test_finish_reason_is_whitelisted_without_rejecting_omitted_or_new_provider_values(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion(finish_reason="length")
        self.assertEqual(self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-length").finish_reason, "length")
        self.responses[("POST", "/v1/chat/completions")] = self._completion(finish_reason="provider_new_value")
        self.assertIsNone(self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-unknown").finish_reason)
        payload = self._completion()[1]
        del payload["choices"][0]["finish_reason"]
        self.responses[("POST", "/v1/chat/completions")] = (200, payload, {"X-Qveris-Call-ID": "call-1"}, 0)
        self.assertIsNone(self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-missing").finish_reason)

    def test_completion_accepts_bounded_explicit_generation_controls_without_streaming(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion()
        self.client.chat_completions(
            model_id="qveris-model-a",
            messages=[{"role": "user", "content": "hello"}],
            request_id="attempt-controls",
            temperature=.2,
            max_tokens=512,
            response_format="json_object",
        )
        payload = json.loads(self.server.requests[-1][3])
        self.assertEqual(payload["temperature"], .2)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertFalse(payload["stream"])
        for field, value in (("temperature", -1), ("temperature", 3), ("max_tokens", 0), ("max_tokens", True), ("response_format", "stream")):
            with self.assertRaises(ValueError):
                self.client.chat_completions(
                    model_id="qveris-model-a",
                    messages=[{"role": "user", "content": "hello"}],
                    request_id="attempt-invalid-" + field,
                    **{field: value},
                )

    def test_chat_requires_model_from_list_models(self):
        with self.assertRaisesRegex(ValueError, "list_models"):
            self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-3")
        self.assertEqual(self.server.requests, [])

    def test_model_listing_is_frozen_after_the_first_successful_response(self):
        self._freeze_model()
        self.responses[("GET", "/v1/models")] = (200, {"data": [{"id": "different-model"}]}, {}, 0)
        self.assertEqual([item.model_id for item in self.client.list_models(request_id="models-ignored")], ["qveris-model-a"])
        self.assertEqual(len(self.server.requests), 1)

    def test_429_preserves_safe_error_fields_and_never_retries(self):
        self.responses[("GET", "/v1/models")] = (
            429,
            {"error": {"code": "rate_limited", "message": "do not retain this body"}},
            {"X-Qveris-Call-ID": "call-429", "Retry-After": "7"},
            0,
        )
        with self.assertRaises(ModelGatewayHttpError) as raised:
            self.client.list_models(request_id="models-429")
        error = raised.exception
        self.assertEqual((error.status_code, error.error_code, error.call_id, error.retry_after), (429, "rate_limited", "call-429", 7))
        self.assertNotIn("do not retain", str(error))
        self.assertEqual(len(self.server.requests), 1)

    def test_invalid_json_oversize_and_timeout_fail_closed(self):
        self.responses[("GET", "/v1/models")] = (200, b"not-json", {}, 0)
        with self.assertRaises(ModelGatewayProtocolError) as invalid_json:
            self.client.list_models(request_id="models-invalid-json")
        self.assertEqual(invalid_json.exception.error_code, "invalid_json")

        small = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, max_response_bytes=8)
        self.responses[("GET", "/v1/models")] = self._models()
        with self.assertRaises(ModelGatewayProtocolError) as oversize:
            small.list_models(request_id="models-oversize")
        self.assertEqual(oversize.exception.error_code, "response_too_large")

        self.responses[("GET", "/v1/models")] = (200, {"data": []}, {}, 0.4)
        with self.assertRaises(ModelGatewayTransportError) as timeout:
            self.client.list_models(request_id="models-timeout")
        self.assertEqual(timeout.exception.error_code, "timeout")

    def test_received_http_parse_failures_expose_only_the_fixed_diagnostic_projection(self):
        marker = "raw-provider-body-must-not-persist"
        cases = (
            (200, b"", {"Content-Type": "application/json; charset=utf-8", "X-Qveris-Call-ID": "call-empty"}, "empty_body", "json", "utf8"),
            (200, b"\xff", {"Content-Type": "application/json; charset=utf-8", "X-Qveris-Call-ID": "call-utf8"}, "invalid_utf8", "json", "utf8"),
            (502, ("<html>" + marker).encode(), {"Content-Type": "text/html; charset=latin1", "Content-Encoding": "gzip", "X-Qveris-Call-ID": "call-html"}, "invalid_json", "html", "non_utf8"),
        )
        for status, body, headers, state, content_type, charset in cases:
            with self.subTest(state=state):
                self.responses[("GET", "/v1/models")] = (status, body, headers, 0)
                with self.assertRaises(ModelGatewayProtocolError) as raised:
                    self.client.list_models(request_id="models-" + state)
                error = raised.exception
                receipt = error.gateway_diagnostic
                self.assertEqual((error.status_code, error.error_code), (status, state))
                self.assertEqual((receipt["http_status"], receipt["content_type_class"], receipt["charset_class"], receipt["body_state"]), (status, content_type, charset, state))
                self.assertEqual(set(receipt), {"http_status", "content_type_class", "content_encoding_class", "charset_class", "declared_body_bytes", "observed_body_bytes", "body_state", "body_sha256", "call_id_sha256"})
                self.assertNotIn(marker, str(receipt))

        small = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, max_response_bytes=8)
        self.responses[("GET", "/v1/models")] = (200, b"0123456789", {"Content-Type": "text/event-stream", "Content-Encoding": "br"}, 0)
        with self.assertRaises(ModelGatewayProtocolError) as oversized:
            small.list_models(request_id="models-oversized-diagnostic")
        self.assertEqual((oversized.exception.error_code, oversized.exception.gateway_diagnostic["body_state"], oversized.exception.gateway_diagnostic["content_type_class"], oversized.exception.gateway_diagnostic["content_encoding_class"], oversized.exception.gateway_diagnostic["body_sha256"]), ("response_too_large", "response_too_large", "sse", "br", None))

        class IncompleteResponse:
            headers = {"Content-Type": "application/json", "Content-Length": "99"}
            @staticmethod
            def read(_limit):
                raise IncompleteRead(b"partial-" + marker.encode(), 42)
        with self.assertRaises(ModelGatewayProtocolError) as incomplete:
            self.client._read_limited(IncompleteResponse(), status_code=200, call_id="call-partial", retry_after=None)
        self.assertEqual((incomplete.exception.error_code, incomplete.exception.gateway_diagnostic["body_state"], incomplete.exception.gateway_diagnostic["body_sha256"]), ("response_truncated", "response_truncated", None))
        self.assertNotIn(marker, str(incomplete.exception.gateway_diagnostic))

    def test_redirect_request_cap_and_unsafe_error_code_are_rejected_without_retry(self):
        self.responses[("GET", "/v1/models")] = (302, {}, {"Location": "http://127.0.0.1:1/second-hop"}, 0)
        with self.assertRaises(ModelGatewayProtocolError) as redirect:
            self.client.list_models(request_id="models-redirect")
        self.assertEqual(redirect.exception.error_code, "invalid_error_envelope")
        self.assertEqual(len(self.server.requests), 1)

        capped = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, max_request_bytes=512)
        self.responses[("GET", "/v1/models")] = self._models()
        capped.list_models(request_id="models-capped")
        with self.assertRaisesRegex(ValueError, "size limit"):
            capped.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "x" * 1024}], request_id="attempt-capped")
        self.assertEqual(len(self.server.requests), 2)

        self.responses[("GET", "/v1/models")] = (400, {"error": {"code": "bad\ncode"}}, {}, 0)
        other = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port)
        with self.assertRaises(ModelGatewayProtocolError) as unsafe_error:
            other.list_models(request_id="models-unsafe-error")
        self.assertEqual(unsafe_error.exception.error_code, "invalid_error_envelope")

    def test_completion_rejects_mismatched_billing_and_header_call_id(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion(billing_call_id="different-call")
        with self.assertRaises(ModelGatewayProtocolError) as raised:
            self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-4")
        self.assertEqual(raised.exception.error_code, "billing_call_id_mismatch")

    def test_completion_requires_response_model_identity(self):
        self._freeze_model()
        payload = self._completion()[1]
        del payload["model"]
        self.responses[("POST", "/v1/chat/completions")] = (200, payload, {"X-Qveris-Call-ID": "call-1"}, 0)
        with self.assertRaises(ModelGatewayProtocolError) as raised:
            self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-missing-model")
        self.assertEqual(raised.exception.error_code, "missing_response_model")

    def test_completion_rejects_mismatched_response_model_identity(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion(model="qveris-model-b")
        with self.assertRaises(ModelGatewayProtocolError) as raised:
            self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-mismatched-model")
        self.assertEqual(raised.exception.error_code, "response_model_mismatch")

    def test_completion_returns_matching_response_model_identity(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = self._completion(model="qveris-model-a")
        result = self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-matching-model")
        self.assertEqual(result.model_id, "qveris-model-a")

    def test_completion_requires_billing_and_header_call_id(self):
        self._freeze_model()
        self.responses[("POST", "/v1/chat/completions")] = (
            200,
            {"model": "qveris-model-a", "choices": [{"message": {"content": "structured output"}, "finish_reason": "stop"}]},
            {},
            0,
        )
        with self.assertRaises(ModelGatewayProtocolError) as raised:
            self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-6")
        self.assertEqual(raised.exception.error_code, "missing_billing")

        self.responses[("POST", "/v1/chat/completions")] = (
            200,
            self._completion()[1],
            {},
            0,
        )
        with self.assertRaises(ModelGatewayProtocolError) as missing_header:
            self.client.chat_completions(model_id="qveris-model-a", messages=[{"role": "user", "content": "hello"}], request_id="attempt-7")
        self.assertEqual(missing_header.exception.error_code, "missing_call_id")

    def test_key_is_not_exposed_by_errors_or_repr(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as missing:
                ModelGatewayClient(base_url="http://127.0.0.1:1")
        self.assertNotIn("super-secret", str(missing.exception))
        client = ModelGatewayClient(api_key="super-secret", base_url="http://127.0.0.1:1")
        self.assertNotIn("super-secret", repr(client))
        with self.assertRaises(ValueError) as bad_model:
            client.chat_completions(model_id="not-listed", messages=[{"role": "user", "content": "hello"}], request_id="attempt-5")
        self.assertNotIn("super-secret", str(bad_model.exception))

    def test_custom_verified_tls_context_is_used_and_invalid_trust_config_fails_closed(self):
        context = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
        with patch("qveris_benchmark.model_gateway.build_opener", wraps=__import__("qveris_benchmark.model_gateway", fromlist=["build_opener"]).build_opener) as opener:
            client = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, ssl_context=context)
        self.assertIs(client._ssl_context, context)
        self.assertTrue(any(getattr(handler, "_context", None) is context for handler in opener.call_args.args))
        with self.assertRaisesRegex(ValueError, "verification"):
            ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:1", ssl_context=ssl._create_unverified_context())
        with self.assertRaisesRegex(ValueError, "certificate bundle"):
            ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:1", ca_file="/definitely/missing-ca.pem")

    def test_gateway_ca_bundle_environment_is_a_verified_context(self):
        with patch.dict(os.environ, {"GATEWAY_CA_BUNDLE": "/etc/ssl/cert.pem"}, clear=True):
            client = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:1")
        self.assertEqual(client._ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(client._ssl_context.check_hostname)

    def test_base_url_rejects_non_loopback_http(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            ModelGatewayClient(api_key="test-key", base_url="http://example.test")

    def test_proxy_can_be_explicitly_disabled_without_weakening_redirect_protection(self):
        client = ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, use_environment_proxy=False)
        proxies = [handler.proxies for handler in client._opener.handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(proxies, [])
        with self.assertRaisesRegex(ValueError, "use_environment_proxy"):
            ModelGatewayClient(api_key="test-key", base_url="http://127.0.0.1:%d" % self.server.server_port, use_environment_proxy="false")


if __name__ == "__main__":
    unittest.main()

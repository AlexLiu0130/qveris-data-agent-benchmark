import io
import json
import pathlib
import socket
import sys
import unittest
from urllib.error import HTTPError

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.public_get import PublicGetAdapter
from qveris_benchmark.qveris_model_gateway import MODEL_GATEWAY_MAX_TOKENS, _SYSTEM_PROMPT, QVerisModelGatewaySemanticResolver, SEMANTIC_GATEWAY_ERROR_CODES, SemanticGatewayError


def semantic():
    return {
        "schema_version": "public-get.semantic/v1",
        "request": {
            "kind": "market_quote",
            "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"},
            "operation": "quote_snapshot",
        },
    }


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


class QVerisModelGatewayTests(unittest.TestCase):
    def resolver(self, opener):
        return QVerisModelGatewaySemanticResolver(api_key="sk-test-key", model="public-model", opener=opener)

    def test_posts_fixed_non_streaming_contract_and_returns_model_only_usage(self):
        seen = []
        call_id = "call-1"
        body = {
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic())}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            "qveris_billing": {"call_id": call_id, "usage_estimated": False},
        }

        def opener(request, timeout):
            seen.append((request, timeout))
            return Response(json.dumps(body).encode(), {"X-QVeris-Call-ID": call_id})

        resolved = self.resolver(opener)("AAPL latest quote", request_id="request-1")
        self.assertEqual(resolved.semantic, semantic())
        self.assertEqual(resolved.usage["total_tokens"], 17)
        self.assertEqual(resolved.usage["request_id"], "request-1")
        self.assertNotEqual(resolved.usage["receipt_id"], call_id)
        request, timeout = seen[0]
        self.assertEqual((request.full_url, request.get_method(), timeout), ("https://aigateway.qveris.ai/v1/chat/completions", "POST", 60.0))
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test-key")
        payload = json.loads(request.data)
        self.assertEqual((payload["model"], payload["stream"], payload["temperature"], payload["max_tokens"]), ("public-model", False, 0, MODEL_GATEWAY_MAX_TOKENS))

    def test_gateway_error_code_is_stable_and_has_no_response_body(self):
        error = HTTPError("https://aigateway.qveris.ai/v1/chat/completions", 402, "payment", {}, io.BytesIO(b'{"error":{"code":"insufficient_credits"}}'))
        with self.assertRaisesRegex(SemanticGatewayError, "^http_402$"):
            self.resolver(lambda _request, _timeout: (_ for _ in ()).throw(error))("AAPL", request_id="request-1")

    def test_known_http_statuses_and_unknown_error_text_are_safely_normalized(self):
        for status in (400, 401, 402, 429, 503):
            with self.subTest(status=status):
                error = HTTPError("https://aigateway.qveris.ai/v1/chat/completions", status, "secret", {}, io.BytesIO(b"secret response"))
                with self.assertRaisesRegex(SemanticGatewayError, "^http_%d$" % status):
                    self.resolver(lambda _request, _timeout, error=error: (_ for _ in ()).throw(error))("AAPL", request_id="request-1")
        self.assertEqual(SemanticGatewayError("api-key=secret").code, "internal_error")
        self.assertTrue({"http_400", "invalid_json", "semantic_schema_invalid", "usage_missing", "timeout", "response_too_large"}.issubset(SEMANTIC_GATEWAY_ERROR_CODES))

    def test_timeout_and_response_limit_fail_closed(self):
        with self.subTest("timeout"):
            with self.assertRaisesRegex(SemanticGatewayError, "^timeout$"):
                self.resolver(lambda _request, _timeout: (_ for _ in ()).throw(socket.timeout()))("AAPL", request_id="request-1")
        with self.subTest("response too large"):
            with self.assertRaisesRegex(SemanticGatewayError, "^response_too_large$"):
                self.resolver(lambda _request, _timeout: Response(b"x" * (256 * 1024 + 1)))("AAPL", request_id="request-1")

    def test_completion_json_rejects_duplicate_keys_and_nonfinite_constants(self):
        for body in (b'{"choices":[],"choices":[]}', b'{"choices":NaN}'):
            with self.subTest(body=body):
                with self.assertRaisesRegex(SemanticGatewayError, "^invalid_json$"):
                    self.resolver(lambda _request, _timeout, body=body: Response(body))("AAPL", request_id="request-1")

    def test_semantic_schema_is_validated_before_adapter_routing(self):
        invalid = semantic()
        invalid["request"]["tool_id"] = "model-cannot-set-this"
        body = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(invalid)}}]}
        with self.assertRaisesRegex(SemanticGatewayError, "^semantic_schema_invalid$"):
                self.resolver(lambda _request, _timeout: Response(json.dumps(body).encode()))("AAPL", request_id="request-1")

    def test_accepts_one_complete_json_fence_but_not_surrounding_prose(self):
        call_id = "call-1"
        base = {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}, "qveris_billing": {"call_id": call_id, "usage_estimated": False}}
        fenced = {**base, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "```json\n" + json.dumps(semantic()) + "\n```"}}]}
        self.assertEqual(self.resolver(lambda _request, _timeout: Response(json.dumps(fenced).encode(), {"X-QVeris-Call-ID": call_id}))("AAPL", request_id="request-1").semantic, semantic())
        prose = {**base, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Here is JSON: " + json.dumps(semantic())}}]}
        with self.assertRaisesRegex(SemanticGatewayError, "^semantic_json_invalid$"):
            self.resolver(lambda _request, _timeout: Response(json.dumps(prose).encode(), {"X-QVeris-Call-ID": call_id}))("AAPL", request_id="request-1")

    def test_prompt_schema_version_matches_the_router_validator(self):
        self.assertIn('"schema_version":"public-get.semantic/v1"', _SYSTEM_PROMPT)
        self.assertNotIn("public-get.semantic.v1", _SYSTEM_PROMPT)

    def test_prompt_contract_includes_exact_routable_shapes_and_examples(self):
        self.assertIn('"kind":"market_quote","security":{"asset_class":"equity","venue":"US|SSE|SZSE|HKEX|JP|GB|DE","local_code":"string"}', _SYSTEM_PROMPT)
        self.assertIn('For batch_quote_snapshot use securities instead of security', _SYSTEM_PROMPT)
        self.assertIn('You may add requested_fields only when the user explicitly names fields.', _SYSTEM_PROMPT)
        self.assertIn('"security":{"asset_class":"equity","venue":"US","local_code":"AAPL"},"operation":"quote_snapshot"', _SYSTEM_PROMPT)
        self.assertIn('"adjustment":"adjusted|unadjusted|not_applicable","interval":"daily|weekly|monthly|intraday|5min|15min|30min|60min"', _SYSTEM_PROMPT)
        self.assertIn('"operation":"corporate_actions","adjustment":"not_applicable","start_date":"2024-01-01","end_date":"2024-12-31"', _SYSTEM_PROMPT)
        self.assertIn('"operation":"trading_calendar","adjustment":"not_applicable","start_date":"2024-01-02","end_date":"2024-01-05"', _SYSTEM_PROMPT)
        self.assertIn('{"kind":"latest","basis":"filed|report","frequency":"annual|quarter"}', _SYSTEM_PROMPT)
        self.assertIn('cash_flow=net_cash_from_operating,net_cash_from_investing,net_cash_from_financing', _SYSTEM_PROMPT)

    def test_every_standalone_json_prompt_example_is_parseable(self):
        examples = [line for line in _SYSTEM_PROMPT.splitlines() if line.startswith("{")]
        self.assertGreater(len(examples), 3)
        for example in examples:
            with self.subTest(example=example):
                self.assertIsInstance(json.loads(example), dict)
        self.assertIn("never manufacture a ticker, venue, date, fiscal period,", _SYSTEM_PROMPT)

    def test_resolver_to_adapter_routes_aapl_quote(self):
        call_id = "call-1"
        body = {
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic())}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            "qveris_billing": {"call_id": call_id, "usage_estimated": False},
        }
        resolver = self.resolver(lambda _request, _timeout: Response(json.dumps(body).encode(), {"X-QVeris-Call-ID": call_id}))
        calls = []

        def gateway(tool_id, params, *, request_id, idempotency_key):
            calls.append((tool_id, params, request_id, idempotency_key))
            return {"raw": {"Global Quote": {"01. symbol": "AAPL", "02. open": "1", "03. high": "2", "04. low": "0.5", "05. price": "1.5", "06. volume": "3", "07. latest trading day": "2026-09-04", "08. previous close": "1.2", "09. change": "0.3", "10. change percent": "25%"}}}

        adapter = PublicGetAdapter(resolver, gateway, agent_variant_id="semantic-agent", agent_version="v1", get_variant_id="public-get", get_version="v1", model_identifier="test-model", model_version="v1", model_config_digest="a" * 64)
        result = adapter.run("AAPL latest quote", request_id="request-1", idempotency_key="key-1")
        self.assertEqual((result.public_response["status"], result.public_response["data"]["quote"]["instrument"]["symbol"]), ("success", "AAPL"))
        self.assertEqual(calls, [("alphavantage.global_quote.retrieve.v1.9b8a7c6d", {"function": "GLOBAL_QUOTE", "symbol": "AAPL", "entitlement": "realtime"}, "request-1", "key-1")])

    def test_semantic_schema_error_has_one_prefix_in_public_response(self):
        adapter = PublicGetAdapter(lambda _query, **_kwargs: (_ for _ in ()).throw(SemanticGatewayError("semantic_schema_invalid")), lambda *_args, **_kwargs: self.fail("must not dispatch"), agent_variant_id="semantic-agent", agent_version="v1", get_variant_id="public-get", get_version="v1", model_identifier="test-model", model_version="v1", model_config_digest="a" * 64)
        result = adapter.run("AAPL latest quote", request_id="request-1", idempotency_key="key-1")
        self.assertEqual(result.public_response["terminal_reason"], "semantic_schema_invalid")

    def test_missing_or_invalid_usage_is_a_safe_failure(self):
        valid = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic())}}]}
        for body, code in ((valid, "usage_missing"), ({**valid, "usage": {}}, "usage_missing"), ({**valid, "usage": {"prompt_tokens": 1}, "qveris_billing": {"usage_estimated": False}}, "usage_invalid")):
            with self.subTest(code=code):
                with self.assertRaisesRegex(SemanticGatewayError, "^" + code + "$"):
                    self.resolver(lambda _request, _timeout, body=body: Response(json.dumps(body).encode()))("AAPL", request_id="request-1")

    def test_truncated_completion_preserves_valid_usage_without_parsing_partial_json(self):
        call_id = "call-1"
        body = {
            "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": '{"schema_version"'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1024, "total_tokens": 1036},
            "qveris_billing": {"call_id": call_id, "usage_estimated": False},
        }
        with self.assertRaisesRegex(SemanticGatewayError, "^semantic_completion_truncated$") as raised:
            self.resolver(lambda _request, _timeout: Response(json.dumps(body).encode(), {"X-QVeris-Call-ID": call_id}))("AAPL", request_id="request-1")
        self.assertEqual(raised.exception.usage["total_tokens"], 1036)

    def test_models_preflight_is_explicit_read_only_and_does_not_select_a_model(self):
        seen = []

        def opener(request, timeout):
            seen.append((request, timeout))
            return Response(b'{"object":"list","data":[{"id":"other-model"},{"id":"public-model"}]}')

        preflight = self.resolver(opener).preflight_models(request_id="request-1")
        self.assertEqual((preflight.configured_model, preflight.available_model_ids), ("public-model", ("other-model", "public-model")))
        request, timeout = seen[0]
        self.assertEqual((request.full_url, request.get_method(), timeout), ("https://aigateway.qveris.ai/v1/models", "GET", 60.0))
        self.assertIsNone(request.data)

    def test_model_preflight_failure_codes_are_safe_and_stable(self):
        for status, expected in ((401, "model_preflight_http_401"), (500, "model_preflight_http_other")):
            with self.subTest(status=status):
                error = HTTPError("https://aigateway.qveris.ai/v1/models", status, "CANARY_SECRET", {}, io.BytesIO(b"CANARY_BODY"))
                with self.assertRaisesRegex(SemanticGatewayError, "^" + expected + "$") as raised:
                    self.resolver(lambda _request, _timeout, error=error: (_ for _ in ()).throw(error)).preflight_models(request_id="request-1")
                self.assertNotIn("CANARY", str(raised.exception))
        with self.assertRaisesRegex(SemanticGatewayError, "^model_preflight_response_invalid$"):
            self.resolver(lambda _request, _timeout: Response(b"CANARY_NOT_JSON")).preflight_models(request_id="request-1")
        with self.assertRaisesRegex(SemanticGatewayError, "^model_preflight_timeout$"):
            self.resolver(lambda _request, _timeout: (_ for _ in ()).throw(socket.timeout())).preflight_models(request_id="request-1")
        for body in (b'{"data":[],"data":[]}', b'{"data":NaN}'):
            with self.subTest(preflight_body=body):
                with self.assertRaisesRegex(SemanticGatewayError, "^model_preflight_response_invalid$"):
                    self.resolver(lambda _request, _timeout, body=body: Response(body)).preflight_models(request_id="request-1")


if __name__ == "__main__":
    unittest.main()

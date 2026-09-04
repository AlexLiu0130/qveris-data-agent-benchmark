"""One-call QVeris Model Gateway client for public-GET semantic compilation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL_GATEWAY_BASE_URL = "https://aigateway.qveris.ai"
MODEL_GATEWAY_CHAT_ENDPOINT = "/v1/chat/completions"
MODEL_GATEWAY_MODELS_ENDPOINT = "/v1/models"
MODEL_GATEWAY_MAX_REQUEST_BYTES = 64 * 1024
MODEL_GATEWAY_MAX_RESPONSE_BYTES = 256 * 1024
MODEL_GATEWAY_TIMEOUT_SECONDS = 60.0
MODEL_GATEWAY_MAX_TOKENS = 512
_SYSTEM_PROMPT = """You compile one public financial-data query into exactly one JSON object.
Return JSON only: no Markdown, prose, tool, provider, route, parser, or provider parameters.
The exact top-level shape is {\"schema_version\":\"public-get.semantic/v1\",\"request\":{...}}.

`request` must be exactly one of these objects (no extra or omitted keys):
1. market quote:
{\"kind\":\"market_quote\",\"security\":{\"asset_class\":\"equity\",\"venue\":\"US|SSE|SZSE|HKEX\",\"local_code\":\"string\"},\"operation\":\"quote_snapshot|last_price|bid_ask_l1|volume_turnover_snapshot|latest_trade|extended_hours_price|trading_status|batch_quote_snapshot\"}
Example for “AAPL latest quote”:
{\"schema_version\":\"public-get.semantic/v1\",\"request\":{\"kind\":\"market_quote\",\"security\":{\"asset_class\":\"equity\",\"venue\":\"US\",\"local_code\":\"AAPL\"},\"operation\":\"quote_snapshot\"}}
2. historical:
{\"kind\":\"historical\",\"security\":{\"asset_class\":\"equity\",\"venue\":\"US|SSE|SZSE|HKEX\",\"local_code\":\"string\"},\"operation\":\"daily_bars|intraday_bars|corporate_actions|adjustment_factors|trading_calendar\",\"start_date\":\"YYYY-MM-DD\",\"end_date\":\"YYYY-MM-DD\"}
For dividends use operation `corporate_actions`; example:
{\"schema_version\":\"public-get.semantic/v1\",\"request\":{\"kind\":\"historical\",\"security\":{\"asset_class\":\"equity\",\"venue\":\"SSE\",\"local_code\":\"600519\"},\"operation\":\"corporate_actions\",\"start_date\":\"2024-01-01\",\"end_date\":\"2024-12-31\"}}
For trading days use operation `trading_calendar`; example:
{\"schema_version\":\"public-get.semantic/v1\",\"request\":{\"kind\":\"historical\",\"security\":{\"asset_class\":\"equity\",\"venue\":\"HKEX\",\"local_code\":\"00700\"},\"operation\":\"trading_calendar\",\"start_date\":\"2024-01-02\",\"end_date\":\"2024-01-05\"}}
3. financial statement:
{\"kind\":\"financial_statement\",\"security\":{\"asset_class\":\"equity\",\"venue\":\"US|SSE|SZSE|HKEX\",\"local_code\":\"string\"},\"statement\":{\"type\":\"income|balance|cash_flow\",\"presentation\":\"standardized|as_reported\",\"period\":{\"kind\":\"specified_period\",\"fiscal_year\":2024,\"fiscal_period\":\"FY|Q1|Q2|Q3|Q4\"},\"fields\":[\"field_name\"]}}

Classify only the ticker, venue, and requested operation from the user's words and the
enums above. Preserve the exchange-local code: strip only `.SH`, `.SZ`, or `.HK` when
the query supplies that suffix; never manufacture a ticker, venue, date, fiscal period,
field, price, or any other financial fact. If a required value is absent, use an empty
string only for `local_code`; do not invent a replacement."""
_HTTP_CODES = {400, 401, 402, 429, 503}
SEMANTIC_GATEWAY_ERROR_CODES = frozenset({
    "http_400", "http_401", "http_402", "http_429", "http_503", "http_other",
    "invalid_json", "completion_shape_invalid", "semantic_json_invalid",
    "semantic_schema_invalid", "usage_missing", "usage_invalid", "timeout",
    "response_too_large", "transport_error", "request_invalid", "request_too_large",
    "model_preflight_request_invalid", "model_preflight_http_400",
    "model_preflight_http_401", "model_preflight_http_402",
    "model_preflight_http_429", "model_preflight_http_503",
    "model_preflight_http_other", "model_preflight_timeout",
    "model_preflight_unavailable", "model_preflight_transport_failed",
    "model_preflight_http_invalid", "model_preflight_response_invalid",
    "internal_error",
})


class SemanticGatewayError(RuntimeError):
    """Stable failure code for the model boundary; never contains response content."""

    def __init__(self, code: str) -> None:
        self.code = code if code in SEMANTIC_GATEWAY_ERROR_CODES else "internal_error"
        super().__init__(self.code)


@dataclass(frozen=True)
class SemanticResolution:
    """Validated model semantics and the model-only usage receipt, if observable."""

    semantic: Mapping[str, Any]
    usage: Mapping[str, Any] | None


@dataclass(frozen=True)
class ModelGatewayPreflight:
    """Explicit, read-only model inventory; it never chooses a model."""

    configured_model: str
    available_model_ids: tuple[str, ...]


def _json(value: bytes, code: str) -> Any:
    def reject_constant(_: str) -> None:
        raise ValueError

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, child in pairs:
            if key in result:
                raise ValueError
            result[key] = child
        return result

    try:
        return json.loads(value.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise SemanticGatewayError(code) from exc


def _read(response: Any) -> bytes:
    value = response.read(MODEL_GATEWAY_MAX_RESPONSE_BYTES + 1)
    if type(value) is not bytes:
        raise SemanticGatewayError("invalid_json")
    if len(value) > MODEL_GATEWAY_MAX_RESPONSE_BYTES:
        raise SemanticGatewayError("response_too_large")
    return value


def _model_usage(value: Any, headers: Any, request_id: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise SemanticGatewayError("usage_missing")
    billing, usage = value.get("qveris_billing"), value.get("usage")
    if billing is None or usage is None:
        raise SemanticGatewayError("usage_missing")
    if type(billing) is not dict or billing.get("usage_estimated") is not False or type(usage) is not dict:
        raise SemanticGatewayError("usage_invalid")
    prompt, completion, total = usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")
    if any(type(count) is not int or isinstance(count, bool) or count < 0 for count in (prompt, completion, total)) or total != prompt + completion:
        raise SemanticGatewayError("usage_invalid")
    header_call_id = headers.get("X-QVeris-Call-ID") if headers is not None else None
    body_call_id = billing.get("call_id")
    if type(header_call_id) is not str or not header_call_id or type(body_call_id) is not str or not body_call_id or header_call_id != body_call_id:
        raise SemanticGatewayError("usage_invalid")
    return {
        # The raw call ID is private.  A digest preserves receipt binding without
        # making the provider's correlation identifier public.
        "receipt_id": sha256(header_call_id.encode("utf-8")).hexdigest(),
        "measurement_version": "qveris-model-gateway.chat-completions/v1",
        "cache_status": "not_reported",
        "request_id": request_id,
        "issuer": "qveris_model_gateway",
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": total,
    }


class QVerisModelGatewaySemanticResolver:
    """Fixed-model, non-streaming semantic resolver with no retry or fallback."""

    def __init__(self, *, api_key: str, model: str, base_url: str = MODEL_GATEWAY_BASE_URL, timeout_seconds: float = MODEL_GATEWAY_TIMEOUT_SECONDS, opener: Callable[[Request, float], Any] | None = None) -> None:
        if type(api_key) is not str or not api_key or type(model) is not str or not model:
            raise ValueError("QVeris model Gateway credentials and model are required")
        if type(base_url) is not str or not base_url.startswith("https://") or base_url.rstrip("/") != base_url:
            raise ValueError("base_url must be an HTTPS origin without a trailing slash")
        if type(timeout_seconds) not in {int, float} or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key, self._model = api_key, model
        self._base_url, self._url, self._timeout = base_url, base_url + MODEL_GATEWAY_CHAT_ENDPOINT, float(timeout_seconds)
        self._open = opener or (lambda request, timeout: urlopen(request, timeout=timeout))

    @classmethod
    def from_environment(cls) -> "QVerisModelGatewaySemanticResolver":
        api_key = os.environ.get("QVERIS_MODEL_GATEWAY_API_KEY")
        model = os.environ.get("QVERIS_MODEL_GATEWAY_MODEL")
        if not api_key or not model:
            raise ValueError("QVERIS_MODEL_GATEWAY_API_KEY and QVERIS_MODEL_GATEWAY_MODEL are required")
        return cls(api_key=api_key, model=model)

    def __call__(self, query: str, *, request_id: str) -> SemanticResolution:
        if type(query) is not str or not query or type(request_id) is not str or not request_id:
            raise SemanticGatewayError("request_invalid")
        payload = {
            "model": self._model,
            "stream": False,
            "temperature": 0,
            "max_tokens": MODEL_GATEWAY_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MODEL_GATEWAY_MAX_REQUEST_BYTES:
            raise SemanticGatewayError("request_too_large")
        request = Request(self._url, data=encoded, method="POST", headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json", "X-Request-ID": request_id, "X-Qveris-Source": "qveris-benchmark-public-get"})
        try:
            with self._open(request, self._timeout) as response:
                body, headers = _read(response), response.headers
        except SemanticGatewayError:
            raise
        except HTTPError as exc:
            try:
                _read(exc)
            except SemanticGatewayError:
                pass
            raise SemanticGatewayError("http_%d" % exc.code if exc.code in _HTTP_CODES else "http_other") from None
        except (socket.timeout, TimeoutError):
            raise SemanticGatewayError("timeout") from None
        except URLError:
            raise SemanticGatewayError("transport_error") from None
        except OSError:
            raise SemanticGatewayError("transport_error") from None
        response = _json(body, "invalid_json")
        if type(response) is not dict or type(response.get("choices")) is not list or len(response["choices"]) != 1:
            raise SemanticGatewayError("completion_shape_invalid")
        choice = response["choices"][0]
        if type(choice) is not dict or choice.get("finish_reason") != "stop" or type(choice.get("message")) is not dict or choice["message"].get("role") != "assistant" or type(choice["message"].get("content")) is not str:
            raise SemanticGatewayError("completion_shape_invalid")
        semantic = _json(choice["message"]["content"].encode("utf-8"), "semantic_json_invalid")
        # Keep model output validation at the same contract boundary as routing.
        from .public_get import SemanticRequestError, _validated_semantic
        try:
            _validated_semantic(semantic)
        except SemanticRequestError as exc:
            raise SemanticGatewayError("semantic_schema_invalid") from exc
        return SemanticResolution(semantic=semantic, usage=_model_usage(response, headers, request_id))

    def preflight_models(self, *, request_id: str) -> ModelGatewayPreflight:
        """List advertised model IDs once on explicit caller request.

        This deliberately does not run during construction or infer a replacement
        for ``configured_model``; GET runtime always uses that configured ID.
        """
        if type(request_id) is not str or not request_id:
            raise SemanticGatewayError("model_preflight_request_invalid")
        request = Request(
            self._base_url + MODEL_GATEWAY_MODELS_ENDPOINT,
            method="GET",
            headers={"Authorization": "Bearer " + self._api_key, "Accept": "application/json", "X-Request-ID": request_id, "X-Qveris-Source": "qveris-benchmark-public-get"},
        )
        try:
            with self._open(request, self._timeout) as response:
                status, body = getattr(response, "status", None), _read(response)
        except SemanticGatewayError:
            raise
        except HTTPError as exc:
            _read(exc)
            code = "model_preflight_http_%d" % exc.code if exc.code in _HTTP_CODES else "model_preflight_http_other"
            raise SemanticGatewayError(code) from None
        except (socket.timeout, TimeoutError):
            raise SemanticGatewayError("model_preflight_timeout") from None
        except URLError:
            raise SemanticGatewayError("model_preflight_unavailable") from None
        except OSError:
            raise SemanticGatewayError("model_preflight_transport_failed") from None
        if status != 200:
            raise SemanticGatewayError("model_preflight_http_invalid")
        response = _json(body, "model_preflight_response_invalid")
        if type(response) is not dict or type(response.get("data")) is not list:
            raise SemanticGatewayError("model_preflight_response_invalid")
        model_ids = []
        for item in response["data"]:
            if type(item) is not dict or type(item.get("id")) is not str or not item["id"]:
                raise SemanticGatewayError("model_preflight_response_invalid")
            model_ids.append(item["id"])
        if len(model_ids) != len(set(model_ids)):
            raise SemanticGatewayError("model_preflight_response_invalid")
        return ModelGatewayPreflight(self._model, tuple(sorted(model_ids)))


__all__ = [
    "MODEL_GATEWAY_BASE_URL", "MODEL_GATEWAY_CHAT_ENDPOINT", "MODEL_GATEWAY_MODELS_ENDPOINT",
    "MODEL_GATEWAY_MAX_REQUEST_BYTES", "MODEL_GATEWAY_MAX_RESPONSE_BYTES", "MODEL_GATEWAY_TIMEOUT_SECONDS", "MODEL_GATEWAY_MAX_TOKENS",
    "ModelGatewayPreflight", "QVerisModelGatewaySemanticResolver", "SEMANTIC_GATEWAY_ERROR_CODES", "SemanticGatewayError", "SemanticResolution",
]

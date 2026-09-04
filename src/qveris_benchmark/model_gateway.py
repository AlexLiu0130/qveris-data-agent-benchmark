"""Small, non-retrying client for the QVeris Model Gateway.

This module is deliberately below the public ``get`` boundary.  It speaks
only to the model Gateway and never constructs benchmark results, execution
evidence, or provider data responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from http.client import IncompleteRead
import json
import os
import re
import socket
import ssl
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .tls import verified_ssl_context


DEFAULT_BASE_URL = "https://aigateway.qveris.ai"
DEFAULT_SOURCE = "qveris-benchmark"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_MAX_REQUEST_BYTES = 1_048_576
_SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$")
_SAFE_FINISH_REASONS = frozenset({"stop", "length", "tool_calls"})


class _NoRedirect(HTTPRedirectHandler):
    """A redirect can leak auth headers and is a forbidden second request."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class ModelGatewayError(RuntimeError):
    """A safe Gateway failure; it never exposes a raw response or API key."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        call_id: str | None = None,
        retry_after: int | None = None,
        gateway_diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.call_id = call_id
        self.retry_after = retry_after
        self.gateway_diagnostic = None if gateway_diagnostic is None else dict(gateway_diagnostic)


class ModelGatewayTransportError(ModelGatewayError):
    """A timeout or transport failure after exactly one attempted request."""


class ModelGatewayProtocolError(ModelGatewayError):
    """A response that cannot safely be used by the benchmark runtime."""


class ModelGatewayHttpError(ModelGatewayError):
    """A Gateway non-2xx error envelope."""


@dataclass(frozen=True)
class GatewayUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class GatewayBilling:
    call_id: str
    credits_charged: float
    cost_usd: float
    usage_estimated: bool


@dataclass(frozen=True)
class GatewayModel:
    model_id: str


@dataclass(frozen=True)
class GatewayChatCompletion:
    status_code: int
    model_id: str
    request_id: str
    call_id: str
    retry_after: int | None
    content: str
    finish_reason: str | None
    usage: GatewayUsage | None
    billing: GatewayBilling

    @property
    def usage_estimated(self) -> bool:
        return self.billing.usage_estimated


def _safe_header(value: Any, name: str) -> str:
    if type(value) is not str or _SAFE_HEADER_VALUE.fullmatch(value) is None:
        raise ValueError("%s must be a safe opaque identifier of at most 128 characters" % name)
    return value


def _safe_model_id(value: Any) -> str:
    if type(value) is not str or _SAFE_MODEL_ID.fullmatch(value) is None:
        raise ValueError("model_id must be a safe Gateway model identifier")
    return value


def _safe_error_code(value: Any) -> str:
    try:
        return _safe_header(value, "Gateway error code")
    except ValueError as exc:
        raise ModelGatewayProtocolError("Gateway error response requires a safe error code", error_code="invalid_error_envelope") from exc


def _number(value: Any, field: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ModelGatewayProtocolError("%s must be a number" % field, error_code="invalid_billing")
    result = float(value)
    if result < 0 or result == float("inf") or result == float("-inf") or result != result:
        raise ModelGatewayProtocolError("%s must be a finite non-negative number" % field, error_code="invalid_billing")
    return result


def _token_count(value: Any, field: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise ModelGatewayProtocolError("usage.%s must be a non-negative integer" % field, error_code="invalid_usage")
    return value


def _temperature(value: Any) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ValueError("temperature must be a finite number from 0 to 2")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")) or not 0 <= result <= 2:
        raise ValueError("temperature must be a finite number from 0 to 2")
    return result


def _max_tokens(value: Any) -> int:
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 1_000_000:
        raise ValueError("max_tokens must be an integer from 1 to 1000000")
    return value


def _response_format(value: Any) -> dict[str, str] | None:
    if value is None or value == "text":
        return None
    if value == "json_object":
        return {"type": "json_object"}
    raise ValueError("response_format must be text or json_object")


def _finish_reason(value: Any) -> str | None:
    """Project only completion reasons the Runner can safely attest.

    Some lightweight test doubles omit this optional provider field.  Unknown
    provider additions are deliberately represented as unavailable rather than
    retained verbatim in a durable benchmark receipt.
    """
    return value if type(value) is str and value in _SAFE_FINISH_REASONS else None


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("request must be JSON serializable") from exc


def _normalize_base_url(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("base_url must be a string")
    parsed = urlsplit(value)
    hostname = parsed.hostname.lower() if parsed.hostname else None
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not hostname:
        raise ValueError("base_url must not contain credentials, query, or fragment")
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and hostname == "127.0.0.1":
        pass
    else:
        raise ValueError("base_url must use HTTPS (except explicit http://127.0.0.1 tests)")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("base_url has an invalid port")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _response_call_id(headers: Any) -> str | None:
    value = headers.get("X-Qveris-Call-ID") if headers is not None else None
    if value is None:
        return None
    try:
        return _safe_header(value, "X-Qveris-Call-ID")
    except ValueError as exc:
        raise ModelGatewayProtocolError("Gateway returned an invalid call ID", error_code="invalid_call_id") from exc


def _retry_after(headers: Any) -> int | None:
    value = headers.get("Retry-After") if headers is not None else None
    if value is None or not value.isdecimal():
        return None
    return int(value)


def _content_type_class(headers: Any) -> str:
    value = headers.get("Content-Type") if headers is not None else None
    if type(value) is not str or not value.strip():
        return "missing"
    media_type = value.split(";", 1)[0].strip().lower()
    if media_type == "application/json" or media_type.endswith("+json"):
        return "json"
    if media_type in {"text/html", "application/xhtml+xml"}:
        return "html"
    if media_type == "text/event-stream":
        return "sse"
    return "other"


def _content_encoding_class(headers: Any) -> str:
    value = headers.get("Content-Encoding") if headers is not None else None
    if type(value) is not str or not value.strip():
        return "missing"
    value = value.strip().lower()
    return value if value in {"identity", "gzip", "deflate", "br"} else "other"


def _charset_class(headers: Any) -> str:
    value = headers.get("Content-Type") if headers is not None else None
    if type(value) is not str or not value.strip():
        return "missing"
    matches = [item for item in value.split(";")[1:] if re.fullmatch(r"\s*charset\s*=.*", item, flags=re.IGNORECASE)]
    if not matches:
        return "missing"
    if len(matches) != 1:
        return "invalid"
    charset = matches[0].split("=", 1)[1].strip().strip('"\'').lower()
    if not re.fullmatch(r"[a-z0-9._-]+", charset):
        return "invalid"
    return "utf8" if charset in {"utf-8", "utf8"} else "non_utf8"


def _gateway_diagnostic(*, headers: Any, status_code: int | None, call_id: str | None, declared_body_bytes: int | None, observed_body_bytes: int, body_state: str, body_sha256: str | None) -> dict[str, Any]:
    """A fixed, body-free failure projection for a received HTTP response."""
    return {
        "http_status": status_code,
        "content_type_class": _content_type_class(headers),
        "content_encoding_class": _content_encoding_class(headers),
        "charset_class": _charset_class(headers),
        "declared_body_bytes": declared_body_bytes,
        "observed_body_bytes": observed_body_bytes,
        "body_state": body_state,
        "body_sha256": body_sha256,
        "call_id_sha256": None if call_id is None else sha256(call_id.encode("utf-8")).hexdigest(),
    }


def _json_object(raw: bytes, *, status_code: int | None, call_id: str | None, retry_after: int | None, diagnostic: Mapping[str, Any]) -> dict[str, Any]:
    if not raw:
        raise ModelGatewayProtocolError(
            "Gateway response body is empty",
            status_code=status_code,
            error_code="empty_body",
            call_id=call_id,
            retry_after=retry_after,
            gateway_diagnostic={**diagnostic, "body_state": "empty_body", "body_sha256": None},
        )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelGatewayProtocolError(
            "Gateway response is not valid UTF-8",
            status_code=status_code,
            error_code="invalid_utf8",
            call_id=call_id,
            retry_after=retry_after,
            gateway_diagnostic={**diagnostic, "body_state": "invalid_utf8", "body_sha256": sha256(raw).hexdigest()},
        ) from exc
    try:
        value = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ModelGatewayProtocolError(
            "Gateway response is not valid JSON",
            status_code=status_code,
            error_code="invalid_json",
            call_id=call_id,
            retry_after=retry_after,
            gateway_diagnostic={**diagnostic, "body_state": "invalid_json", "body_sha256": sha256(raw).hexdigest()},
        ) from exc
    if type(value) is not dict:
        raise ModelGatewayProtocolError(
            "Gateway response must be a JSON object",
            status_code=status_code,
            error_code="invalid_json_object",
            call_id=call_id,
            retry_after=retry_after,
            gateway_diagnostic={**diagnostic, "body_state": "invalid_json_object", "body_sha256": sha256(raw).hexdigest()},
        )
    return value


def _usage(payload: Mapping[str, Any]) -> GatewayUsage | None:
    value = payload.get("usage")
    if value is None:
        return None
    if type(value) is not dict:
        raise ModelGatewayProtocolError("usage must be an object", error_code="invalid_usage")
    keys = {"prompt_tokens", "completion_tokens", "total_tokens"}
    if not keys.issubset(value):
        raise ModelGatewayProtocolError("usage must include prompt, completion, and total tokens", error_code="invalid_usage")
    usage = GatewayUsage(
        input_tokens=_token_count(value["prompt_tokens"], "prompt_tokens"),
        output_tokens=_token_count(value["completion_tokens"], "completion_tokens"),
        total_tokens=_token_count(value["total_tokens"], "total_tokens"),
    )
    if usage.total_tokens != usage.input_tokens + usage.output_tokens:
        raise ModelGatewayProtocolError("usage total_tokens does not match prompt plus completion", error_code="invalid_usage")
    return usage


def _billing(payload: Mapping[str, Any], call_id: str | None) -> GatewayBilling:
    value = payload.get("qveris_billing")
    if type(value) is not dict:
        raise ModelGatewayProtocolError("Gateway completion requires qveris_billing", error_code="missing_billing", call_id=call_id)
    try:
        billing_call_id = _safe_header(value.get("call_id"), "qveris_billing.call_id")
    except ValueError as exc:
        raise ModelGatewayProtocolError("Gateway completion has an invalid billing call ID", error_code="invalid_billing", call_id=call_id) from exc
    if call_id is None:
        raise ModelGatewayProtocolError("Gateway completion requires X-Qveris-Call-ID", error_code="missing_call_id")
    if billing_call_id != call_id:
        raise ModelGatewayProtocolError("Gateway billing call ID does not match response header", error_code="billing_call_id_mismatch", call_id=call_id)
    if type(value.get("usage_estimated")) is not bool:
        raise ModelGatewayProtocolError("qveris_billing.usage_estimated must be boolean", error_code="invalid_billing", call_id=call_id)
    return GatewayBilling(
        call_id=billing_call_id,
        credits_charged=_number(value.get("credits_charged"), "qveris_billing.credits_charged"),
        cost_usd=_number(value.get("cost_usd"), "qveris_billing.cost_usd"),
        usage_estimated=value["usage_estimated"],
    )


class ModelGatewayClient:
    """Minimal Gateway boundary with no retries and no raw response retention."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        source: str = DEFAULT_SOURCE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        use_environment_proxy: bool = True,
        ssl_context: ssl.SSLContext | None = None,
        ca_file: str | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("QVERIS_API_KEY")
        if type(key) is not str or not key or "\r" in key or "\n" in key:
            raise ValueError("QVeris API key must be supplied explicitly or via QVERIS_API_KEY")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_response_bytes) is not int or isinstance(max_response_bytes, bool) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        if type(max_request_bytes) is not int or isinstance(max_request_bytes, bool) or max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be a positive integer")
        if type(use_environment_proxy) is not bool:
            raise ValueError("use_environment_proxy must be boolean")
        self._api_key = key
        self._base_url = _normalize_base_url(base_url)
        self._source = _safe_header(source, "source")
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._max_request_bytes = max_request_bytes
        self._listed_models: tuple[GatewayModel, ...] | None = None
        self._listed_model_ids: frozenset[str] = frozenset()
        self._ssl_context = verified_ssl_context(
            ssl_context=ssl_context,
            ca_file=ca_file,
            environment_ca_file="GATEWAY_CA_BUNDLE",
        )
        self._opener = build_opener(
            *(() if use_environment_proxy else (ProxyHandler({}),)),
            HTTPSHandler(context=self._ssl_context),
            _NoRedirect(),
        )

    def __repr__(self) -> str:
        return "ModelGatewayClient(base_url=%r, source=%r)" % (self._base_url, self._source)

    def _url(self, path: str) -> str:
        return self._base_url + path

    def _read_limited(self, response: Any, *, status_code: int | None, call_id: str | None, retry_after: int | None) -> tuple[bytes, dict[str, Any]]:
        length = response.headers.get("Content-Length")
        declared_length: int | None = None
        if length is not None:
            try:
                declared_length = int(length)
                if declared_length < 0:
                    raise ValueError()
            except ValueError as exc:
                raise ModelGatewayProtocolError("Gateway returned an invalid Content-Length", status_code=status_code, error_code="invalid_content_length", call_id=call_id, retry_after=retry_after, gateway_diagnostic=_gateway_diagnostic(headers=response.headers, status_code=status_code, call_id=call_id, declared_body_bytes=None, observed_body_bytes=0, body_state="invalid_content_length", body_sha256=None)) from exc
            if declared_length > self._max_response_bytes:
                raise ModelGatewayProtocolError("Gateway response exceeds configured size limit", status_code=status_code, error_code="response_too_large", call_id=call_id, retry_after=retry_after, gateway_diagnostic=_gateway_diagnostic(headers=response.headers, status_code=status_code, call_id=call_id, declared_body_bytes=declared_length, observed_body_bytes=0, body_state="response_too_large", body_sha256=None))
        try:
            raw = response.read(self._max_response_bytes + 1)
        except IncompleteRead as exc:
            partial = exc.partial if type(exc.partial) is bytes else b""
            raise ModelGatewayProtocolError("Gateway response body is incomplete", status_code=status_code, error_code="response_truncated", call_id=call_id, retry_after=retry_after, gateway_diagnostic=_gateway_diagnostic(headers=response.headers, status_code=status_code, call_id=call_id, declared_body_bytes=declared_length, observed_body_bytes=len(partial), body_state="response_truncated", body_sha256=None)) from exc
        if len(raw) > self._max_response_bytes:
            raise ModelGatewayProtocolError("Gateway response exceeds configured size limit", status_code=status_code, error_code="response_too_large", call_id=call_id, retry_after=retry_after, gateway_diagnostic=_gateway_diagnostic(headers=response.headers, status_code=status_code, call_id=call_id, declared_body_bytes=declared_length, observed_body_bytes=len(raw), body_state="response_too_large", body_sha256=None))
        return raw, _gateway_diagnostic(headers=response.headers, status_code=status_code, call_id=call_id, declared_body_bytes=declared_length, observed_body_bytes=len(raw), body_state="unclassified", body_sha256=None)

    def _request(self, method: str, path: str, *, request_id: str, payload: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any], str | None, int | None]:
        request_id = _safe_header(request_id, "request_id")
        data = _canonical_json(payload) if payload is not None else None
        if data is not None and len(data) > self._max_request_bytes:
            raise ValueError("request exceeds configured size limit")
        headers = {
            "Authorization": "Bearer " + self._api_key,
            "Accept": "application/json",
            "X-Request-ID": request_id,
            "X-Qveris-Source": self._source,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                call_id = _response_call_id(response.headers)
                retry_after = _retry_after(response.headers)
                raw, diagnostic = self._read_limited(response, status_code=response.status, call_id=call_id, retry_after=retry_after)
                return response.status, _json_object(raw, status_code=response.status, call_id=call_id, retry_after=retry_after, diagnostic=diagnostic), call_id, retry_after
        except HTTPError as exc:
            call_id = _response_call_id(exc.headers)
            retry_after = _retry_after(exc.headers)
            raw, diagnostic = self._read_limited(exc, status_code=exc.code, call_id=call_id, retry_after=retry_after)
            payload = _json_object(raw, status_code=exc.code, call_id=call_id, retry_after=retry_after, diagnostic=diagnostic)
            error = payload.get("error")
            if type(error) is not dict:
                raise ModelGatewayProtocolError("Gateway error response requires an error object", status_code=exc.code, error_code="invalid_error_envelope", call_id=call_id, retry_after=retry_after)
            try:
                code = _safe_error_code(error.get("code"))
            except ModelGatewayProtocolError as protocol_error:
                raise ModelGatewayProtocolError(str(protocol_error), status_code=exc.code, error_code="invalid_error_envelope", call_id=call_id, retry_after=retry_after) from None
            raise ModelGatewayHttpError("Gateway request failed", status_code=exc.code, error_code=code, call_id=call_id, retry_after=retry_after) from None
        except (TimeoutError, socket.timeout) as exc:
            raise ModelGatewayTransportError("Gateway request timed out", error_code="timeout") from exc
        except URLError as exc:
            raise ModelGatewayTransportError("Gateway transport failed", error_code="transport_error") from exc

    def list_models(self, *, request_id: str) -> tuple[GatewayModel, ...]:
        """List and freeze the externally advertised Gateway model IDs for this client."""
        if self._listed_models is not None:
            return self._listed_models
        status, payload, _call_id, _retry_after = self._request("GET", "/v1/models", request_id=request_id)
        if status != 200 or type(payload.get("data")) is not list:
            raise ModelGatewayProtocolError("Gateway models response requires a data array", status_code=status, error_code="invalid_models_response")
        model_ids: list[str] = []
        for item in payload["data"]:
            if type(item) is not dict:
                raise ModelGatewayProtocolError("Gateway models response contains an invalid model", status_code=status, error_code="invalid_models_response")
            try:
                model_id = _safe_model_id(item.get("id"))
            except ValueError as exc:
                raise ModelGatewayProtocolError("Gateway models response contains an invalid model ID", status_code=status, error_code="invalid_models_response") from exc
            model_ids.append(model_id)
        if len(model_ids) != len(set(model_ids)):
            raise ModelGatewayProtocolError("Gateway models response contains duplicate model IDs", status_code=status, error_code="invalid_models_response")
        self._listed_models = tuple(GatewayModel(model_id=item) for item in model_ids)
        self._listed_model_ids = frozenset(model_ids)
        return self._listed_models

    def chat_completions(
        self,
        *,
        model_id: str,
        messages: Sequence[Mapping[str, Any]],
        request_id: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: str | None = None,
    ) -> GatewayChatCompletion:
        """Make exactly one non-streaming chat-completions request; never retries."""
        model_id = _safe_model_id(model_id)
        if model_id not in self._listed_model_ids:
            raise ValueError("model_id must be selected from this client's list_models result")
        if type(messages) not in (list, tuple) or not messages:
            raise ValueError("messages must be a non-empty sequence")
        normalized_messages: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, Mapping) or type(item.get("role")) is not str or type(item.get("content")) is not str:
                raise ValueError("each message requires string role and content")
            normalized_messages.append({"role": item["role"], "content": item["content"]})
        request_payload: dict[str, Any] = {"model": model_id, "messages": normalized_messages, "stream": False}
        if temperature is not None:
            request_payload["temperature"] = _temperature(temperature)
        if max_tokens is not None:
            request_payload["max_tokens"] = _max_tokens(max_tokens)
        json_format = _response_format(response_format)
        if json_format is not None:
            request_payload["response_format"] = json_format
        status, payload, call_id, _retry_after = self._request(
            "POST",
            "/v1/chat/completions",
            request_id=request_id,
            payload=request_payload,
        )
        if status != 200:
            raise ModelGatewayProtocolError("Gateway completion returned an unexpected status", status_code=status, error_code="unexpected_status", call_id=call_id)
        try:
            response_model_id = _safe_model_id(payload.get("model"))
        except ValueError as exc:
            raise ModelGatewayProtocolError(
                "Gateway completion requires a safe response model ID",
                status_code=status,
                error_code="missing_response_model",
                call_id=call_id,
            ) from exc
        if response_model_id != model_id:
            raise ModelGatewayProtocolError(
                "Gateway completion model ID does not match the requested model",
                status_code=status,
                error_code="response_model_mismatch",
                call_id=call_id,
            )
        try:
            billing = _billing(payload, call_id)
        except ModelGatewayProtocolError as exc:
            raise ModelGatewayProtocolError(str(exc), status_code=status, error_code=exc.error_code, call_id=exc.call_id or call_id) from None
        if call_id is None:
            raise ModelGatewayProtocolError("Gateway completion requires X-Qveris-Call-ID", error_code="missing_call_id")
        choices = payload.get("choices")
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ModelGatewayProtocolError("Gateway completion requires exactly one choice", status_code=status, error_code="invalid_completion", call_id=call_id)
        message = choices[0].get("message")
        if type(message) is not dict or type(message.get("content")) is not str:
            raise ModelGatewayProtocolError("Gateway completion choice requires string message content", status_code=status, error_code="invalid_completion", call_id=call_id)
        finish_reason = _finish_reason(choices[0].get("finish_reason"))
        try:
            usage = _usage(payload)
        except ModelGatewayProtocolError as exc:
            raise ModelGatewayProtocolError(str(exc), status_code=status, error_code=exc.error_code, call_id=call_id) from None
        return GatewayChatCompletion(
            status_code=status,
            model_id=response_model_id,
            request_id=_safe_header(request_id, "request_id"),
            call_id=call_id,
            retry_after=_retry_after,
            content=message["content"],
            finish_reason=finish_reason,
            usage=usage,
            billing=billing,
        )

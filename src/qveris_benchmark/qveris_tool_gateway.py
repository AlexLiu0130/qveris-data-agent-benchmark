"""One-call QVeris Tool Gateway client for the fixed public-GET routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_GATEWAY_BASE_URL = "https://qveris.ai/api/v1"
TOOL_GATEWAY_MAX_REQUEST_BYTES = 64 * 1024
TOOL_GATEWAY_MAX_RESPONSE_BYTES = 1024 * 1024
TOOL_GATEWAY_TIMEOUT_SECONDS = 15.0
_HTTP_CODES = {400, 401, 402, 429, 503}
TOOL_GATEWAY_ERROR_CODES = frozenset({
    "http_400", "http_401", "http_402", "http_429", "http_503", "http_other",
    "invalid_json", "response_shape_invalid", "timeout", "response_too_large",
    "transport_error", "request_invalid", "request_too_large", "rejected",
    "receipt_record_failed", "internal_error",
})


class ToolGatewayError(RuntimeError):
    """Stable, non-sensitive failure at the QVeris Tool boundary."""

    def __init__(self, code: str) -> None:
        self.code = code if code in TOOL_GATEWAY_ERROR_CODES else "internal_error"
        super().__init__(self.code)


@dataclass(frozen=True)
class ToolCreditReceipt:
    """Private cost observation; without a server execution ID it is not proof."""

    tool_id: str
    request_id: str
    execution_id: str | None
    actual_credits: int | float | None
    correlation_id: str
    server_correlated: bool


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
        raise ToolGatewayError(code) from exc


def _read(response: Any) -> bytes:
    value = response.read(TOOL_GATEWAY_MAX_RESPONSE_BYTES + 1)
    if type(value) is not bytes:
        raise ToolGatewayError("invalid_json")
    if len(value) > TOOL_GATEWAY_MAX_RESPONSE_BYTES:
        raise ToolGatewayError("response_too_large")
    return value


def _credit(value: Any) -> int | float | None:
    if type(value) in {int, float} and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _failure_error(value: Any) -> bool:
    return (type(value) is str and bool(value.strip())) or (type(value) is dict and bool(value))


class QVerisToolGateway:
    """HTTPS-only Tool Execute callable for ``PublicGetAdapter.gateway_execute``.

    The supplied receipt sink is the separate private channel for Tool credits.
    It never affects model usage or the public GET response.
    """

    def __init__(self, *, api_key: str, timeout_seconds: float = TOOL_GATEWAY_TIMEOUT_SECONDS, receipt_sink: Callable[[ToolCreditReceipt], None] | None = None, opener: Callable[[Request, float], Any] | None = None) -> None:
        if type(api_key) is not str or not api_key:
            raise ValueError("QVeris Tool Gateway API key is required")
        if type(timeout_seconds) not in {int, float} or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key, self._timeout = api_key, float(timeout_seconds)
        self._receipt_sink = receipt_sink
        self._open = opener or (lambda request, timeout: urlopen(request, timeout=timeout))

    @classmethod
    def from_environment(cls, **kwargs: Any) -> "QVerisToolGateway":
        api_key = os.environ.get("QVERIS_API_KEY")
        if not api_key:
            raise ValueError("QVERIS_API_KEY is required")
        return cls(api_key=api_key, **kwargs)

    def __call__(self, tool_id: str, parameters: Mapping[str, Any], *, request_id: str, idempotency_key: str) -> Mapping[str, Any]:
        if type(tool_id) is not str or not tool_id or type(parameters) is not dict or type(request_id) is not str or not request_id or type(idempotency_key) is not str or not idempotency_key:
            raise ToolGatewayError("request_invalid")
        try:
            encoded = json.dumps({"parameters": parameters}, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolGatewayError("request_invalid") from exc
        if len(encoded) > TOOL_GATEWAY_MAX_REQUEST_BYTES:
            raise ToolGatewayError("request_too_large")
        request = Request(
            TOOL_GATEWAY_BASE_URL + "/tools/execute?" + urlencode({"tool_id": tool_id}),
            data=encoded,
            method="POST",
            headers={"Accept": "application/json", "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json", "Idempotency-Key": idempotency_key, "X-Request-ID": request_id},
        )
        try:
            with self._open(request, self._timeout) as response:
                status, body = getattr(response, "status", None), _read(response)
        except ToolGatewayError:
            raise
        except HTTPError as exc:
            _read(exc)
            raise ToolGatewayError("http_%d" % exc.code if exc.code in _HTTP_CODES else "http_other") from None
        except (socket.timeout, TimeoutError):
            raise ToolGatewayError("timeout") from None
        except URLError:
            raise ToolGatewayError("transport_error") from None
        except OSError:
            raise ToolGatewayError("transport_error") from None
        if status != 200:
            raise ToolGatewayError("http_other")
        response = _json(body, "invalid_json")
        if type(response) is not dict or type(response.get("success")) is not bool:
            raise ToolGatewayError("response_shape_invalid")
        if not response["success"]:
            if not _failure_error(response.get("error")):
                raise ToolGatewayError("response_shape_invalid")
            raise ToolGatewayError("rejected")
        if response.get("error") is not None:
            raise ToolGatewayError("response_shape_invalid")
        result = response.get("result")
        if type(result) is not dict or "data" not in result or result["data"] is None:
            raise ToolGatewayError("response_shape_invalid")
        as_of = response.get("as_of")
        if as_of is None:
            as_of = result.get("as_of")
        if as_of is not None and (type(as_of) is not str or not as_of):
            raise ToolGatewayError("response_shape_invalid")
        execution_id = response.get("execution_id")
        if execution_id is not None and (type(execution_id) is not str or not execution_id):
            raise ToolGatewayError("response_shape_invalid")
        receipt = ToolCreditReceipt(tool_id, request_id, execution_id, _credit(response.get("actual_credits", response.get("cost"))), request_id, execution_id is not None)
        if self._receipt_sink is not None:
            try:
                self._receipt_sink(receipt)
            except Exception as exc:
                raise ToolGatewayError("receipt_record_failed") from exc
        # This private envelope is consumed by PublicGetAdapter; no receipt or raw
        # payload is copied into its public response.
        return {"raw": result["data"], "as_of": as_of}


__all__ = [
    "TOOL_GATEWAY_BASE_URL", "TOOL_GATEWAY_MAX_REQUEST_BYTES", "TOOL_GATEWAY_MAX_RESPONSE_BYTES", "TOOL_GATEWAY_TIMEOUT_SECONDS",
    "QVerisToolGateway", "TOOL_GATEWAY_ERROR_CODES", "ToolCreditReceipt", "ToolGatewayError",
]

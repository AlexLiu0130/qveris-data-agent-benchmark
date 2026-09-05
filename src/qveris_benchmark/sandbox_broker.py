"""Narrow host-side HTTPS broker for a network-disabled public-GET sandbox."""

from __future__ import annotations

import base64
import json
import os
import socket
from collections.abc import Callable, Mapping
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from . import domain_routes_financial, domain_routes_historical, domain_routes_realtime
from .domain_route_contract import RoutePlan
from .public_get import _domain_module, _validated_semantic
from .qveris_model_gateway import MODEL_GATEWAY_BASE_URL, MODEL_GATEWAY_CHAT_ENDPOINT, MODEL_GATEWAY_MAX_REQUEST_BYTES, MODEL_GATEWAY_MAX_RESPONSE_BYTES, MODEL_GATEWAY_MAX_TOKENS, MODEL_GATEWAY_TIMEOUT_SECONDS, SemanticGatewayError, _semantic_json
from .qveris_tool_gateway import QVerisToolGateway, TOOL_GATEWAY_BASE_URL, TOOL_GATEWAY_MAX_REQUEST_BYTES, TOOL_GATEWAY_MAX_RESPONSE_BYTES, TOOL_GATEWAY_TIMEOUT_SECONDS, ToolGatewayError, _ALPHA_POINTER_TOOL_IDS, _alpha_pointer_url, _validate_alpha_download_url
from .tls import DirectHTTPSOpener


SCHEMA_VERSION = "sandbox-http-broker/v1"
_ID = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_BODY = 1024 * 1024

def _tool_ids() -> frozenset[str]:
    """Load every frozen domain tool ID; never accept a sandbox-selected ID."""
    values = (
        *domain_routes_realtime.SUPPORTED_KEYS.values(),
        *domain_routes_historical.SUPPORTED_KEYS.values(),
        *domain_routes_financial.SUPPORTED_KEYS.values(),
    )
    return frozenset(tool_id for value in values for tool_id in (value if type(value) is tuple else (value,)))


_TOOL_IDS = _tool_ids()


class SandboxBrokerError(ValueError):
    """A malformed or disallowed sandbox broker frame."""


class _Response:
    def __init__(self, status: int, body: bytes, headers: Mapping[str, str]) -> None:
        self.status, self._body, self.headers = status, body, dict(headers)

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


def _safe_id(value: Any, field: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise SandboxBrokerError(field + " is invalid")
    return value


def _body(value: Any) -> bytes:
    if type(value) is not str:
        raise SandboxBrokerError("body_b64 is invalid")
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise SandboxBrokerError("body_b64 is invalid") from exc
    if len(result) > _MAX_BODY:
        raise SandboxBrokerError("body is too large")
    return result


def _headers(value: Any) -> dict[str, str]:
    if type(value) is not dict:
        raise SandboxBrokerError("headers are invalid")
    result = {}
    for key, item in value.items():
        if type(key) is not str or type(item) is not str or not key or "\r" in key + item or "\n" in key + item:
            raise SandboxBrokerError("headers are invalid")
        normalized = key.lower()
        if normalized in result:
            raise SandboxBrokerError("headers are invalid")
        result[normalized] = item
    return result


def _frame(value: Any, request_id: str) -> tuple[str, str, str, dict[str, str], bytes, int]:
    fields = {"schema_version", "kind", "request_id", "method", "url", "headers", "body_b64", "timeout_ms"}
    if type(value) is not dict or set(value) != fields or value.get("schema_version") != SCHEMA_VERSION:
        raise SandboxBrokerError("broker request schema is invalid")
    if _safe_id(value["request_id"], "request_id") != request_id or value["kind"] not in {"model", "tool", "result_download"} or type(value["url"]) is not str:
        raise SandboxBrokerError("broker request is invalid")
    if (value["kind"] in {"model", "tool"} and value["method"] != "POST") or (value["kind"] == "result_download" and value["method"] != "GET"):
        raise SandboxBrokerError("broker request is invalid")
    timeout = value["timeout_ms"]
    if type(timeout) is not int or isinstance(timeout, bool) or timeout <= 0:
        raise SandboxBrokerError("timeout_ms is invalid")
    return value["kind"], value["url"], value["method"], _headers(value["headers"]), _body(value["body_b64"]), timeout


def _request_headers(request: Request) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in request.header_items():
        if key.lower() == "authorization":
            continue
        values[key] = value
    return values


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise SandboxBrokerError("request body is invalid") from exc
    if type(value) is not dict:
        raise SandboxBrokerError("request body is invalid")
    return value


def _expected_plan(body: bytes) -> RoutePlan | None:
    """Mirror the fixed semantic-to-route path before a sandbox may execute it."""
    try:
        response = _json_object(body)
        choices = response["choices"]
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            return None
        message = choices[0]["message"]
        if choices[0].get("finish_reason") != "stop" or type(message) is not dict or message.get("role") != "assistant" or type(message.get("content")) is not str:
            return None
        canonical, _venue, _operation, _symbol, _details = _validated_semantic(_semantic_json(message["content"]))
        plan = _domain_module(canonical).resolve(canonical)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, SemanticGatewayError):
        return None
    return plan if type(plan) is RoutePlan and plan.tool_id in _TOOL_IDS else None


class SandboxBrokerOpener:
    """Sandbox-side ``urllib`` opener that turns one Request into one JSONL RPC."""

    def __init__(self, request_id: str, input_stream: Any, output_stream: Any) -> None:
        self.request_id, self.input_stream, self.output_stream = _safe_id(request_id, "request_id"), input_stream, output_stream

    def __call__(self, request: Request, timeout: float) -> _Response:
        if type(timeout) not in (int, float) or timeout <= 0 or timeout > 60:
            raise URLError("transport_error")
        url = request.full_url
        kind = "model" if url == MODEL_GATEWAY_BASE_URL + MODEL_GATEWAY_CHAT_ENDPOINT else "result_download" if request.get_method() == "GET" else "tool"
        frame = {
            "schema_version": SCHEMA_VERSION, "kind": kind, "request_id": self.request_id,
            "method": request.get_method(), "url": url, "headers": _request_headers(request),
            "body_b64": base64.b64encode(request.data or b"").decode("ascii"), "timeout_ms": int(timeout * 1000),
        }
        self.output_stream.write(json.dumps(frame, separators=(",", ":"), sort_keys=True) + "\n")
        self.output_stream.flush()
        raw = self.input_stream.readline()
        try:
            reply = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise URLError("transport_error") from exc
        if type(reply) is not dict or reply.get("schema_version") != SCHEMA_VERSION or reply.get("request_id") != self.request_id:
            raise URLError("transport_error")
        if "error" in reply:
            if reply.get("error") == "timeout":
                raise socket.timeout()
            raise URLError("transport_error")
        if set(reply) != {"schema_version", "request_id", "status", "headers", "body_b64"} or type(reply["status"]) is not int or not 100 <= reply["status"] <= 599:
            raise URLError("transport_error")
        body, headers = _body(reply["body_b64"]), _headers(reply["headers"])
        public_headers = {"X-QVeris-Call-ID": headers["x-qveris-call-id"]} if "x-qveris-call-id" in headers else {}
        if reply["status"] >= 400:
            raise HTTPError(url, reply["status"], "brokered HTTP error", public_headers, BytesIO(body))
        return _Response(reply["status"], body, public_headers)


class SandboxBroker:
    """Host-owned, fixed-destination broker. It never returns secret request headers."""

    def __init__(self, request_id: str, *, query: str | None = None, model_identifier: str | None = None, model_api_key: str | None = None, tool_api_key: str | None = None, model_opener: Callable[[Request, float], Any] | None = None, tool_opener: Callable[[Request, float], Any] | None = None, result_download_opener: Callable[[Request, float], Any] | None = None) -> None:
        self.request_id = _safe_id(request_id, "request_id")
        if query is not None and (type(query) is not str or not query):
            raise SandboxBrokerError("query is invalid")
        self.query = query
        self.model_identifier = None if model_identifier is None else _safe_id(model_identifier, "model_identifier")
        self.model_api_key, self.tool_api_key = model_api_key, tool_api_key
        self.model_opener, self.tool_opener = model_opener, tool_opener
        self._pointer_downloader = QVerisToolGateway(api_key=tool_api_key or "sandbox-broker", timeout_seconds=TOOL_GATEWAY_TIMEOUT_SECONDS, download_opener=result_download_opener)
        self._expected_tool: RoutePlan | None = None
        self._pointer_url: str | None = None
        self.model_dispatches = self.model_completions = 0
        self.tool_dispatches = self.tool_completions = 0
        self.result_download_dispatches = self.result_download_completions = 0

    @classmethod
    def from_environment(cls, request_id: str, *, query: str | None = None, model_identifier: str | None = None, environ: Mapping[str, str] | None = None, model_opener: Callable[[Request, float], Any] | None = None, tool_opener: Callable[[Request, float], Any] | None = None) -> "SandboxBroker":
        values = os.environ if environ is None else environ
        return cls(
            request_id,
            query=query,
            model_identifier=model_identifier,
            model_api_key=values.get("QVERIS_MODEL_GATEWAY_API_KEY"),
            tool_api_key=values.get("QVERIS_API_KEY"),
            model_opener=model_opener or DirectHTTPSOpener(ssl_context=None, ca_file=None, environment_ca_file="QVERIS_MODEL_GATEWAY_CA_BUNDLE"),
            tool_opener=tool_opener or DirectHTTPSOpener(ssl_context=None, ca_file=None, environment_ca_file="QVERIS_TOOL_GATEWAY_CA_BUNDLE"),
        )

    def reply(self, value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            kind, url, _method, headers, body, timeout = _frame(value, self.request_id)
            if kind == "result_download":
                if self.result_download_dispatches or self._pointer_url is None or url != self._pointer_url or headers != {"accept": "application/json"} or body or timeout != int(TOOL_GATEWAY_TIMEOUT_SECONDS * 1000):
                    raise SandboxBrokerError("result download route is denied")
                self.result_download_dispatches += 1
                try:
                    downloaded = self._pointer_downloader._download_alpha_pointer(url)
                    response_body = json.dumps(downloaded, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                except Exception:
                    return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "error": "transport_error"}
                self.result_download_completions += 1
                return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "status": 200, "headers": {}, "body_b64": base64.b64encode(response_body).decode("ascii")}
            if kind == "model":
                payload = _json_object(body)
                messages = payload.get("messages")
                if self.model_dispatches or len(body) > MODEL_GATEWAY_MAX_REQUEST_BYTES or self.model_identifier is None or self.query is None or url != MODEL_GATEWAY_BASE_URL + MODEL_GATEWAY_CHAT_ENDPOINT or headers != {"content-type": "application/json", "x-request-id": self.request_id, "x-qveris-source": "qveris-benchmark-public-get"} or timeout != int(MODEL_GATEWAY_TIMEOUT_SECONDS * 1000) or payload.get("model") != self.model_identifier or payload.get("stream") is not False or payload.get("temperature") != 0 or payload.get("max_tokens") != MODEL_GATEWAY_MAX_TOKENS or type(messages) is not list or len(messages) != 2 or any(type(message) is not dict for message in messages) or messages[0].get("role") != "system" or type(messages[0].get("content")) is not str or not messages[0]["content"] or messages[1] != {"role": "user", "content": self.query}:
                    raise SandboxBrokerError("model route is denied")
                key, opener = self.model_api_key, self.model_opener
                outgoing_headers = {"Authorization": "Bearer " + (key or ""), "Content-Type": "application/json", "X-Request-ID": self.request_id, "X-Qveris-Source": "qveris-benchmark-public-get"}
            else:
                parsed = urlsplit(url)
                query = parse_qs(parsed.query, keep_blank_values=True)
                tool_id = query.get("tool_id", [None])[0]
                expected = {"accept": "application/json", "content-type": "application/json", "idempotency-key": headers.get("idempotency-key"), "x-request-id": self.request_id}
                if self.tool_dispatches or self.model_completions != 1 or self._expected_tool is None or len(body) > TOOL_GATEWAY_MAX_REQUEST_BYTES or (parsed.scheme, parsed.netloc, parsed.path, query) != ("https", "qveris.ai", "/api/v1/tools/execute", {"tool_id": [tool_id]}) or tool_id != self._expected_tool.tool_id or tool_id not in _TOOL_IDS or headers != expected or headers["idempotency-key"] != "idem-" + self.request_id or _json_object(body) != {"parameters": self._expected_tool.params} or timeout != int(TOOL_GATEWAY_TIMEOUT_SECONDS * 1000):
                    raise SandboxBrokerError("tool route is denied")
                key, opener = self.tool_api_key, self.tool_opener
                outgoing_headers = {"Authorization": "Bearer " + (key or ""), "Accept": "application/json", "Content-Type": "application/json", "Idempotency-Key": headers["idempotency-key"], "X-Request-ID": self.request_id}
            if type(key) is not str or not key or opener is None:
                return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "error": "credential_unavailable"}
            request = Request(url, data=body, method="POST", headers=outgoing_headers)
            if kind == "model":
                self.model_dispatches += 1
            else:
                self.tool_dispatches += 1
            try:
                with opener(request, timeout / 1000) as response:
                    status, response_body = getattr(response, "status", 200), response.read(_MAX_BODY + 1)
                    response_headers = getattr(response, "headers", {})
            except HTTPError as exc:
                status, response_body, response_headers = exc.code, exc.read(_MAX_BODY + 1), exc.headers or {}
            except (socket.timeout, TimeoutError):
                return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "error": "timeout"}
            except (OSError, URLError):
                return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "error": "transport_error"}
            response_limit = MODEL_GATEWAY_MAX_RESPONSE_BYTES if kind == "model" else TOOL_GATEWAY_MAX_RESPONSE_BYTES
            if type(response_body) is not bytes or len(response_body) > response_limit or type(status) is not int or not 100 <= status <= 599:
                return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "error": "transport_error"}
            if kind == "model" and 200 <= status < 300:
                self.model_completions += 1
                self._expected_tool = _expected_plan(response_body)
            elif kind == "tool":
                self.tool_completions += 1
                if status == 200 and tool_id in _ALPHA_POINTER_TOOL_IDS:
                    try:
                        tool_response = _json_object(response_body)
                        result = tool_response.get("result") if tool_response.get("success") is True else None
                        pointer = _alpha_pointer_url(result) or (_alpha_pointer_url(result.get("data")) if type(result) is dict else None)
                        if pointer is not None:
                            _validate_alpha_download_url(pointer)
                        self._pointer_url = pointer
                    except (SandboxBrokerError, ToolGatewayError):
                        self._pointer_url = None
            call_id = response_headers.get("X-QVeris-Call-ID") if hasattr(response_headers, "get") else None
            returned_headers = {"X-QVeris-Call-ID": call_id} if type(call_id) is str and call_id else {}
            return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "status": status, "headers": returned_headers, "body_b64": base64.b64encode(response_body).decode("ascii")}
        except SandboxBrokerError:
            return {"schema_version": SCHEMA_VERSION, "request_id": self.request_id, "error": "route_denied"}

    def observations(self) -> dict[str, int]:
        """Host-observed broker traffic; not a provider-signed execution receipt."""
        return {
            "model_dispatches": self.model_dispatches, "model_completions": self.model_completions,
            "tool_dispatches": self.tool_dispatches, "tool_completions": self.tool_completions,
            "result_download_dispatches": self.result_download_dispatches, "result_download_completions": self.result_download_completions,
        }


__all__ = ["SCHEMA_VERSION", "SandboxBroker", "SandboxBrokerError", "SandboxBrokerOpener"]

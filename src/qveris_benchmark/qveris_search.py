"""Small, one-shot client for exploratory QVeris Search/Inspect/Execute.

This is an exploratory boundary only.  It does not construct a public ``get``
response or join the benchmark Runner, Scorer, Arena, or ranking paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import socket
import ssl
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .tls import verified_ssl_context


DEFAULT_BASE_URL = "https://qveris.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_REQUEST_BYTES = 65_536
DEFAULT_MAX_RESPONSE_BYTES = 262_144
_OFFICIAL_BASES = frozenset({"https://qveris.ai/api/v1", "https://qveris.cn/api/v1"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class QVerisSearchError(RuntimeError):
    """A safe search failure; no raw response or key is retained."""

    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None, call_id: str | None = None) -> None:
        super().__init__(message)
        self.status_code, self.error_code, self.call_id = status_code, error_code, call_id


class QVerisSearchTransportError(QVerisSearchError):
    pass


class QVerisSearchProtocolError(QVerisSearchError):
    pass


class QVerisSearchHttpError(QVerisSearchError):
    pass


@dataclass(frozen=True)
class SearchTool:
    tool_id: str
    name: str
    description: str
    params: tuple[dict[str, Any], ...] | None
    expected_cost: str | None
    billing_rule: dict[str, Any] | None


@dataclass(frozen=True)
class SearchCatalog:
    search_id: str
    results: tuple[SearchTool, ...]
    remaining_credits: float | None
    call_id: str | None


@dataclass(frozen=True)
class ToolInspection:
    tool: SearchTool
    remaining_credits: float | None
    call_id: str | None


@dataclass(frozen=True)
class ToolExecution:
    tool_id: str
    execution_id: str
    cost: float
    remaining_credits: float | None
    result: Any
    call_id: str | None


def _safe_id(value: Any, field: str) -> str:
    if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
        raise ValueError("%s must be a safe opaque identifier" % field)
    return value


def _finite_nonnegative(value: Any, field: str) -> float:
    if type(value) is str:
        try:
            value = float(value.strip())
        except ValueError as exc:
            raise QVerisSearchProtocolError("%s must be a finite non-negative number" % field, error_code="invalid_response") from exc
    if type(value) not in (int, float) or isinstance(value, bool):
        raise QVerisSearchProtocolError("%s must be a finite non-negative number" % field, error_code="invalid_response")
    result = float(value)
    if result < 0 or result != result or result in (float("inf"), float("-inf")):
        raise QVerisSearchProtocolError("%s must be a finite non-negative number" % field, error_code="invalid_response")
    return result


def _canonical_json(value: Any, *, field: str) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be JSON serializable" % field) from exc


def _json_object(raw: bytes, *, status_code: int | None, call_id: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QVerisSearchProtocolError("search response is not valid JSON", status_code=status_code, error_code="invalid_json", call_id=call_id) from exc
    if type(value) is not dict:
        raise QVerisSearchProtocolError("search response must be a JSON object", status_code=status_code, error_code="invalid_response", call_id=call_id)
    return value


def _normalize_base_url(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("base_url must be a string")
    parsed = urlsplit(value)
    hostname = parsed.hostname.lower() if parsed.hostname else None
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not hostname:
        raise ValueError("base_url must not contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    if normalized in _OFFICIAL_BASES:
        return normalized
    if parsed.scheme == "http" and hostname == "127.0.0.1":
        return normalized
    raise ValueError("base_url must be an official QVeris Search API base or explicit http://127.0.0.1 test server")


def _call_id(headers: Any) -> str | None:
    value = headers.get("X-Qveris-Call-ID") if headers is not None else None
    if value is None:
        return None
    try:
        return _safe_id(value, "X-Qveris-Call-ID")
    except ValueError as exc:
        raise QVerisSearchProtocolError("search response has an invalid call ID", error_code="invalid_call_id") from exc


def _read_limited(response: Any, limit: int, *, status_code: int | None, call_id: str | None) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) < 0 or int(length) > limit:
                raise QVerisSearchProtocolError("search response exceeds configured size limit", status_code=status_code, error_code="response_too_large", call_id=call_id)
        except ValueError as exc:
            raise QVerisSearchProtocolError("search response has invalid Content-Length", status_code=status_code, error_code="invalid_content_length", call_id=call_id) from exc
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise QVerisSearchProtocolError("search response exceeds configured size limit", status_code=status_code, error_code="response_too_large", call_id=call_id)
    return raw


def _safe_text(value: Any, field: str, limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit or "\x00" in value:
        raise QVerisSearchProtocolError("%s is invalid" % field, error_code="invalid_response")
    return value


def _optional_text(value: Any, field: str, limit: int) -> str:
    if value is None:
        return ""
    return _safe_text(value, field, limit)


def _schema_type(value: Any) -> str:
    """Match the Go client's permissive ToolParam type projection."""
    if type(value) is str:
        return value
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float) and not isinstance(value, bool):
        if type(value) is float and (value != value or value in (float("inf"), float("-inf"))):
            return ""
        return ("%f" % value).rstrip("0").rstrip(".") if type(value) is float else str(value)
    if type(value) is list:
        return "|".join(part for part in (_schema_type(item) for item in value) if part)
    if type(value) is dict:
        type_name = _schema_type(value.get("type"))
        item_type = _schema_type(value.get("items"))
        return "array<%s>" % item_type if type_name.lower() == "array" and item_type else type_name
    return ""


def _bounded_json(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise QVerisSearchProtocolError("tool params exceed depth limit", error_code="invalid_response")
    if value is None or type(value) in (bool, int, float, str):
        if type(value) is float and (value != value or value in (float("inf"), float("-inf"))):
            raise QVerisSearchProtocolError("tool params contain a non-finite number", error_code="invalid_response")
        if type(value) is str and len(value) > 4096:
            raise QVerisSearchProtocolError("tool params contain an oversized string", error_code="invalid_response")
        return
    if type(value) is list:
        if len(value) > 100:
            raise QVerisSearchProtocolError("tool params contain too many items", error_code="invalid_response")
        for item in value:
            _bounded_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        if len(value) > 100 or any(type(key) is not str or not key for key in value):
            raise QVerisSearchProtocolError("tool params contain invalid keys", error_code="invalid_response")
        for item in value.values():
            _bounded_json(item, depth=depth + 1)
        return
    raise QVerisSearchProtocolError("tool params are not JSON", error_code="invalid_response")


def _params(value: Any) -> tuple[dict[str, Any], ...] | None:
    if value is None:
        return None
    if type(value) is not list or len(value) > 100:
        raise QVerisSearchProtocolError("tool params must be a bounded array", error_code="invalid_response")
    normalized = []
    for item in value:
        if type(item) is not dict:
            raise QVerisSearchProtocolError("tool param must be an object", error_code="invalid_response")
        try:
            name = _safe_id(item.get("name"), "param.name")
        except ValueError as exc:
            raise QVerisSearchProtocolError("tool param has invalid name", error_code="invalid_response") from exc
        _bounded_json(item.get("type"))
        type_name = _schema_type(item.get("type"))
        if len(type_name) > 128 or "\x00" in type_name:
            raise QVerisSearchProtocolError("param.type is invalid", error_code="invalid_response")
        required = item.get("required", False)
        if required is None:
            required = False
        if type(required) is not bool:
            raise QVerisSearchProtocolError("tool param required must be boolean", error_code="invalid_response")
        projection: dict[str, Any] = {"name": name, "type": type_name, "required": required, "description": _optional_text(item.get("description"), "param.description", 4096)}
        for optional in ("enum", "minimum", "maximum"):
            outer = item.get(optional) if optional in item else None
            structured = item.get("type") if type(item.get("type")) is dict else None
            inner = structured.get(optional) if structured is not None and optional in structured else None
            if outer is not None and inner is not None and _canonical_json(outer, field="tool param") != _canonical_json(inner, field="tool param"):
                raise QVerisSearchProtocolError("tool param has conflicting %s" % optional, error_code="invalid_response")
            option = outer if outer is not None else inner
            if option is not None:
                _bounded_json(option)
                projection[optional] = json.loads(_canonical_json(option, field="tool param"))
        normalized.append(projection)
    if len({item["name"] for item in normalized}) != len(normalized):
        raise QVerisSearchProtocolError("tool params contain duplicate names", error_code="invalid_response")
    return tuple(normalized)


def _tool(value: Any, *, allow_optional_metadata: bool = False) -> SearchTool:
    if type(value) is not dict:
        raise QVerisSearchProtocolError("search result must be an object", error_code="invalid_response")
    try:
        tool_id = _safe_id(value.get("tool_id"), "tool_id")
    except ValueError as exc:
        raise QVerisSearchProtocolError("search result has invalid tool_id", error_code="invalid_response") from exc
    text = _optional_text if allow_optional_metadata else _safe_text
    name = text(value.get("name"), "tool name", 256)
    description = text(value.get("description"), "tool description", 8192)
    params = _params(value.get("params"))
    if params is not None and len(_canonical_json(params, field="tool params")) > 16_384:
        raise QVerisSearchProtocolError("tool params exceed size limit", error_code="invalid_response")
    expected_cost = value.get("expected_cost")
    if expected_cost is not None:
        if type(expected_cost) is not str or len(expected_cost) > 256 or "\x00" in expected_cost:
            raise QVerisSearchProtocolError("expected_cost is invalid", error_code="invalid_response")
    billing_rule = value.get("billing_rule")
    if billing_rule is not None:
        if type(billing_rule) is not dict:
            raise QVerisSearchProtocolError("billing_rule must be an object", error_code="invalid_response")
        _bounded_json(billing_rule)
        if len(_canonical_json(billing_rule, field="billing_rule")) > 4096:
            raise QVerisSearchProtocolError("billing_rule exceeds size limit", error_code="invalid_response")
        billing_rule = json.loads(_canonical_json(billing_rule, field="billing_rule"))
    return SearchTool(tool_id, name, description, params, expected_cost, billing_rule)


class QVerisSearchClient:
    """One-shot QVeris Search/Inspect/Execute client; no redirects or retries."""

    def __init__(self, *, api_key: str | None = None, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, use_environment_proxy: bool = True, ssl_context: ssl.SSLContext | None = None, ca_file: str | None = None) -> None:
        key = api_key if api_key is not None else os.environ.get("QVERIS_API_KEY")
        if type(key) is not str or not key or "\r" in key or "\n" in key:
            raise ValueError("QVeris API key must be supplied explicitly or via QVERIS_API_KEY")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_request_bytes) is not int or isinstance(max_request_bytes, bool) or max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be a positive integer")
        if type(max_response_bytes) is not int or isinstance(max_response_bytes, bool) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        if type(use_environment_proxy) is not bool:
            raise ValueError("use_environment_proxy must be a boolean")
        self._api_key, self._base_url, self._timeout_seconds = key, _normalize_base_url(base_url), float(timeout_seconds)
        self._max_request_bytes, self._max_response_bytes = max_request_bytes, max_response_bytes
        self._ssl_context = verified_ssl_context(
            ssl_context=ssl_context,
            ca_file=ca_file,
            environment_ca_file="QVERIS_CA_BUNDLE",
        )
        self._opener = build_opener(
            *(() if use_environment_proxy else (ProxyHandler({}),)),
            HTTPSHandler(context=self._ssl_context),
            _NoRedirect(),
        )

    def __repr__(self) -> str:
        return "QVerisSearchClient(base_url=%r)" % self._base_url

    def _post(self, path: str, body_value: Mapping[str, Any], *, idempotency_key: str | None = None) -> tuple[dict[str, Any], str | None]:
        body = _canonical_json(body_value, field="request")
        if len(body) > self._max_request_bytes:
            raise ValueError("request exceeds configured size limit")
        headers = {"Authorization": "Bearer " + self._api_key, "Accept": "application/json", "Content-Type": "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = _safe_id(idempotency_key, "idempotency_key")
        request = Request(self._base_url + path, data=body, method="POST", headers=headers)
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                call_id = _call_id(response.headers)
                raw = _read_limited(response, self._max_response_bytes, status_code=response.status, call_id=call_id)
                payload, status_code = _json_object(raw, status_code=response.status, call_id=call_id), response.status
        except HTTPError as exc:
            call_id = _call_id(exc.headers)
            payload = _json_object(_read_limited(exc, self._max_response_bytes, status_code=exc.code, call_id=call_id), status_code=exc.code, call_id=call_id)
            error = payload.get("error")
            code = error.get("code") if type(error) is dict else None
            try:
                code = _safe_id(code, "error.code")
            except ValueError:
                code = "invalid_error_envelope"
            raise QVerisSearchHttpError("QVeris API request failed", status_code=exc.code, error_code=code, call_id=call_id) from None
        except (TimeoutError, socket.timeout) as exc:
            raise QVerisSearchTransportError("QVeris API request timed out", error_code="timeout") from exc
        except URLError as exc:
            raise QVerisSearchTransportError("QVeris API transport failed", error_code="transport_error") from exc
        if status_code != 200:
            raise QVerisSearchProtocolError("QVeris API returned unexpected status", status_code=status_code, error_code="unexpected_status", call_id=call_id)
        return payload, call_id

    def search(self, *, query: str, limit: int, session_id: str) -> SearchCatalog:
        if type(query) is not str or not query.strip() or len(query) > 8192 or "\x00" in query:
            raise ValueError("query must be non-empty text of at most 8192 characters")
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer from 1 to 50")
        session_id = _safe_id(session_id, "session_id")
        payload, call_id = self._post("/search", {"query": query, "limit": limit, "session_id": session_id})
        try:
            search_id = _safe_id(payload.get("search_id"), "search_id")
        except ValueError as exc:
            raise QVerisSearchProtocolError("search response requires search_id", error_code="invalid_response", call_id=call_id) from exc
        if type(payload.get("results")) is not list or len(payload["results"]) > limit:
            raise QVerisSearchProtocolError("search response requires at most limit results", error_code="invalid_response", call_id=call_id)
        results = tuple(_tool(item) for item in payload["results"])
        if len({item.tool_id for item in results}) != len(results):
            raise QVerisSearchProtocolError("search response contains duplicate tool IDs", error_code="invalid_response", call_id=call_id)
        remaining = payload.get("remaining_credits")
        if remaining is not None:
            remaining = _finite_nonnegative(remaining, "remaining_credits")
        return SearchCatalog(search_id, results, remaining, call_id)

    def inspect(self, *, tool_id: str, search_id: str, session_id: str) -> ToolInspection:
        """Read one tool contract. It is never an execution and never retries."""
        tool_id, search_id, session_id = _safe_id(tool_id, "tool_id"), _safe_id(search_id, "search_id"), _safe_id(session_id, "session_id")
        payload, call_id = self._post("/tools/by-ids", {"tool_ids": [tool_id], "search_id": search_id, "session_id": session_id})
        success = payload.get("success")
        if ("success" in payload and type(success) is not bool) or success is False or type(payload.get("results")) is not list or len(payload["results"]) != 1:
            raise QVerisSearchProtocolError("inspect response must confirm exactly one tool", error_code="invalid_inspect_response", call_id=call_id)
        tool = _tool(payload["results"][0], allow_optional_metadata=True)
        if tool.tool_id != tool_id or tool.params is None:
            raise QVerisSearchProtocolError("inspect response does not freeze the requested tool schema", error_code="invalid_inspect_response", call_id=call_id)
        remaining = payload.get("remaining_credits")
        if remaining is not None:
            remaining = _finite_nonnegative(remaining, "remaining_credits")
        return ToolInspection(tool, remaining, call_id)

    def execute(self, *, tool_id: str, parameters: Mapping[str, Any], search_id: str, session_id: str, idempotency_key: str, max_response_size: int = 65_536) -> ToolExecution:
        """Execute exactly once with explicit idempotency; callers must not retry."""
        tool_id, search_id, session_id = _safe_id(tool_id, "tool_id"), _safe_id(search_id, "search_id"), _safe_id(session_id, "session_id")
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object")
        _bounded_json(dict(parameters))
        if type(max_response_size) is not int or isinstance(max_response_size, bool) or not 1 <= max_response_size <= 65_536:
            raise ValueError("max_response_size must be an integer from 1 to 65536")
        payload, call_id = self._post("/tools/execute?tool_id=" + quote(tool_id, safe=""), {"parameters": dict(parameters), "search_id": search_id, "session_id": session_id, "max_response_size": max_response_size}, idempotency_key=idempotency_key)
        if "success" in payload:
            success = payload["success"]
            if success is not True and not (type(success) is str and success.strip().lower() == "true"):
                if success is False or (type(success) is str and success.strip().lower() == "false"):
                    raise QVerisSearchProtocolError("execute response is not a business success", error_code="execution_failed", call_id=call_id)
                raise QVerisSearchProtocolError("execute response success is invalid", error_code="invalid_execution_response", call_id=call_id)
        try:
            execution_id = _safe_id(payload.get("execution_id"), "execution_id")
        except ValueError as exc:
            raise QVerisSearchProtocolError("execute response requires execution_id", error_code="invalid_execution_response", call_id=call_id) from exc
        cost = _finite_nonnegative(payload.get("cost"), "cost")
        remaining = payload.get("remaining_credits")
        if remaining is not None:
            remaining = _finite_nonnegative(remaining, "remaining_credits")
        if "result" not in payload:
            raise QVerisSearchProtocolError("execute response requires result", error_code="invalid_execution_response", call_id=call_id)
        result = payload["result"]
        _bounded_json(result)
        if len(_canonical_json(result, field="execution result")) > max_response_size:
            raise QVerisSearchProtocolError("execute result exceeds declared response limit", error_code="invalid_execution_response", call_id=call_id)
        return ToolExecution(tool_id, execution_id, cost, remaining, json.loads(_canonical_json(result, field="execution result")), call_id)

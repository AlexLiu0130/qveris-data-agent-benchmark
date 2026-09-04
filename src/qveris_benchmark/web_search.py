"""One-shot Tavily public-web Search client for the exploratory Runner arm.

Returned sources are untrusted external content.  The caller must keep them
inside its untrusted-data boundary; this module never treats a source as an
instruction or a benchmark receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .runner_gateway_agent import WebSearchResult, WebSource


DEFAULT_BASE_URL = "https://api.tavily.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_REQUEST_BYTES = 65_536
DEFAULT_MAX_RESPONSE_BYTES = 262_144
MAX_SOURCES = 5
MAX_QUERY_CHARS = 4_096
MAX_SOURCE_FIELD_CHARS = 4_096
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class TavilySearchError(RuntimeError):
    """Safe Tavily failure; it never retains a response body or API key."""

    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status_code, self.error_code, self.retry_after = status_code, error_code, retry_after


class TavilySearchTransportError(TavilySearchError):
    pass


class TavilySearchProtocolError(TavilySearchError):
    pass


class TavilySearchHttpError(TavilySearchError):
    pass


@dataclass(frozen=True)
class TavilyReceipt:
    """Safe, local-only metadata from the most recent one-shot Search call."""

    request_id: str | None
    credits: float | None
    cost_usd: None = None


def _normalize_base_url(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("base_url must be a string")
    parsed = urlsplit(value)
    hostname = parsed.hostname.lower() if parsed.hostname else None
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not hostname:
        raise ValueError("base_url must not contain credentials, query, or fragment")
    normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    if normalized == DEFAULT_BASE_URL:
        return normalized
    if parsed.scheme == "http" and hostname == "127.0.0.1":
        return normalized
    raise ValueError("base_url must be https://api.tavily.com or explicit http://127.0.0.1 test server")


def _limited_read(response: Any, limit: int, *, status_code: int | None, retry_after: int | None) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) < 0 or int(length) > limit:
                raise TavilySearchProtocolError("Tavily response exceeds configured size limit", status_code=status_code, error_code="response_too_large", retry_after=retry_after)
        except ValueError as exc:
            raise TavilySearchProtocolError("Tavily returned invalid Content-Length", status_code=status_code, error_code="invalid_content_length", retry_after=retry_after) from exc
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise TavilySearchProtocolError("Tavily response exceeds configured size limit", status_code=status_code, error_code="response_too_large", retry_after=retry_after)
    return raw


def _json_object(raw: bytes, *, status_code: int | None, retry_after: int | None) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TavilySearchProtocolError("Tavily response is not valid JSON", status_code=status_code, error_code="invalid_json", retry_after=retry_after) from exc
    if type(value) is not dict:
        raise TavilySearchProtocolError("Tavily response must be a JSON object", status_code=status_code, error_code="invalid_response", retry_after=retry_after)
    return value


def _retry_after(headers: Any) -> int | None:
    value = headers.get("Retry-After") if headers is not None else None
    return int(value) if isinstance(value, str) and value.isdecimal() else None


def _safe_request_id(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SAFE_REQUEST_ID.fullmatch(value) is None:
        raise TavilySearchProtocolError("Tavily request_id is invalid", error_code="invalid_response")
    return value


def _credits(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) is not dict or type(value.get("credits")) not in (int, float) or isinstance(value.get("credits"), bool):
        raise TavilySearchProtocolError("Tavily usage is invalid", error_code="invalid_response")
    credits = float(value["credits"])
    if credits < 0 or credits != credits or credits in (float("inf"), float("-inf")):
        raise TavilySearchProtocolError("Tavily usage is invalid", error_code="invalid_response")
    return credits


def _source_url(value: Any) -> str:
    if type(value) is not str or not value or len(value) > MAX_SOURCE_FIELD_CHARS or any(character.isspace() for character in value):
        raise TavilySearchProtocolError("Tavily result URL is invalid", error_code="invalid_response")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise TavilySearchProtocolError("Tavily result URL must be an absolute HTTPS URL", error_code="invalid_response")
    return value


def _source_text(value: Any, field: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_SOURCE_FIELD_CHARS or "\x00" in value:
        raise TavilySearchProtocolError("Tavily result %s is invalid" % field, error_code="invalid_response")
    return value


def _project_sources(value: Any, limit: int) -> tuple[WebSource, ...]:
    if type(value) is not list or len(value) > limit:
        raise TavilySearchProtocolError("Tavily results are invalid", error_code="invalid_response")
    sources = []
    for item in value:
        if type(item) is not dict:
            raise TavilySearchProtocolError("Tavily result is invalid", error_code="invalid_response")
        sources.append(WebSource(_source_url(item.get("url")), _source_text(item.get("title"), "title"), _source_text(item.get("content"), "content")))
    return tuple(sources)


class TavilyWebSearchClient:
    """Exactly one POST per ``search`` call, with no retry or redirect follow."""

    def __init__(self, *, api_key: str | None = None, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES) -> None:
        key = api_key if api_key is not None else os.environ.get("TAVILY_API_KEY")
        if type(key) is not str or not key or "\r" in key or "\n" in key:
            raise ValueError("Tavily API key must be supplied explicitly or via TAVILY_API_KEY")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_request_bytes) is not int or isinstance(max_request_bytes, bool) or max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be a positive integer")
        if type(max_response_bytes) is not int or isinstance(max_response_bytes, bool) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        self._api_key, self._base_url = key, _normalize_base_url(base_url)
        self._timeout_seconds, self._max_request_bytes, self._max_response_bytes = float(timeout_seconds), max_request_bytes, max_response_bytes
        self._opener = build_opener(_NoRedirect())
        self.last_receipt: TavilyReceipt | None = None

    def __repr__(self) -> str:
        return "TavilyWebSearchClient(base_url=%r)" % self._base_url

    def search(self, *, query: str, limit: int) -> WebSearchResult:
        if type(query) is not str or not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise ValueError("query must be a non-empty bounded string")
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= MAX_SOURCES:
            raise ValueError("limit must be an integer from 1 to %d" % MAX_SOURCES)
        payload = {"query": query, "search_depth": "basic", "max_results": limit, "include_answer": False, "include_raw_content": False, "include_images": False, "include_favicon": False, "auto_parameters": False, "include_usage": True}
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) > self._max_request_bytes:
            raise ValueError("request exceeds configured size limit")
        request = Request(self._base_url + "/search", data=data, method="POST", headers={"Authorization": "Bearer " + self._api_key, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                retry_after = _retry_after(response.headers)
                body = _json_object(_limited_read(response, self._max_response_bytes, status_code=response.status, retry_after=retry_after), status_code=response.status, retry_after=retry_after)
                if response.status != 200:
                    raise TavilySearchProtocolError("Tavily returned unexpected status", status_code=response.status, error_code="unexpected_status", retry_after=retry_after)
        except HTTPError as exc:
            retry_after = _retry_after(exc.headers)
            _json_object(_limited_read(exc, self._max_response_bytes, status_code=exc.code, retry_after=retry_after), status_code=exc.code, retry_after=retry_after)
            raise TavilySearchHttpError("Tavily search failed", status_code=exc.code, error_code="http_%d" % exc.code, retry_after=retry_after) from None
        except (TimeoutError, socket.timeout) as exc:
            raise TavilySearchTransportError("Tavily request timed out", error_code="timeout") from exc
        except URLError as exc:
            raise TavilySearchTransportError("Tavily transport failed", error_code="transport_error") from exc
        if body.get("query") != query:
            raise TavilySearchProtocolError("Tavily response query does not match request", status_code=200, error_code="query_mismatch")
        sources = _project_sources(body.get("results"), limit)
        self.last_receipt = TavilyReceipt(_safe_request_id(body.get("request_id")), _credits(body.get("usage")))
        return WebSearchResult(query=query, as_of=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), sources=sources)

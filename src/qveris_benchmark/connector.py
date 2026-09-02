"""One-shot, manifest-bound connector for QVeris benchmark tool calls."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from .contracts import AuthMode, PlanStatus, SemanticPlan
from .manifest import Manifest, ToolManifestEntry


DEFAULT_API_BASE = "https://qveris.ai/api/v1"
_BLOCKED_STATUSES = frozenset({"blocked", "denied", "forbidden", "rejected"})
_EMPTY_FIELDS = ("data", "results", "items", "result")


class CallOutcome(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class RequestValidationError(ValueError):
    """Raised before transport when plan arguments are not manifest-safe."""


class SchemaValidationError(ValueError):
    """Raised when a value or a manifest schema falls outside the supported subset."""


@dataclass(frozen=True)
class TransportResponse:
    status_code: int | None
    payload: Mapping[str, Any] | None = None
    error: str | None = None
    timed_out: bool = False


@dataclass(frozen=True)
class CallMetadata:
    tool_alias: str
    tool_id: str
    idempotency_key: str
    attempt: int
    latency_ms: int
    http_status: int | None


@dataclass(frozen=True)
class ConnectorResult:
    outcome: CallOutcome
    payload: Mapping[str, Any] | None
    metadata: CallMetadata
    reason: str | None = None


class LiveTransport:
    """Low-level HTTP boundary; BenchmarkRunner accepts FakeReplayTransport only."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310: URL is validated by Connector.
                raw = response.read()
                return TransportResponse(response.status, _decode_object(raw))
        except HTTPError as exc:
            return TransportResponse(exc.code, _decode_object(exc.read()), str(exc))
        except TimeoutError as exc:
            return TransportResponse(None, error=str(exc) or "timeout", timed_out=True)
        except URLError as exc:
            timed_out = isinstance(exc.reason, TimeoutError)
            return TransportResponse(None, error=str(exc.reason), timed_out=timed_out)


@dataclass
class FakeReplayTransport:
    """Fixture-only transport. It never opens a socket."""

    fixtures: Mapping[str, TransportResponse | Mapping[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        tool_id = parse_qs(urlsplit(url).query).get("tool_id", [""])[0]
        self.calls.append({"url": url, "body": body, "headers": dict(headers), "timeout": timeout})
        fixture = self.fixtures.get(tool_id)
        if fixture is None:
            return TransportResponse(None, error="missing replay fixture")
        if isinstance(fixture, TransportResponse):
            return fixture
        if type(fixture) is not dict:
            return TransportResponse(None, error="invalid replay fixture")
        return TransportResponse(200, fixture)


class Connector:
    """Resolve a model alias, validate its arguments, then perform exactly one POST."""

    def __init__(
        self,
        manifest: Manifest,
        transport: LiveTransport | FakeReplayTransport,
        *,
        base_url: str = DEFAULT_API_BASE,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._manifest = manifest
        self._transport = transport
        self._base_url = _validated_api_base(base_url)
        self._api_key = api_key
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._timeout = timeout
        if isinstance(transport, LiveTransport) and not api_key:
            raise ValueError("api_key is required for live transport")

    def execute(self, plan: SemanticPlan, *, idempotency_key: str) -> ConnectorResult:
        if plan.status is not PlanStatus.READY:
            raise RequestValidationError("only READY plans may execute")
        if type(idempotency_key) is not str or not idempotency_key.strip():
            raise RequestValidationError("idempotency_key must be a non-empty string")

        entry = self._manifest.entry_for(plan)
        if entry.auth_mode is not AuthMode.BEARER:
            raise RequestValidationError("only bearer manifest entries are executable")
        arguments = dict(plan.request or {})
        _validate_arguments(arguments, entry.request_schema)
        url = "%s/tools/execute?%s" % (self._base_url, urlencode({"tool_id": entry.tool_id}))
        body = json.dumps({"parameters": arguments}, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        if self._api_key:
            headers["Authorization"] = "Bearer %s" % self._api_key

        started = time.monotonic()
        response = self._transport.post(url, body, headers, self._timeout)
        latency_ms = round((time.monotonic() - started) * 1000)
        metadata = CallMetadata(entry.alias, entry.tool_id, idempotency_key, 1, latency_ms, response.status_code)
        outcome, reason = _gate_response(response, entry.response_schema)
        return ConnectorResult(outcome, response.payload, metadata, reason)


def _validated_api_base(base_url: str) -> str:
    if type(base_url) is not str:
        raise ValueError("base_url must be a string")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "qveris.ai"
        or parsed.username
        or parsed.password
        or parsed.path != "/api/v1"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base_url must be exactly https://qveris.ai/api/v1")
    return DEFAULT_API_BASE


def _validate_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    if type(arguments) is not dict:
        raise RequestValidationError("arguments must be an object")
    _reject_full_content_url(arguments)
    _validate_value(arguments, schema, "arguments", RequestValidationError, strict_objects=True)


def _validate_value(
    value: Any,
    schema: Any,
    name: str,
    error_type: type[ValueError],
    *,
    strict_objects: bool,
) -> None:
    _validate_schema(schema, name, error_type)
    expected = schema["type"]
    valid = {
        "string": type(value) is str,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "array": type(value) is list,
        "object": type(value) is dict,
        "null": value is None,
    }.get(expected)
    if valid is not True:
        raise error_type("invalid type for %s" % name)
    if "enum" in schema and not any(type(option) is type(value) and option == value for option in schema["enum"]):
        raise error_type("value for %s is not in enum" % name)
    if expected in ("number", "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            raise error_type("value for %s is below minimum" % name)
        if "maximum" in schema and value > schema["maximum"]:
            raise error_type("value for %s is above maximum" % name)
    elif expected == "string" and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            raise error_type("value for %s does not match pattern" % name)
    elif expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise error_type("array for %s is too short" % name)
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise error_type("array for %s is too long" % name)
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_value(item, schema["items"], "%s[%d]" % (name, index), error_type, strict_objects=strict_objects)
    elif expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        unknown = set(value) - set(properties)
        if unknown and (strict_objects or schema.get("additionalProperties") is False):
            raise error_type("unknown property for %s: %s" % (name, sorted(unknown)[0]))
        missing = set(required) - set(value)
        if missing:
            raise error_type("missing required property for %s: %s" % (name, sorted(missing)[0]))
        for property_name, property_value in value.items():
            if property_name in properties:
                _validate_value(
                    property_value,
                    properties[property_name],
                    "%s.%s" % (name, property_name),
                    error_type,
                    strict_objects=strict_objects,
                )
    _reject_full_content_url(value)


def _validate_schema(schema: Any, name: str, error_type: type[ValueError]) -> None:
    if not isinstance(schema, Mapping) or type(schema.get("type")) is not str:
        raise error_type("invalid schema for %s" % name)
    expected = schema["type"]
    allowed = {"type", "enum"}
    type_keywords = {
        "object": {"properties", "required", "additionalProperties"},
        "array": {"items", "minItems", "maxItems"},
        "string": {"pattern"},
        "number": {"minimum", "maximum"},
        "integer": {"minimum", "maximum"},
        "boolean": set(),
        "null": set(),
    }
    if expected not in type_keywords or set(schema) - allowed - type_keywords[expected]:
        raise error_type("unsupported schema keyword for %s" % name)
    if "enum" in schema and type(schema["enum"]) is not list:
        raise error_type("invalid enum for %s" % name)
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", False)
        if not isinstance(properties, Mapping) or type(required) is not list or not all(type(key) is str for key in required):
            raise error_type("invalid object schema for %s" % name)
        if additional is not False or not set(required).issubset(properties):
            raise error_type("invalid object constraints for %s" % name)
        for property_name, property_schema in properties.items():
            if type(property_name) is not str:
                raise error_type("invalid property name for %s" % name)
            _validate_schema(property_schema, "%s.%s" % (name, property_name), error_type)
    elif expected == "array":
        if "items" in schema:
            _validate_schema(schema["items"], "%s[]" % name, error_type)
        for key in ("minItems", "maxItems"):
            if key in schema and (type(schema[key]) is not int or schema[key] < 0):
                raise error_type("invalid %s for %s" % (key, name))
        if "minItems" in schema and "maxItems" in schema and schema["minItems"] > schema["maxItems"]:
            raise error_type("invalid array bounds for %s" % name)
    elif expected == "string" and "pattern" in schema:
        if type(schema["pattern"]) is not str:
            raise error_type("invalid pattern for %s" % name)
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise error_type("invalid pattern for %s" % name) from exc
    elif expected in ("number", "integer"):
        for key in ("minimum", "maximum"):
            if key in schema and type(schema[key]) not in (int, float):
                raise error_type("invalid %s for %s" % (key, name))
        if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
            raise error_type("invalid numeric bounds for %s" % name)


def _reject_full_content_url(value: Any) -> None:
    if isinstance(value, Mapping):
        if "full_content_file_url" in value:
            raise RequestValidationError("full_content_file_url is not permitted")
        for nested in value.values():
            _reject_full_content_url(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_full_content_url(nested)


def _gate_response(response: TransportResponse, response_schema: Mapping[str, Any]) -> tuple[CallOutcome, str | None]:
    if response.timed_out:
        return CallOutcome.UNCERTAIN, "timeout"
    if response.status_code is None:
        return CallOutcome.UNCERTAIN, response.error or "transport error"
    if not 200 <= response.status_code < 300:
        return CallOutcome.FAILED, response.error or "http error"
    payload = response.payload
    if type(payload) is not dict:
        return CallOutcome.UNCERTAIN, "invalid response payload"
    status = payload.get("status")
    if payload.get("blocked") is True or (type(status) is str and status.lower() in _BLOCKED_STATUSES):
        return CallOutcome.BLOCKED, "provider blocked request"
    if payload.get("success") is False:
        return CallOutcome.FAILED, "provider reported failure"
    if payload.get("success") is not True:
        return CallOutcome.UNCERTAIN, "missing explicit business success"
    try:
        _validate_value(payload, response_schema, "response", SchemaValidationError, strict_objects=False)
    except SchemaValidationError as exc:
        return CallOutcome.FAILED, "response schema validation failed: %s" % exc
    if any(field in payload and payload[field] in (None, [], {}) for field in _EMPTY_FIELDS):
        return CallOutcome.EMPTY, None
    return CallOutcome.SUCCESS, None


def _decode_object(raw: bytes) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if type(value) is dict else None

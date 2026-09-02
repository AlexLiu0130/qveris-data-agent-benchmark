"""Minimal one-agent, one-tool QVeris get orchestration.

QVeris Tool live execution is forbidden here. An HTTPS-allowlisted live
SemanticAgent may still be paired with a FakeReplay connector for semantic
integration or benchmark runs; that does not make this interface live-ready.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import re
from collections.abc import Callable, Mapping
from typing import Any

from .agent import SemanticAgent, SemanticPlanReceipt
from .connector import CallOutcome, Connector, ConnectorResult, RequestValidationError
from .contracts import PlanStatus


class GetStatus(str, Enum):
    """Public get statuses; connector outcomes retain their exact values."""

    SUCCESS = CallOutcome.SUCCESS.value
    EMPTY = CallOutcome.EMPTY.value
    BLOCKED = CallOutcome.BLOCKED.value
    FAILED = CallOutcome.FAILED.value
    UNCERTAIN = CallOutcome.UNCERTAIN.value
    CLARIFY = PlanStatus.CLARIFY.value
    REJECT = PlanStatus.REJECT.value
    SEMANTIC_ERROR = "semantic_error"


@dataclass(frozen=True)
class GetResultEnvelope:
    """The only business result returned by a get request."""

    request_id: str
    status: GetStatus
    tool_alias: str | None
    payload: Mapping[str, Any] | None
    message: str | None


@dataclass(frozen=True)
class _GetTrace:
    """Private harness-only execution material; never part of the get return."""

    receipt: SemanticPlanReceipt | None
    connector_result: ConnectorResult | None
    reason: str | None
    agent_call_count: int
    connector_call_count: int | None


_TraceSink = Callable[[_GetTrace], None]
_SEMANTIC_ERROR_MESSAGE = "semantic_error"
_CONNECTOR_MESSAGES = {
    CallOutcome.EMPTY: "no data returned",
    CallOutcome.BLOCKED: "tool execution blocked",
    CallOutcome.FAILED: "tool execution failed",
    CallOutcome.UNCERTAIN: "tool execution uncertain",
}
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE_SCHEMA_EXACT = frozenset(
    {
        "authorization",
        "api_key",
        "access_token",
        "secret",
        "password",
        "credential",
        "token",
        "cookie",
        "header",
        "key",
        "tool_id",
        "idempotency",
        "idempotency_key",
        "execution_id",
    }
)
_SENSITIVE_SCHEMA_SEGMENTS = frozenset(
    {"authorization", "secret", "password", "credential", "token", "cookie", "header", "key", "idempotency"}
)
_MAX_QUERY_LENGTH = 4096


class QVerisGet:
    """Safe/replay core: one plan, then at most one non-live QVeris tool call.

    A live, allowlisted SemanticAgent is permitted only with a replay
    connector for semantic integration; it does not make QVeris live.
    """

    def __init__(
        self, agent: SemanticAgent, connector: Connector, *, trace_sink: _TraceSink | None = None
    ) -> None:
        if agent.manifest is None or agent.manifest is not connector.manifest:
            raise ValueError("agent and connector must share the exact manifest")
        if connector.is_live:
            raise ValueError("QVerisGet only accepts a non-live connector")
        _validate_manifest_response_schemas(connector.manifest.entries.values())
        self._agent = agent
        self._connector = connector
        self._trace_sink = trace_sink

    def get(self, query: str, *, request_id: str, idempotency_key: str) -> GetResultEnvelope:
        _validate_query(query)
        _validate_opaque_id(request_id, "request_id")
        _validate_opaque_id(idempotency_key, "idempotency_key")

        try:
            receipt = self._agent.plan(query)
        except Exception:
            return self._finish(
                GetResultEnvelope(request_id, GetStatus.SEMANTIC_ERROR, None, None, _SEMANTIC_ERROR_MESSAGE),
                _GetTrace(None, None, "semantic_error", 1, 0),
            )

        plan = receipt.plan
        if plan.status is PlanStatus.CLARIFY or plan.status is PlanStatus.REJECT:
            return self._finish(
                GetResultEnvelope(request_id, GetStatus(plan.status.value), None, None, plan.message),
                _GetTrace(receipt, None, None, 1, 0),
            )

        try:
            connector_result = self._connector.execute(plan, idempotency_key=idempotency_key)
        except RequestValidationError:
            return self._finish(
                GetResultEnvelope(request_id, GetStatus.FAILED, plan.tool_alias, None, "tool execution failed"),
                _GetTrace(receipt, None, "connector_validation_error", 1, 0),
            )
        except Exception:
            return self._finish(
                GetResultEnvelope(request_id, GetStatus.UNCERTAIN, plan.tool_alias, None, "tool execution uncertain"),
                _GetTrace(receipt, None, "connector_error", 1, None),
            )

        return self._finish(
            GetResultEnvelope(
                request_id,
                GetStatus(connector_result.outcome.value),
                plan.tool_alias,
                deepcopy(connector_result.payload),
                _CONNECTOR_MESSAGES.get(connector_result.outcome),
            ),
            _GetTrace(receipt, connector_result, None, 1, 1),
        )

    def _finish(self, envelope: GetResultEnvelope, trace: _GetTrace) -> GetResultEnvelope:
        if self._trace_sink is not None:
            try:
                self._trace_sink(trace)
            except Exception:
                pass
        return envelope


def _validate_query(query: str) -> None:
    if type(query) is not str or not query.strip() or len(query) > _MAX_QUERY_LENGTH:
        raise ValueError("query must be a non-empty string within the length limit")
    if any(ord(character) < 32 or ord(character) == 127 for character in query):
        raise ValueError("query must not contain control characters")


def _validate_opaque_id(value: str, field: str) -> None:
    if type(value) is not str or _OPAQUE_ID.fullmatch(value) is None:
        raise ValueError("%s must be a safe opaque identifier" % field)


def _validate_manifest_response_schemas(entries: Any) -> None:
    for entry in entries:
        _validate_response_schema(entry.response_schema, entry.alias)


def _validate_response_schema(schema: Any, path: str) -> None:
    if not isinstance(schema, Mapping) or type(schema.get("type")) is not str:
        raise ValueError("response schema must be typed: %s" % path)
    if schema["type"] == "object":
        properties = schema.get("properties")
        if schema.get("additionalProperties") is not False or not isinstance(properties, Mapping):
            raise ValueError("response objects must be closed: %s" % path)
        for key, child in properties.items():
            if type(key) is not str or _is_sensitive_schema_key(key):
                raise ValueError("response schema has a sensitive property: %s" % path)
            _validate_response_schema(child, "%s.%s" % (path, key))
    elif schema["type"] == "array":
        if "items" not in schema:
            raise ValueError("response arrays must define items: %s" % path)
        _validate_response_schema(schema["items"], "%s[]" % path)


def _is_sensitive_schema_key(key: str) -> bool:
    """Reject exact normalized names or standalone/contiguous sensitive segments.

    Camel case and punctuation normalize to lowercase underscore segments, so
    ``apiKey`` and ``execution-id`` are caught while unrelated words such as
    ``monkey`` are not matched by substring.
    """
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").lower()
    segments = tuple(part for part in normalized.split("_") if part)
    return (
        normalized in _SENSITIVE_SCHEMA_EXACT
        or any(part in _SENSITIVE_SCHEMA_SEGMENTS for part in segments)
        or any("_".join(segments[index : index + 2]) in _SENSITIVE_SCHEMA_EXACT for index in range(len(segments) - 1))
    )

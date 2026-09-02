"""Stable model-output contracts for one-agent, one-tool benchmark cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .strict_json import StrictJSONError, loads_object, require_exact_fields, require_exact_type


class Domain(str, Enum):
    REALTIME_QUOTE = "realtime_quote"
    HISTORICAL_PRICE = "historical_price"
    FINANCIAL_STATEMENT = "financial_statement"


class AuthMode(str, Enum):
    BEARER = "bearer"


class PlanStatus(str, Enum):
    READY = "READY"
    CLARIFY = "CLARIFY"
    REJECT = "REJECT"


_STATUS_FIELDS = frozenset({"status", "plan_status", "execution_status", "result_status"})


@dataclass(frozen=True)
class SemanticPlan:
    """The only structured model output accepted by the runtime.

    A READY plan references a manifest alias, never a provider tool identifier.
    CLARIFY and REJECT terminate before any tool execution.
    """

    status: PlanStatus
    domain: Domain | None = None
    tool_alias: str | None = None
    request: Mapping[str, Any] | None = None
    message: str | None = None

    @classmethod
    def from_json(cls, raw: str) -> "SemanticPlan":
        value = loads_object(raw)
        require_exact_fields(
            value,
            required={"status"},
            allowed={"status", "domain", "tool_alias", "request", "message"},
            status_fields=_STATUS_FIELDS,
        )
        require_exact_type(value["status"], str, "status")
        try:
            status = PlanStatus(value["status"])
        except ValueError as exc:
            raise StrictJSONError("status must be READY, CLARIFY, or REJECT") from exc

        if status is PlanStatus.READY:
            require_exact_fields(
                value,
                required={"status", "domain", "tool_alias", "request"},
                allowed={"status", "domain", "tool_alias", "request"},
                status_fields=_STATUS_FIELDS,
            )
            require_exact_type(value["domain"], str, "domain")
            require_exact_type(value["tool_alias"], str, "tool_alias")
            require_exact_type(value["request"], dict, "request")
            try:
                domain = Domain(value["domain"])
            except ValueError as exc:
                raise StrictJSONError("unknown domain") from exc
            if not value["tool_alias"]:
                raise StrictJSONError("tool_alias must not be empty")
            return cls(status, domain, value["tool_alias"], value["request"])

        require_exact_fields(
            value,
            required={"status", "message"},
            allowed={"status", "message"},
            status_fields=_STATUS_FIELDS,
        )
        require_exact_type(value["message"], str, "message")
        if not value["message"]:
            raise StrictJSONError("message must not be empty")
        return cls(status, message=value["message"])

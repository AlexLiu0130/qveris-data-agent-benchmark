"""Deterministic, oracle-backed benchmark scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .contracts import SemanticPlan


# The four benchmark metrics and their calculations.  Values are intentionally
# per-case; aggregation is a reporting concern, not a hidden total score.
METRIC_DEFINITIONS = {
    "semantic_exact": "1 only when status, semantic slots, tool alias, and arguments exactly match the case.",
    "data_accuracy": "Scored only for an independent oracle: 1 only when every declared field matches.",
    "token_usage": "Derived from the raw agent usage receipt; cost is unknown without a harness pricing policy.",
    "e2e_ms": "Monotonic end-to-end elapsed milliseconds; agent_call_ms, plan_gate_ms, and connector_ms are harness phase measurements.",
}


@dataclass(frozen=True)
class SemanticScore:
    status_matches: bool
    exact: bool


@dataclass(frozen=True)
class TokenCostPolicy:
    """Optional per-token pricing owned by scoring, never by the agent."""

    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None

    def __post_init__(self) -> None:
        for value in (self.input_cost_per_token, self.output_cost_per_token):
            if value is not None and (type(value) not in (int, float) or isinstance(value, bool) or value < 0):
                raise ValueError("token costs must be non-negative numbers or None")


def derive_token_usage(receipt: Mapping[str, Any] | None, policy: TokenCostPolicy = TokenCostPolicy()) -> dict[str, Any]:
    """Parse a provider receipt into benchmark metrics without estimating missing usage."""
    raw = receipt or {}
    input_tokens = _receipt_int(raw.get("prompt_tokens"))
    output_tokens = _receipt_int(raw.get("completion_tokens"))
    total_tokens = _receipt_int(raw.get("total_tokens"))
    cost: float | str = "unknown"
    if input_tokens is not None and output_tokens is not None and policy.input_cost_per_token is not None and policy.output_cost_per_token is not None:
        cost = input_tokens * policy.input_cost_per_token + output_tokens * policy.output_cost_per_token
    return {
        "source": "provider_reported" if any(value is not None for value in (input_tokens, output_tokens, total_tokens)) else "unknown",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost": cost,
    }


def score_data_accuracy(matches: bool, *, comparable: bool) -> bool | str:
    """Keep data-score admission in scoring; fake replay is never comparable."""
    return matches if comparable else "not_scored"


def score_semantics(
    plan: SemanticPlan,
    *,
    expected_status: str,
    expected_semantics: Mapping[str, Any],
    expected_tool_alias: str | None,
    expected_arguments: Mapping[str, Any],
) -> SemanticScore:
    """Score the fixed plan slots without using a model judge."""
    status_matches = plan.status.value == expected_status
    slots = {"status": plan.status.value}
    if plan.status.value == "READY":
        slots.update({"domain": plan.domain.value if plan.domain else None, "tool_alias": plan.tool_alias, "request": dict(plan.request or {})})
    else:
        slots["message"] = plan.message
    semantics_match = all(slots.get(name) == value for name, value in expected_semantics.items())
    exact = (
        status_matches
        and semantics_match
        and plan.tool_alias == expected_tool_alias
        and dict(plan.request or {}) == dict(expected_arguments)
    )
    return SemanticScore(status_matches, exact)


def match_data(
    actual: Mapping[str, Any], expected: Mapping[str, Any], rule: Mapping[str, Any]
) -> bool:
    """Compare declared payload fields; supported rules are exact and absolute float tolerance."""
    fields = rule.get("fields")
    if type(fields) is not dict or not fields:
        raise ValueError("comparison_rule.fields must be a non-empty object")
    for path, field_rule in fields.items():
        if type(path) is not str:
            raise ValueError("comparison field path must be a string")
        actual_value = _path_value(actual, path)
        expected_value = _path_value(expected, path)
        if field_rule == "exact":
            if type(actual_value) is not type(expected_value) or actual_value != expected_value:
                return False
        elif type(field_rule) is dict and field_rule.get("mode") == "float_tolerance":
            tolerance = field_rule.get("absolute")
            if type(tolerance) not in (int, float) or isinstance(tolerance, bool) or tolerance < 0:
                raise ValueError("float_tolerance.absolute must be a non-negative number")
            if (
                type(actual_value) not in (int, float)
                or isinstance(actual_value, bool)
                or type(expected_value) not in (int, float)
                or isinstance(expected_value, bool)
                or not isfinite(float(actual_value))
                or not isfinite(float(expected_value))
                or abs(float(actual_value) - float(expected_value)) > tolerance
            ):
                return False
        else:
            raise ValueError("comparison rule must be exact or float_tolerance")
    return True


def _path_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if type(current) is not dict or part not in current:
            raise ValueError("missing comparison field: %s" % path)
        current = current[part]
    return current


def _receipt_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None

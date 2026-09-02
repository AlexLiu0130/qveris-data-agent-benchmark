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
    "token_usage": "Provider-reported prompt, completion, and total tokens; unknown when absent.",
    "e2e_ms": "Monotonic end-to-end elapsed milliseconds; model_network_ms, plan_gate_ms, and connector_ms are recorded phase measurements.",
}


@dataclass(frozen=True)
class SemanticScore:
    status_matches: bool
    exact: bool


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

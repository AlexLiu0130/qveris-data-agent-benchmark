"""Strict JSON decoding shared by the benchmark runtime."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


class StrictJSONError(ValueError):
    """Raised when model output does not match its exact JSON contract."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise StrictJSONError(f"non-standard JSON number: {value}")


def loads_object(raw: str) -> dict[str, Any]:
    """Decode one JSON object, rejecting duplicate keys and NaN/Infinity."""
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_nonstandard_number,
        )
    except (json.JSONDecodeError, StrictJSONError) as exc:
        raise StrictJSONError(str(exc)) from exc
    if type(value) is not dict:
        raise StrictJSONError("structured output must be a JSON object")
    return value


def require_exact_fields(
    value: Mapping[str, Any],
    *,
    required: Iterable[str],
    allowed: Iterable[str],
    status_fields: Iterable[str] = (),
) -> None:
    """Require an exact field set and reject alternate status fields explicitly."""
    required_set = set(required)
    allowed_set = set(allowed)
    status_conflicts = (set(status_fields) - {"status"}) & set(value)
    if status_conflicts:
        names = ", ".join(sorted(status_conflicts))
        raise StrictJSONError(f"conflicting status fields: {names}")
    missing = required_set - set(value)
    if missing:
        raise StrictJSONError(f"missing required fields: {', '.join(sorted(missing))}")
    extra = set(value) - allowed_set
    if extra:
        raise StrictJSONError(f"unexpected fields: {', '.join(sorted(extra))}")


def require_exact_type(value: Any, expected: type[Any], field: str) -> None:
    """Reject coercion and bool-as-int surprises by requiring the exact type."""
    if type(value) is not expected:
        raise StrictJSONError(f"{field} must be {expected.__name__}")

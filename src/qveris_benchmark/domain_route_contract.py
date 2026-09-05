"""Small shared contract for deterministic public-GET domain routes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class RoutePlan:
    """One fixed Tool call selected from validated public semantics."""

    tool_id: str
    params: Mapping[str, Any]
    parser_id: str | None
    suite: Literal["financial_statements", "historical_price", "realtime_quote"]
    accepted_variant_id: str
    source: str
    context: Mapping[str, Any]


@dataclass(frozen=True)
class RouteProjection:
    """Provider-free data projection for one completed fixed Tool call."""

    data: Mapping[str, Any]
    as_of: str | None
    status: Literal["success", "partial"] = "success"
    missing_fields: tuple[str, ...] = ()
    schema_version: str = "get-response/v1"
    as_of_status: str = "known"


__all__ = ["RoutePlan", "RouteProjection"]

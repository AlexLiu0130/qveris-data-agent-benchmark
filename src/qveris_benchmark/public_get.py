"""One-pass semantic-to-fixed-route public GET adapter.

The injected resolver is the only model boundary.  It may describe a user
request, but it cannot select a provider, a tool, a parser, or tool
parameters.  Everything after that boundary is deterministic and makes at
most one injected Gateway call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import Any

from .provider_payload import (
    ALPHAVANTAGE_GLOBAL_QUOTE_V1,
    HANGSENG_HK_L1_V1,
    ProviderPayloadParseError,
    parse_provider_payload,
)
from .response_contract import normalize_response
from .run_backend import ExecutionEvidence, PublicGetResult
from .qveris_model_gateway import SemanticGatewayError
from .qveris_tool_gateway import ToolGatewayError
from .runtime_catalog import catalog_entry


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_US = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\Z")
_CN = re.compile(r"[0-9]{6}\Z")
_HK = re.compile(r"[0-9]{5}\Z")
_VENUES = frozenset({"US", "SSE", "SZSE", "HKEX"})
_QUOTE_OPS = frozenset({
    "quote_snapshot", "last_price", "bid_ask_l1", "volume_turnover_snapshot",
    "latest_trade", "extended_hours_price", "trading_status", "batch_quote_snapshot",
})
_HISTORICAL_OPS = frozenset({"daily_bars", "intraday_bars", "corporate_actions", "adjustment_factors", "trading_calendar"})


class SemanticRequestError(ValueError):
    """A model output did not satisfy the public semantic contract."""

    def __init__(self, code: str = "semantic_schema_invalid", *, needs_clarification: bool = False) -> None:
        self.code = "request_incomplete" if needs_clarification else "semantic_schema_invalid"
        self.needs_clarification = needs_clarification
        super().__init__(self.code)


@dataclass(frozen=True)
class _Route:
    scenario_id: str
    tool_id: str
    parser_id: str
    source: str


# This is deliberately small.  The complete static 84-cell catalog lives in
# runtime_catalog.py; only rows with a closed renderer+parser contract appear
# here.  There is no runtime registry lookup or provider fallback.
_DISPATCHABLE = {
    ("US", "quote_snapshot"): _Route("realtime.equity.quote_snapshot.v1", "alphavantage.global_quote.retrieve.v1.9b8a7c6d", ALPHAVANTAGE_GLOBAL_QUOTE_V1, "Alpha Vantage"),
    ("US", "last_price"): _Route("realtime.equity.last_price.v1", "alphavantage.global_quote.retrieve.v1.9b8a7c6d", ALPHAVANTAGE_GLOBAL_QUOTE_V1, "Alpha Vantage"),
    ("HKEX", "bid_ask_l1"): _Route("realtime.equity.bid_ask_l1.v1", "hangseng_polysource.quote.hkshares.live.v2.dec427af", HANGSENG_HK_L1_V1, "Hang Seng"),
}


def _exact_mapping(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise SemanticRequestError("%s has an invalid schema" % label)
    return value


def _iso_date(value: Any, label: str) -> str:
    if type(value) is not str or _DATE.fullmatch(value) is None:
        raise SemanticRequestError("%s must be ISO date" % label)
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SemanticRequestError("%s must be ISO date" % label) from exc
    return value


def _security(value: Any) -> tuple[str, str]:
    item = _exact_mapping(value, {"asset_class", "venue", "local_code"}, "security")
    if item["asset_class"] != "equity" or item["venue"] not in _VENUES or type(item["local_code"]) is not str:
        raise SemanticRequestError(needs_clarification=True)
    venue, code = item["venue"], item["local_code"]
    if venue == "US" and _US.fullmatch(code):
        return venue, code
    if venue in {"SSE", "SZSE"} and _CN.fullmatch(code):
        return venue, code + (".SH" if venue == "SSE" else ".SZ")
    if venue == "HKEX" and _HK.fullmatch(code):
        return venue, code + ".HK"
    raise SemanticRequestError(needs_clarification=True)


def _validated_semantic(value: Any) -> tuple[dict[str, Any], str, str, str, dict[str, Any]]:
    """Return public semantic request plus deterministic route key/arguments."""
    outer = _exact_mapping(value, {"schema_version", "request"}, "semantic output")
    if outer["schema_version"] != "public-get.semantic/v1":
        raise SemanticRequestError("semantic schema version is invalid")
    request = outer["request"]
    if type(request) is not dict or type(request.get("kind")) is not str:
        raise SemanticRequestError("semantic request is invalid")
    kind = request["kind"]
    if kind == "market_quote":
        item = _exact_mapping(request, {"kind", "security", "operation"}, "market quote")
        venue, symbol = _security(item["security"])
        operation = item["operation"]
        if type(operation) is not str or operation not in _QUOTE_OPS:
            raise SemanticRequestError("market quote operation is invalid")
        public = {"kind": kind, "security": {"asset_class": "equity", "venue": venue, "symbol": symbol}, "operation": operation}
        return public, venue, operation, symbol, {}
    if kind == "historical":
        item = _exact_mapping(request, {"kind", "security", "operation", "start_date", "end_date"}, "historical request")
        venue, symbol = _security(item["security"])
        operation, start, end = item["operation"], _iso_date(item["start_date"], "start_date"), _iso_date(item["end_date"], "end_date")
        if type(operation) is not str or operation not in _HISTORICAL_OPS or start > end:
            raise SemanticRequestError("historical request is invalid")
        public = {"kind": kind, "security": {"asset_class": "equity", "venue": venue, "symbol": symbol}, "operation": operation, "start_date": start, "end_date": end}
        return public, venue, operation, symbol, {"start_date": start, "end_date": end}
    if kind == "financial_statement":
        item = _exact_mapping(request, {"kind", "security", "statement"}, "financial statement")
        venue, symbol = _security(item["security"])
        statement = _exact_mapping(item["statement"], {"type", "presentation", "period", "fields"}, "statement")
        period = _exact_mapping(statement["period"], {"kind", "fiscal_year", "fiscal_period"}, "statement period")
        if statement["type"] not in {"income", "balance", "cash_flow"} or statement["presentation"] not in {"standardized", "as_reported"} or (statement["presentation"] == "as_reported" and statement["type"] != "income") or period["kind"] != "specified_period" or type(period["fiscal_year"]) is not int or type(period["fiscal_period"]) is not str or type(statement["fields"]) is not list or not statement["fields"] or not all(type(field) is str and field for field in statement["fields"]) or len(statement["fields"]) != len(set(statement["fields"])):
            raise SemanticRequestError("financial statement is invalid")
        operation = "financial_statement"
        public = {"kind": kind, "security": {"asset_class": "equity", "venue": venue, "symbol": symbol}, "statement": {"type": statement["type"], "presentation": statement["presentation"], "period": dict(period), "fields": list(statement["fields"])}}
        return public, venue, operation, symbol, {}
    raise SemanticRequestError("semantic request kind is unsupported")


def _scenario_id(semantic: Mapping[str, Any]) -> str:
    """Resolve the business capability, never a provider implementation."""
    kind = semantic["kind"]
    if kind == "market_quote":
        return "realtime.equity.%s.v1" % semantic["operation"]
    if kind == "historical":
        return "historical.%s.v1" % semantic["operation"]
    statement = semantic["statement"]
    if statement["presentation"] == "as_reported":
        return "financial.income_statement.as_reported.specified_period.v1"
    if statement["fields"]:
        return "financial.direct_line_items.specified_period.v1"
    return {
        "income": "financial.income_statement.standard.specified_period.v1",
        "balance": "financial.balance_sheet.standard.specified_period.v1",
        "cash_flow": "financial.cash_flow.standard.specified_period.v1",
    }[statement["type"]]


def _render(route: _Route, symbol: str, details: Mapping[str, Any]) -> dict[str, Any]:
    if route.parser_id == ALPHAVANTAGE_GLOBAL_QUOTE_V1:
        return {"function": "GLOBAL_QUOTE", "symbol": symbol, "entitlement": "realtime"}
    if route.parser_id == HANGSENG_HK_L1_V1:
        return {"stockObject": [symbol], "pageNo": 1, "pageSize": 1}
    raise AssertionError("unrenderable fixed route")


def _gateway_envelope(value: Any) -> tuple[Any, str | None]:
    """Accept a private data-Gateway envelope; its usage is never public token data."""
    if type(value) is dict and "raw" in value:
        if not set(value).issubset({"raw", "as_of", "source", "usage"}):
            raise ValueError("gateway envelope is invalid")
        raw, as_of = value["raw"], value.get("as_of")
    else:
        raw, as_of = value, None
    if as_of is not None and (type(as_of) is not str or not as_of):
        raise ValueError("gateway as_of is invalid")
    return raw, as_of


def _semantic_resolution(value: Any) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    """Accept the resolver contract, keeping model and data-Gateway receipts separate."""
    if isinstance(value, Mapping):
        return value, None
    semantic, usage = getattr(value, "semantic", None), getattr(value, "usage", None)
    if not isinstance(semantic, Mapping) or usage is not None and not isinstance(usage, Mapping):
        raise SemanticRequestError("semantic resolver result is invalid")
    return semantic, usage


def _public_usage(value: Mapping[str, Any] | None, request_id: str) -> dict[str, Any]:
    """Forward an actual receipt, or mark token measurement unavailable."""
    allowed = {"receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"}
    if value is not None and set(value) == allowed:
        tokens = (value.get("input_tokens"), value.get("output_tokens"), value.get("total_tokens"))
        if not any(type(token) is not int or isinstance(token, bool) or token < 0 for token in tokens) and tokens[2] == tokens[0] + tokens[1] and not any(type(value[key]) is not str or not value[key] for key in allowed - {"input_tokens", "output_tokens", "total_tokens"}) and value["issuer"] == "qveris_model_gateway" and value["request_id"] == request_id and value["cache_status"] in {"miss", "not_reported"}:
            return dict(value)
    return {"receipt_id": "unavailable", "measurement_version": "not_measured", "cache_status": "unavailable", "request_id": request_id if type(request_id) is str and request_id else "unavailable", "issuer": "unavailable", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _public_data(route: _Route, parsed: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Project the two admitted quote shapes into get-response/v1 data."""
    if route.parser_id == ALPHAVANTAGE_GLOBAL_QUOTE_V1:
        values = (("last_price", "close", "USD_per_share"),) if route.scenario_id.endswith("last_price.v1") else (
            ("open", "open", "USD_per_share"), ("high", "high", "USD_per_share"),
            ("low", "low", "USD_per_share"), ("close", "close", "USD_per_share"),
            ("volume", "volume", "shares"), ("previous_close", "previous_close", "USD_per_share"),
            ("change", "change", "USD_per_share"), ("change_percent", "change_percent", "percent"),
        )
        as_of = parsed["trade_date"]
        return {
            "kind": "realtime_quote",
            "quote": {
                "instrument": {"symbol": parsed["symbol"], "market": "US"},
                "fields": {name: {"value": parsed[key], "unit": unit, "as_of": as_of, "nil": False} for name, key, unit in values},
            },
        }, as_of
    if route.parser_id == HANGSENG_HK_L1_V1:
        as_of = parsed["timestamp"]
        return {
            "kind": "realtime_quote",
            "quote": {
                "instrument": {"symbol": parsed["symbol"], "market": "HKEX"},
                "fields": {
                    "bid": {"value": parsed["bid"], "unit": "HKD_per_share", "as_of": as_of, "nil": False},
                    "ask": {"value": parsed["ask"], "unit": "HKD_per_share", "as_of": as_of, "nil": False},
                    "bid_size": {"value": parsed["bid_size"], "unit": "lots", "as_of": as_of, "nil": False},
                    "ask_size": {"value": parsed["ask_size"], "unit": "lots", "as_of": as_of, "nil": False},
                },
            },
        }, as_of
    raise AssertionError("unprojectable fixed route")


class PublicGetAdapter:
    """Injectable, one-model/one-Gateway-call implementation of public GET."""

    def __init__(self, semantic_resolver: Callable[..., Any], gateway_execute: Callable[..., Any], *, agent_variant_id: str, agent_version: str, get_variant_id: str, get_version: str, model_identifier: str, model_version: str, model_config_digest: str) -> None:
        self.semantic_resolver, self.gateway_execute = semantic_resolver, gateway_execute
        self.identity = {"agent_variant_id": agent_variant_id, "agent_version": agent_version, "get_variant_id": get_variant_id, "get_version": get_version, "model_identifier": model_identifier, "model_version": model_version, "model_config_digest": model_config_digest}

    def _evidence(self, tool_executions: int, *, semantic_ms: float, tool_ms: float, total_ms: float) -> ExecutionEvidence:
        return ExecutionEvidence(**self.identity, agent_invocations=1, structured_outputs=1, tool_executions=tool_executions, tools_used=("get",) if tool_executions else (), semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=total_ms)

    @staticmethod
    def _ms(start_ns: int) -> float:
        return max(0.0, (time.monotonic_ns() - start_ns) / 1_000_000)

    @staticmethod
    def _terminal(status: str, reason: str, usage: Mapping[str, Any] | None = None, request_id: str = "") -> dict[str, Any]:
        response: dict[str, Any] = {"schema_version": "get-response/v1", "status": status, "data": None, "clarification": None, "terminal_reason": None}
        if status == "needs_clarification":
            response["clarification"] = reason
        else:
            response["terminal_reason"] = reason
        response["meta"] = {"usage": _public_usage(usage, request_id)}
        return normalize_response(response)

    def run(self, query: str, *, request_id: str, idempotency_key: str) -> PublicGetResult:
        # `idempotency_key` is passed only to the private Gateway and is never public.
        started = time.monotonic_ns()
        semantic_started = started
        tool_ms = 0.0
        try:
            semantic_value, model_usage = _semantic_resolution(self.semantic_resolver(query, request_id=request_id))
            semantic, venue, operation, symbol, details = _validated_semantic(semantic_value)
        except SemanticGatewayError as exc:
            reason = exc.code if exc.code.startswith("semantic_") else "semantic_" + exc.code
            return PublicGetResult(self._terminal("error", reason, request_id=request_id), self._evidence(0, semantic_ms=self._ms(semantic_started), tool_ms=tool_ms, total_ms=self._ms(started)))
        except SemanticRequestError as exc:
            status = "needs_clarification" if exc.needs_clarification else "error"
            return PublicGetResult(self._terminal(status, exc.code, request_id=request_id), self._evidence(0, semantic_ms=self._ms(semantic_started), tool_ms=tool_ms, total_ms=self._ms(started)))
        except Exception:
            return PublicGetResult(self._terminal("error", "semantic_internal_error", request_id=request_id), self._evidence(0, semantic_ms=self._ms(semantic_started), tool_ms=tool_ms, total_ms=self._ms(started)))
        semantic_ms = self._ms(semantic_started)
        entry = catalog_entry(venue, _scenario_id(semantic))
        route = _DISPATCHABLE.get((venue, operation))
        if entry is None or entry.disposition != "dispatchable" or route is None:
            return PublicGetResult(self._terminal("unsupported", "route_%s" % (entry.disposition if entry is not None else "unmapped"), model_usage, request_id), self._evidence(0, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        tool_started = time.monotonic_ns()
        try:
            private = self.gateway_execute(route.tool_id, _render(route, symbol, details), request_id=request_id, idempotency_key=idempotency_key)
            tool_ms = self._ms(tool_started)
            raw, _gateway_as_of = _gateway_envelope(private)
            parsed = parse_provider_payload(route.parser_id, raw, expected_symbol=symbol)
            data, parsed_as_of = _public_data(route, parsed)
        except ToolGatewayError as exc:
            tool_ms = self._ms(tool_started)
            return PublicGetResult(self._terminal("error", "tool_" + exc.code, model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        except ProviderPayloadParseError:
            return PublicGetResult(self._terminal("error", "provider_payload_invalid", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        except Exception:
            return PublicGetResult(self._terminal("error", "tool_internal_error", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        # Tool envelopes do not yet carry a contract-proven source-data timestamp.
        # The raw parser is authoritative only for the two routes that contain one.
        source_as_of = parsed_as_of
        if not source_as_of:
            return PublicGetResult(self._terminal("error", "source_time_missing", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        response: dict[str, Any] = {"schema_version": "get-response/v1", "status": "success", "resolved_request": {"suite": "realtime_quote", "accepted_variant_id": route.scenario_id.replace(".", "-").replace("_", "-")}, "data": data, "as_of": source_as_of, "source": route.source, "clarification": None, "terminal_reason": None}
        response["meta"] = {"usage": _public_usage(model_usage, request_id)}
        return PublicGetResult(normalize_response(response), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))


__all__ = ["PublicGetAdapter", "SemanticRequestError"]

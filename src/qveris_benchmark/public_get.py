"""One-pass semantic-to-fixed-route public GET adapter.

The injected resolver is the only model boundary.  It may describe a user
request, but it cannot select a provider, a tool, a parser, or tool
parameters.  Everything after that boundary is deterministic and makes at
most one injected Gateway call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import re
import time
from typing import Any

from .domain_route_contract import RoutePlan, RouteProjection
from . import domain_routes_financial, domain_routes_historical, domain_routes_realtime
from .provider_payload import ProviderPayloadParseError
from .response_contract import normalize_response
from .run_backend import ExecutionEvidence, PublicGetResult
from .qveris_model_gateway import SemanticGatewayError
from .qveris_tool_gateway import ToolGatewayError
from .runtime_catalog import catalog_entry


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_US = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\Z")
_CN = re.compile(r"[0-9]{6}\Z")
_HK = re.compile(r"[0-9]{5}\Z")
_JP = re.compile(r"[0-9]{4}\Z")
_GB_DE = re.compile(r"[A-Z][A-Z0-9-]{0,30}\Z")
_VENUES = frozenset({"US", "SSE", "SZSE", "HKEX", "JP", "GB", "DE"})
_QUOTE_OPS = frozenset({
    "quote_snapshot", "last_price", "bid_ask_l1", "volume_turnover_snapshot",
    "latest_trade", "extended_hours_price", "trading_status", "batch_quote_snapshot",
})
_QUOTE_FIELDS = {
    "quote_snapshot": frozenset({"open", "high", "low", "last_price", "previous_close", "change", "change_percent", "volume", "amount"}),
    "batch_quote_snapshot": frozenset({"open", "high", "low", "last_price", "previous_close", "change", "change_percent", "volume", "amount"}),
    "last_price": frozenset({"last_price"}),
    "latest_trade": frozenset({"last_price"}),
    "bid_ask_l1": frozenset({"bid", "ask", "bid_size", "ask_size"}),
    "volume_turnover_snapshot": frozenset({"volume", "amount"}),
    "extended_hours_price": frozenset({"extended_hours_price"}),
    "trading_status": frozenset({"trading_status"}),
}
_HISTORICAL_OPS = frozenset({"daily_bars", "weekly_bars", "monthly_bars", "intraday_bars", "corporate_actions", "adjustment_factors", "trading_calendar"})
_BAR_OPS = frozenset({"daily_bars", "weekly_bars", "monthly_bars", "intraday_bars"})
_FINANCIAL_FIELD_ALIASES = {
    "operating_cash_flow": "net_cash_from_operating",
    "investing_cash_flow": "net_cash_from_investing",
    "financing_cash_flow": "net_cash_from_financing",
}
_FINANCIAL_FIELDS = {
    "income": frozenset({"revenue", "cost_of_revenue", "gross_profit", "research_and_development_expense", "selling_general_and_administrative_expense", "operating_income", "income_before_tax", "income_tax_expense", "net_income"}),
    "balance": frozenset({"total_assets", "total_liabilities", "total_equity"}),
    "cash_flow": frozenset({"net_cash_from_operating", "net_cash_from_investing", "net_cash_from_financing", "net_increase_in_cash", "cash_and_cash_equivalents_at_end"}),
}


class SemanticRequestError(ValueError):
    """A model output did not satisfy the public semantic contract."""

    def __init__(self, code: str = "semantic_schema_invalid", *, needs_clarification: bool = False) -> None:
        self.code = "request_incomplete" if needs_clarification else "semantic_schema_invalid"
        self.needs_clarification = needs_clarification
        super().__init__(self.code)


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
    if venue == "JP":
        local = code.removesuffix(".T")
        if _JP.fullmatch(local):
            return venue, local + ".T"
    if venue == "GB":
        local = code.removesuffix(".L")
        if _GB_DE.fullmatch(local):
            return venue, local + ".L"
    if venue == "DE":
        local = code.removesuffix(".DE")
        if _GB_DE.fullmatch(local):
            return venue, local + ".DE"
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
        if type(request) is not dict or set(request) not in ({"kind", "security", "operation"}, {"kind", "security", "operation", "requested_fields"}, {"kind", "securities", "operation"}, {"kind", "securities", "operation", "requested_fields"}):
            raise SemanticRequestError("market quote has an invalid schema")
        item = request
        operation = item["operation"]
        if type(operation) is not str or operation not in _QUOTE_OPS:
            raise SemanticRequestError("market quote operation is invalid")
        requested_fields = item.get("requested_fields")
        if requested_fields is not None and (type(requested_fields) is not list or not requested_fields or len(requested_fields) != len(set(requested_fields)) or any(type(field) is not str or field not in _QUOTE_FIELDS[operation] for field in requested_fields)):
            raise SemanticRequestError("market quote requested_fields is invalid")
        if "security" in item:
            if operation == "batch_quote_snapshot":
                raise SemanticRequestError("batch quote requires securities")
            venue, symbol = _security(item["security"])
            public = {"kind": kind, "security": {"asset_class": "equity", "venue": venue, "symbol": symbol}, "operation": operation}
            if requested_fields is not None:
                public["requested_fields"] = list(requested_fields)
            return public, venue, operation, symbol, {}
        if operation != "batch_quote_snapshot" or type(item["securities"]) is not list or not 1 <= len(item["securities"]) <= 50:
            raise SemanticRequestError("batch quote request is invalid")
        resolved = [_security(security) for security in item["securities"]]
        venue = resolved[0][0] if resolved else ""
        if any(item_venue != venue for item_venue, _ in resolved) or len({symbol for _, symbol in resolved}) != len(resolved):
            raise SemanticRequestError("batch quote securities are invalid")
        public = {"kind": kind, "securities": [{"asset_class": "equity", "venue": item_venue, "symbol": symbol} for item_venue, symbol in resolved], "operation": operation}
        if requested_fields is not None:
            public["requested_fields"] = list(requested_fields)
        return public, venue, operation, "", {}
    if kind == "historical":
        required = {"kind", "security", "operation", "start_date", "end_date"}
        if type(request) is not dict or not required.issubset(request) or not set(request).issubset(required | {"adjustment", "interval"}):
            raise SemanticRequestError("historical request has an invalid schema")
        item = request
        venue, symbol = _security(item["security"])
        requested_operation, start, end = item["operation"], _iso_date(item["start_date"], "start_date"), _iso_date(item["end_date"], "end_date")
        if type(requested_operation) is not str or requested_operation not in _HISTORICAL_OPS or start > end:
            raise SemanticRequestError("historical request is invalid")
        operation = "daily_bars" if requested_operation in {"weekly_bars", "monthly_bars"} else requested_operation
        expected_interval = {"weekly_bars": "weekly", "monthly_bars": "monthly"}.get(requested_operation)
        interval = item.get("interval", expected_interval or ("daily" if operation == "daily_bars" else "intraday" if operation == "intraday_bars" else None))
        if operation == "daily_bars":
            if interval not in {"daily", "weekly", "monthly"} or expected_interval is not None and interval != expected_interval:
                raise SemanticRequestError("historical interval is invalid")
        elif operation == "intraday_bars":
            if interval not in {"intraday", "5min", "15min", "30min", "60min"}:
                raise SemanticRequestError("historical interval is invalid")
        elif "interval" in item:
            raise SemanticRequestError("historical interval is invalid")
        adjustment = item.get("adjustment", "unadjusted" if operation in _BAR_OPS else "not_applicable")
        if type(adjustment) is not str or (operation in _BAR_OPS and adjustment not in {"adjusted", "unadjusted"}) or (operation not in _BAR_OPS and adjustment != "not_applicable"):
            raise SemanticRequestError("historical adjustment is invalid")
        public = {"kind": kind, "security": {"asset_class": "equity", "venue": venue, "symbol": symbol}, "operation": operation, "adjustment": adjustment, "start_date": start, "end_date": end}
        if interval is not None:
            public["interval"] = interval
        return public, venue, operation, symbol, {"start_date": start, "end_date": end, "adjustment": adjustment, "interval": interval}
    if kind == "financial_statement":
        item = _exact_mapping(request, {"kind", "security", "statement"}, "financial statement")
        venue, symbol = _security(item["security"])
        statement = _exact_mapping(item["statement"], {"type", "presentation", "period", "fields"}, "statement")
        if type(statement["period"]) is not dict or set(statement["period"]) not in ({"kind", "fiscal_year", "fiscal_period"}, {"kind", "basis", "frequency"}):
            raise SemanticRequestError("statement period has an invalid schema")
        period = statement["period"]
        if statement["type"] not in {"income", "balance", "cash_flow"} or statement["presentation"] not in {"standardized", "as_reported"} or (statement["presentation"] == "as_reported" and statement["type"] != "income") or type(statement["fields"]) is not list or not statement["fields"] or not all(type(field) is str and field for field in statement["fields"]):
            raise SemanticRequestError("financial statement is invalid")
        fields = [_FINANCIAL_FIELD_ALIASES.get(field, field) for field in statement["fields"]]
        if len(fields) != len(set(fields)) or not set(fields).issubset(_FINANCIAL_FIELDS[statement["type"]]):
            raise SemanticRequestError("financial statement fields are invalid")
        if period["kind"] == "specified_period":
            if type(period["fiscal_year"]) is not int or type(period["fiscal_period"]) is not str:
                raise SemanticRequestError("financial statement is invalid")
        elif period["kind"] == "latest":
            if set(period) != {"kind", "basis", "frequency"} or period["basis"] not in {"filed", "report"} or period["frequency"] not in {"annual", "quarter"}:
                raise SemanticRequestError("financial statement is invalid")
        else:
            raise SemanticRequestError("financial statement is invalid")
        operation = "financial_statement"
        public = {"kind": kind, "security": {"asset_class": "equity", "venue": venue, "symbol": symbol}, "statement": {"type": statement["type"], "presentation": statement["presentation"], "period": dict(period), "fields": fields}}
        return public, venue, operation, symbol, {}
    raise SemanticRequestError("semantic request kind is unsupported")


def _scenario_id(semantic: Mapping[str, Any]) -> str:
    """Resolve the business capability, never a provider implementation."""
    kind = semantic["kind"]
    if kind == "market_quote":
        return "realtime.equity.%s.v1" % semantic["operation"]
    if kind == "historical":
        operation = semantic["operation"]
        return "historical.%s.%s.v1" % (operation, semantic["adjustment"]) if operation in _BAR_OPS else "historical.%s.v1" % operation
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


def _domain_module(semantic: Mapping[str, Any]) -> Any:
    return {
        "market_quote": domain_routes_realtime,
        "historical": domain_routes_historical,
        "financial_statement": domain_routes_financial,
    }[semantic["kind"]]


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
            return PublicGetResult(self._terminal("error", reason, exc.usage, request_id), self._evidence(0, semantic_ms=self._ms(semantic_started), tool_ms=tool_ms, total_ms=self._ms(started)))
        except SemanticRequestError as exc:
            status = "needs_clarification" if exc.needs_clarification else "error"
            return PublicGetResult(self._terminal(status, exc.code, request_id=request_id), self._evidence(0, semantic_ms=self._ms(semantic_started), tool_ms=tool_ms, total_ms=self._ms(started)))
        except Exception:
            return PublicGetResult(self._terminal("error", "semantic_internal_error", request_id=request_id), self._evidence(0, semantic_ms=self._ms(semantic_started), tool_ms=tool_ms, total_ms=self._ms(started)))
        semantic_ms = self._ms(semantic_started)
        domain = _domain_module(semantic)
        try:
            plan = domain.resolve(semantic)
        except Exception:
            return PublicGetResult(self._terminal("error", "route_render_invalid", model_usage, request_id), self._evidence(0, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        if type(plan) is not RoutePlan:
            entry = catalog_entry(venue, _scenario_id(semantic))
            return PublicGetResult(self._terminal("unsupported", "route_%s" % (entry.disposition if entry is not None else "unmapped"), model_usage, request_id), self._evidence(0, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        tool_started = time.monotonic_ns()
        try:
            private = self.gateway_execute(plan.tool_id, plan.params, request_id=request_id, idempotency_key=idempotency_key)
            tool_ms = self._ms(tool_started)
        except ToolGatewayError as exc:
            tool_ms = self._ms(tool_started)
            return PublicGetResult(self._terminal("error", "tool_" + exc.code, model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        except Exception:
            tool_ms = self._ms(tool_started)
            return PublicGetResult(self._terminal("error", "tool_gateway_internal_error", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        try:
            raw, _gateway_as_of = _gateway_envelope(private)
        except Exception:
            return PublicGetResult(self._terminal("error", "tool_gateway_payload_invalid", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        try:
            projection = domain.project(plan, raw)
            if type(projection) is not RouteProjection:
                raise ValueError("route projection is invalid")
        except ProviderPayloadParseError:
            return PublicGetResult(self._terminal("error", "provider_payload_invalid", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        except Exception:
            return PublicGetResult(self._terminal("error", "route_project_invalid", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        if plan.suite == "financial_statements" and not projection.data.get("facts"):
            return PublicGetResult(self._terminal("no_data", "provider_no_matching_period", model_usage, request_id), self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))
        response: dict[str, Any] = {"schema_version": projection.schema_version, "status": projection.status, "resolved_request": {"suite": plan.suite, "accepted_variant_id": plan.accepted_variant_id}, "data": dict(projection.data), "as_of": projection.as_of, "source": plan.source, "clarification": None, "terminal_reason": None}
        if projection.schema_version == "get-response/v2":
            response["as_of_status"] = projection.as_of_status
            response["coverage"] = {"complete": projection.status == "success", "missing_fields": list(projection.missing_fields)}
        response["meta"] = {"usage": _public_usage(model_usage, request_id)}
        try:
            public_response = normalize_response(response, suite=plan.suite)
        except Exception:
            public_response = self._terminal("error", "route_projection_invalid", model_usage, request_id)
        return PublicGetResult(public_response, self._evidence(1, semantic_ms=semantic_ms, tool_ms=tool_ms, total_ms=self._ms(started)))


__all__ = ["PublicGetAdapter", "SemanticRequestError"]

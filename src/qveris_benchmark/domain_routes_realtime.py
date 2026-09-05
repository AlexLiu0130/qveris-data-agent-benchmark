"""Deterministic routes and provider-free projections for realtime equities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from types import MappingProxyType
from typing import Any

from .domain_route_contract import RoutePlan, RouteProjection
from .provider_payload import (
    ALPHAVANTAGE_BULK_BID_ASK_V1,
    ALPHAVANTAGE_GLOBAL_QUOTE_V1,
    CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1,
    CNFP_REALTIME_QUOTE_V1,
    HANGSENG_HK_L1_V1,
    QVERIS_AFTER_HOURS_V1,
    parse_provider_payload,
)


_US = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\Z")
_CN = re.compile(r"[0-9]{6}(?:\.(?:SH|SZ))?\Z")
_HK = re.compile(r"[0-9]{5}(?:\.HK)?\Z")
_OPERATIONS = frozenset({
    "quote_snapshot", "last_price", "bid_ask_l1", "volume_turnover_snapshot",
    "latest_trade", "extended_hours_price", "trading_status", "batch_quote_snapshot",
})
_REQUESTED_FIELDS = {
    "quote_snapshot": frozenset({"open", "high", "low", "last_price", "previous_close", "change", "change_percent", "volume", "amount"}),
    "batch_quote_snapshot": frozenset({"open", "high", "low", "last_price", "previous_close", "change", "change_percent", "volume", "amount"}),
    "last_price": frozenset({"last_price"}),
    "latest_trade": frozenset({"last_price"}),
    "bid_ask_l1": frozenset({"bid", "ask", "bid_size", "ask_size"}),
    "volume_turnover_snapshot": frozenset({"volume", "amount"}),
    "extended_hours_price": frozenset({"extended_hours_price"}),
    "trading_status": frozenset({"trading_status"}),
}
SUPPORTED_KEYS = MappingProxyType({
    ("US", "realtime.equity.quote_snapshot.v1"): "alphavantage.global_quote.retrieve.v1.9b8a7c6d",
    ("US", "realtime.equity.last_price.v1"): "alphavantage.global_quote.retrieve.v1.9b8a7c6d",
    ("US", "realtime.equity.volume_turnover_snapshot.v1"): "alphavantage.global_quote.retrieve.v1.9b8a7c6d",
    ("US", "realtime.equity.bid_ask_l1.v1"): "alphavantage.realtime_bulk_bid_ask_prices.retrieve.v1.9b8a7c6d",
    ("US", "realtime.equity.extended_hours_price.v1"): "qveris_finance.mkt_after_hours",
    **{("SSE", "realtime.equity.%s.v1" % operation): "cn_financial_pro.real_time_quotation.v1" for operation in ("quote_snapshot", "last_price", "volume_turnover_snapshot", "latest_trade", "batch_quote_snapshot")},
    ("SSE", "realtime.equity.bid_ask_l1.v1"): "caidazi.get_real_time_record.execute.v1.7a43f96e",
    **{("SZSE", "realtime.equity.%s.v1" % operation): "cn_financial_pro.real_time_quotation.v1" for operation in ("quote_snapshot", "last_price", "volume_turnover_snapshot", "latest_trade", "batch_quote_snapshot")},
    ("SZSE", "realtime.equity.bid_ask_l1.v1"): "caidazi.get_real_time_record.execute.v1.7a43f96e",
    **{("HKEX", "realtime.equity.%s.v1" % operation): "hangseng_polysource.quote.hkshares.live.v2.dec427af" for operation in ("quote_snapshot", "last_price", "bid_ask_l1", "volume_turnover_snapshot", "latest_trade", "trading_status", "batch_quote_snapshot")},
})


def _timestamp(value: Any) -> str:
    if type(value) is not str or ("T" not in value and " " not in value):
        raise ValueError("source_time_missing")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("source_time_invalid") from exc
    return value


def _number(value: Any) -> str:
    if type(value) is bool or type(value) not in (int, float, str):
        raise ValueError("provider_value_invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("provider_value_invalid") from exc
    if not result.is_finite():
        raise ValueError("provider_value_invalid")
    return format(result, "f")


def _semantic_request(semantic: Any) -> Mapping[str, Any] | None:
    if not isinstance(semantic, Mapping):
        return None
    if semantic.get("kind") == "market_quote":
        return semantic
    request = semantic.get("request")
    return request if isinstance(request, Mapping) and request.get("kind") == "market_quote" else None


def _symbol(security: Any) -> tuple[str, str] | None:
    if not isinstance(security, Mapping) or security.get("asset_class") != "equity":
        return None
    venue = security.get("venue")
    code = security.get("symbol", security.get("local_code"))
    if venue not in {"US", "SSE", "SZSE", "HKEX"} or type(code) is not str:
        return None
    if venue == "US" and _US.fullmatch(code):
        return venue, code
    if venue in {"SSE", "SZSE"} and _CN.fullmatch(code):
        suffix = ".SH" if venue == "SSE" else ".SZ"
        return venue, code if code.endswith(suffix) else code + suffix
    if venue == "HKEX" and _HK.fullmatch(code):
        return venue, code if code.endswith(".HK") else code + ".HK"
    return None


def _symbols(request: Mapping[str, Any], operation: str) -> tuple[str, tuple[str, ...]] | None:
    if operation == "batch_quote_snapshot":
        items = request.get("securities")
        if type(items) not in (list, tuple) or not 1 <= len(items) <= 50:
            return None
        resolved = tuple(_symbol(item) for item in items)
    else:
        resolved = (_symbol(request.get("security")),)
    if any(item is None for item in resolved):
        return None
    venues = {item[0] for item in resolved if item is not None}
    values = tuple(item[1] for item in resolved if item is not None)
    if len(venues) != 1 or len(values) != len(set(values)):
        return None
    return venues.pop(), values


def _plan(venue: str, operation: str, symbols: tuple[str, ...], *, tool_id: str, params: Mapping[str, Any], parser_id: str, source: str, requested_fields: tuple[str, ...] = (), response_version: str = "v1") -> RoutePlan:
    scenario = "realtime.equity.%s.v1" % operation
    return RoutePlan(
        tool_id=tool_id,
        params=dict(params),
        parser_id=parser_id,
        suite="realtime_quote",
        accepted_variant_id=scenario.replace(".", "-").replace("_", "-"),
        source=source,
        context={
            "market": venue,
            "operation": operation,
            "symbols": symbols,
            "requirements": {"operation": operation, "symbols": symbols, "fields": requested_fields},
            "response_version": response_version,
        },
    )


def resolve(semantic: Any) -> RoutePlan | None:
    """Resolve validated public quote semantics without catalog/provider lookup."""
    request = _semantic_request(semantic)
    if request is None or type(request.get("operation")) is not str:
        return None
    operation = request["operation"]
    if operation not in _OPERATIONS:
        return None
    resolved = _symbols(request, operation)
    if resolved is None:
        return None
    venue, symbols = resolved
    symbol = symbols[0]
    requested = request.get("requested_fields", ())
    if type(requested) not in (list, tuple) or any(type(field) is not str for field in requested) or len(requested) != len(set(requested)) or any(field not in _REQUESTED_FIELDS[operation] for field in requested):
        return None
    requested_fields = tuple(requested)
    response_version = "v2" if operation == "batch_quote_snapshot" else "v1"
    if venue == "US":
        if operation in {"quote_snapshot", "last_price", "volume_turnover_snapshot"}:
            return _plan(venue, operation, symbols, tool_id="alphavantage.global_quote.retrieve.v1.9b8a7c6d", params={"function": "GLOBAL_QUOTE", "symbol": symbol, "entitlement": "realtime"}, parser_id=ALPHAVANTAGE_GLOBAL_QUOTE_V1, source="Alpha Vantage", requested_fields=requested_fields)
        if operation == "bid_ask_l1":
            return _plan(venue, operation, symbols, tool_id="alphavantage.realtime_bulk_bid_ask_prices.retrieve.v1.9b8a7c6d", params={"function": "REALTIME_BULK_BID_ASK_PRICES", "symbol": symbol, "datatype": "json", "entitlement": "realtime"}, parser_id=ALPHAVANTAGE_BULK_BID_ASK_V1, source="Alpha Vantage", requested_fields=requested_fields)
        if operation == "extended_hours_price":
            return _plan(venue, operation, symbols, tool_id="qveris_finance.mkt_after_hours", params={"symbol": symbol}, parser_id=QVERIS_AFTER_HOURS_V1, source="QVeris Finance", requested_fields=requested_fields)
        return None
    if venue in {"SSE", "SZSE"}:
        if operation in {"quote_snapshot", "last_price", "volume_turnover_snapshot", "latest_trade", "batch_quote_snapshot"}:
            indicators = "common" if venue == "SSE" else "all"
            return _plan(venue, operation, symbols, tool_id="cn_financial_pro.real_time_quotation.v1", params={"codes": ",".join(symbols), "indicators": indicators}, parser_id=CNFP_REALTIME_QUOTE_V1, source="CN Financial Pro", requested_fields=requested_fields, response_version=response_version)
        if operation == "bid_ask_l1":
            return _plan(venue, operation, symbols, tool_id="caidazi.get_real_time_record.execute.v1.7a43f96e", params={"symbol": symbol}, parser_id=CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1, source="Caidaizi", requested_fields=requested_fields)
        return None
    if venue == "HKEX":
        params = {"stockObject": list(symbols), "pageNo": 1, "pageSize": len(symbols)}
        if operation == "bid_ask_l1":
            return _plan(venue, operation, symbols, tool_id="hangseng_polysource.quote.hkshares.live.v2.dec427af", params=params, parser_id=HANGSENG_HK_L1_V1, source="Hang Seng", requested_fields=requested_fields)
        if operation in {"quote_snapshot", "last_price", "volume_turnover_snapshot", "latest_trade", "trading_status", "batch_quote_snapshot"}:
            return _plan(venue, operation, symbols, tool_id="hangseng_polysource.quote.hkshares.live.v2.dec427af", params=params, parser_id="hangseng_hk_domain_quote_v1", source="Hang Seng", requested_fields=requested_fields, response_version="v2" if operation == "trading_status" else response_version)
    return None


def _field(value: str, unit: str, as_of: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "as_of": as_of, "nil": False}


def _quote_data(symbol: str, market: str, as_of: str, fields: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    return {"kind": "realtime_quote", "quote": {"instrument": {"symbol": symbol, "market": market}, "fields": {name: _field(value, unit, as_of) for name, (value, unit) in fields.items()}}}


def _requested(plan: RoutePlan, fields: Mapping[str, tuple[str, str]], *, complete: tuple[str, ...] = ()) -> tuple[dict[str, tuple[str, str]], tuple[str, ...]]:
    requested = plan.context["requirements"]["fields"] or complete
    missing = tuple(field for field in requested if field not in fields)
    return ({field: fields[field] for field in requested if field in fields} if requested else dict(fields)), missing


def _batch_data(market: str, quotes: Mapping[str, tuple[str, Mapping[str, tuple[str, str]]]]) -> dict[str, Any]:
    return {"kind": "batch_realtime_quote", "quotes": {symbol: {"instrument": {"symbol": symbol, "market": market}, "fields": {name: _field(value, unit, as_of) for name, (value, unit) in fields.items()}} for symbol, (as_of, fields) in quotes.items()}}


def _batch_projection(market: str, quotes: Mapping[str, tuple[str, Mapping[str, tuple[str, str]]]]) -> RouteProjection:
    timestamps = {as_of for as_of, _ in quotes.values()}
    as_of = next(iter(timestamps)) if len(timestamps) == 1 else None
    return RouteProjection(
        _batch_data(market, quotes), as_of,
        status="success",
        missing_fields=(),
        schema_version="get-response/v2",
        as_of_status="known" if as_of is not None else "mixed",
    )


def _from_cn(plan: RoutePlan, raw: Any) -> RouteProjection:
    quotes = parse_provider_payload(CNFP_REALTIME_QUOTE_V1, raw, expected_symbol=plan.context["symbols"])["quotes"]
    operation, market = plan.context["operation"], plan.context["market"]
    selected: dict[str, tuple[str, Mapping[str, tuple[str, str]]]] = {}
    missing_fields: tuple[str, ...] = ()
    for quote in quotes:
        as_of = quote["timestamp"]
        values = {name: (quote[key], "unknown") for name, key in (("open", "open"), ("high", "high"), ("low", "low"), ("last_price", "close"), ("previous_close", "previous_close"), ("volume", "volume"), ("amount", "amount"))}
        if operation == "last_price": values = {"last_price": values["last_price"]}
        elif operation == "volume_turnover_snapshot": values = {key: values[key] for key in ("volume", "amount")}
        elif operation == "latest_trade": values = {"last_price": values["last_price"]}
        values, missing_fields = _requested(plan, values)
        selected[quote["symbol"]] = (as_of, values)
    if operation == "batch_quote_snapshot":
        projection = _batch_projection(market, selected)
        return RouteProjection(projection.data, projection.as_of, status="partial" if missing_fields else "success", missing_fields=missing_fields, schema_version=projection.schema_version, as_of_status=projection.as_of_status)
    symbol = plan.context["symbols"][0]
    as_of, fields = selected[symbol]
    return RouteProjection(_quote_data(symbol, market, as_of, fields), as_of, status="partial" if missing_fields else "success", missing_fields=missing_fields)


def _from_hk(raw: Any, symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    try:
        rows = raw["data"]["data"]["rows"]
    except (KeyError, TypeError) as exc:
        raise ValueError("hangseng_hk_shape_invalid") from exc
    if type(rows) is not list or not rows:
        raise ValueError("hangseng_hk_shape_invalid")
    expected = set(symbols)
    result = []
    for row in rows:
        if type(row) is not dict or type(row.get("stockCode")) is not str or not re.fullmatch(r"[0-9]{5}", row["stockCode"]):
            raise ValueError("hangseng_hk_identity_invalid")
        symbol = row["stockCode"] + ".HK"
        if symbol not in expected:
            raise ValueError("hangseng_hk_symbol_mismatch")
        as_of = _timestamp(row.get("tradingTimestamp"))
        values: dict[str, str] = {}
        for target, source in (("open", "openPrice"), ("high", "highPrice"), ("low", "lowPrice"), ("last_price", "latestPrice"), ("previous_close", "prevClosePrice"), ("volume", "turnoverVolumeLot"), ("amount", "turnoverValue")):
            if source in row and row[source] is not None:
                values[target] = _number(row[source])
        if "last_price" not in values:
            raise ValueError("hangseng_hk_last_price_missing")
        if {"open", "high", "low"}.issubset(values):
            if not Decimal(values["low"]) <= min(Decimal(values["open"]), Decimal(values["last_price"])) <= max(Decimal(values["open"]), Decimal(values["last_price"])) <= Decimal(values["high"]):
                raise ValueError("hangseng_hk_ohlc_invalid")
        if type(row.get("tradeStatus")) is not str or not row["tradeStatus"]:
            status = None
        else:
            status = row["tradeStatus"]
        currency = row.get("currency") if type(row.get("currency")) is str and re.fullmatch(r"[A-Z]{3}", row["currency"]) else "unknown"
        result.append({"symbol": symbol, "as_of": as_of, "values": values, "status": status, "currency": currency})
    if {quote["symbol"] for quote in result} != expected:
        raise ValueError("hangseng_hk_symbol_mismatch")
    return result


def _from_hk_quote(plan: RoutePlan, raw: Any) -> RouteProjection:
    operation = plan.context["operation"]
    selected: dict[str, tuple[str, Mapping[str, tuple[str, str]]]] = {}
    missing_fields: tuple[str, ...] = ()
    for quote in _from_hk(raw, plan.context["symbols"]):
        price_unit = quote["currency"] + "_per_share" if quote["currency"] != "unknown" else "unknown"
        fields = {name: (value, price_unit if name not in {"volume", "amount"} else "unknown") for name, value in quote["values"].items()}
        if operation == "last_price" or operation == "latest_trade": fields = {"last_price": fields["last_price"]}
        elif operation == "volume_turnover_snapshot":
            fields = {key: fields[key] for key in ("volume", "amount") if key in fields}
            if not fields: raise ValueError("hangseng_hk_volume_turnover_missing")
        elif operation == "trading_status":
            if quote["status"] is None: raise ValueError("hangseng_hk_status_missing")
            selected[quote["symbol"]] = (quote["as_of"], {"trading_status": (quote["status"], "provider_status")})
            continue
        fields, missing_fields = _requested(plan, fields)
        selected[quote["symbol"]] = (quote["as_of"], fields)
    if operation == "batch_quote_snapshot":
        projection = _batch_projection("HKEX", selected)
        return RouteProjection(projection.data, projection.as_of, status="partial" if missing_fields else "success", missing_fields=missing_fields, schema_version=projection.schema_version, as_of_status=projection.as_of_status)
    symbol = plan.context["symbols"][0]
    as_of, fields = selected[symbol]
    if operation == "trading_status":
        return RouteProjection(
            {"kind": "market_status", "instrument": {"symbol": symbol, "market": "HKEX"}, "status": fields["trading_status"][0]},
            as_of,
            schema_version="get-response/v2",
        )
    return RouteProjection(_quote_data(symbol, "HKEX", as_of, fields), as_of, status="partial" if missing_fields else "success", missing_fields=missing_fields)


def project(plan: RoutePlan, raw: Any) -> RouteProjection:
    """Parse one fixed route and return a provider-free, source-timed projection."""
    if plan.suite != "realtime_quote":
        raise ValueError("realtime_route_plan_required")
    parser_id, operation, market, symbol = plan.parser_id, plan.context["operation"], plan.context["market"], plan.context["symbols"][0]
    if parser_id == CNFP_REALTIME_QUOTE_V1:
        return _from_cn(plan, raw)
    if parser_id == "hangseng_hk_domain_quote_v1":
        return _from_hk_quote(plan, raw)
    parsed = parse_provider_payload(parser_id, raw, expected_symbol=symbol)
    if parser_id == ALPHAVANTAGE_GLOBAL_QUOTE_V1:
        as_of = parsed["trade_date"]
        fields = {"last_price": (parsed["close"], "USD_per_share")} if operation == "last_price" else {name: (parsed[key], unit) for name, key, unit in (("open", "open", "USD_per_share"), ("high", "high", "USD_per_share"), ("low", "low", "USD_per_share"), ("last_price", "close", "USD_per_share"), ("volume", "volume", "shares"), ("previous_close", "previous_close", "USD_per_share"), ("change", "change", "USD_per_share"), ("change_percent", "change_percent", "percent"))}
        if operation == "volume_turnover_snapshot":
            fields = {"volume": fields["volume"]}
    elif parser_id == ALPHAVANTAGE_BULK_BID_ASK_V1:
        quote = parsed["quotes"][0]; as_of = quote["timestamp"]
        fields = {key: (quote[key], "unknown") for key in ("bid", "ask", "bid_size", "ask_size")}
    elif parser_id == QVERIS_AFTER_HOURS_V1:
        as_of = parsed["timestamp"]
        fields = {"extended_hours_price": (parsed["price"], parsed.get("currency", "unknown") + "_per_share" if parsed.get("currency") else "unknown")}
    elif parser_id == CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1:
        as_of = parsed["trade_time"]
        fields = {key: (parsed[key], "unknown") for key in ("bid", "ask")}
    elif parser_id == HANGSENG_HK_L1_V1:
        as_of = parsed["timestamp"]
        fields = {"bid": (parsed["bid"], "HKD_per_share"), "ask": (parsed["ask"], "HKD_per_share"), "bid_size": (parsed["bid_size"], "lots"), "ask_size": (parsed["ask_size"], "lots")}
    else:
        raise ValueError("realtime_parser_unsupported")
    fields, missing_fields = _requested(
        plan, fields,
        complete=("volume", "amount") if parser_id == ALPHAVANTAGE_GLOBAL_QUOTE_V1 and operation == "volume_turnover_snapshot" else (),
    )
    return RouteProjection(
        _quote_data(symbol, market, as_of, fields), as_of,
        status="partial" if missing_fields else "success",
        missing_fields=missing_fields,
        schema_version="get-response/v2" if plan.context["response_version"] == "v2" else "get-response/v1",
    )


__all__ = ["SUPPORTED_KEYS", "project", "resolve"]

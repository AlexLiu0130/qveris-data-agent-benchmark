"""Fixed historical GET routes and strict provider-free projections."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from types import MappingProxyType
from typing import Any

from .domain_route_contract import RoutePlan, RouteProjection
from .provider_payload import (
    ALPHAVANTAGE_INTRADAY_BARS_V1,
    CNFP_ADJUSTMENT_FACTOR_V1,
    CNFP_HKEX_TRADING_CALENDAR_V1,
    CNFP_INTRADAY_BARS_V1,
    FIU_SSE_DIVIDENDS_V1,
    parse_provider_payload,
)


_US = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\Z")
_CN = re.compile(r"[0-9]{6}(?:\.(?:SH|SZ))?\Z")
_HK = re.compile(r"[0-9]{5}(?:\.HK)?\Z")
_JP = re.compile(r"[0-9]{4}(?:\.T)?\Z")
_GB_DE = re.compile(r"[A-Z][A-Z0-9-]{0,30}(?:\.(?:L|DE))?\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_OPS = frozenset({"daily_bars", "intraday_bars", "corporate_actions", "adjustment_factors", "trading_calendar"})
_BARS = frozenset({"daily_bars", "intraday_bars"})

# The map is intentionally only routes whose renderer and projection live here.
# It is imported by the catalog reconciler; ``resolve`` still validates every
# semantic detail before one of these identifiers can be dispatched.
SUPPORTED_KEYS = MappingProxyType({
    ("US", "historical.daily_bars.unadjusted.v1"): "tiingo.daily.ticker.prices.list.v1",
    ("US", "historical.daily_bars.adjusted.v1"): "tiingo.daily.ticker.prices.list.v1",
    ("US", "historical.intraday_bars.unadjusted.v1"): "alphavantage.time_series_intraday.retrieve.v1.1e18340d",
    ("US", "historical.intraday_bars.adjusted.v1"): "alphavantage.time_series_intraday.retrieve.v1.1e18340d",
    ("US", "historical.corporate_actions.v1"): "tiingo.daily.ticker.prices.list.v1",
    ("US", "historical.adjustment_factors.v1"): "tiingo.daily.ticker.prices.list.v1",
    ("SSE", "historical.daily_bars.unadjusted.v1"): "cn_financial_pro.history_quotation.v1",
    ("SSE", "historical.daily_bars.adjusted.v1"): "cn_financial_pro.adjusted_price.v1",
    ("SSE", "historical.intraday_bars.unadjusted.v1"): "cn_financial_pro.hf_basic_quotation.v1",
    ("SSE", "historical.corporate_actions.v1"): "fiu_mcp_server.postapihsf10summarycadividends.create.v2.88186c04",
    ("SSE", "historical.adjustment_factors.v1"): "cn_financial_pro.adjusted_price.v1",
    ("SZSE", "historical.daily_bars.unadjusted.v1"): "cn_financial_pro.history_quotation.v1",
    ("SZSE", "historical.daily_bars.adjusted.v1"): "cn_financial_pro.adjusted_price.v1",
    ("SZSE", "historical.intraday_bars.unadjusted.v1"): "cn_financial_pro.hf_basic_quotation.v1",
    ("SZSE", "historical.adjustment_factors.v1"): "cn_financial_pro.adjusted_price.v1",
    ("HKEX", "historical.daily_bars.unadjusted.v1"): "hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924",
    ("HKEX", "historical.corporate_actions.v1"): "fiu_mcp_server.postapihkf10summarycadividends.create.v2.d3fe48e2",
    ("HKEX", "historical.trading_calendar.v1"): "cn_financial_pro.trade_dates.v1",
    **{(market, "historical.daily_bars.unadjusted.v1"): "financialmodelingprep.historical_price_eod.full.retrieve.v1.f9aefe40" for market in ("JP", "GB", "DE")},
    **{(market, "historical.weekly_bars.unadjusted.v1"): tool for market, tool in (("US", "tiingo.daily.ticker.prices.list.v1"), ("SSE", "cn_financial_pro.history_quotation.v1"), ("SZSE", "cn_financial_pro.history_quotation.v1"), ("HKEX", "hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924"))},
    **{(market, "historical.monthly_bars.unadjusted.v1"): tool for market, tool in (("US", "tiingo.daily.ticker.prices.list.v1"), ("SSE", "cn_financial_pro.history_quotation.v1"), ("SZSE", "cn_financial_pro.history_quotation.v1"), ("HKEX", "hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924"))},
})


def _request(semantic: Any) -> Mapping[str, Any] | None:
    if not isinstance(semantic, Mapping):
        return None
    if semantic.get("kind") == "historical":
        return semantic
    request = semantic.get("request")
    return request if isinstance(request, Mapping) and request.get("kind") == "historical" else None


def _date_value(value: Any) -> str | None:
    if type(value) is not str or _DATE.fullmatch(value) is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _symbol(security: Any) -> tuple[str, str] | None:
    if not isinstance(security, Mapping) or security.get("asset_class") != "equity":
        return None
    venue = security.get("venue")
    code = security.get("symbol", security.get("local_code"))
    if type(code) is not str or venue not in {"US", "SSE", "SZSE", "HKEX", "JP", "GB", "DE"}:
        return None
    if venue == "US" and _US.fullmatch(code):
        return venue, code
    if venue in {"SSE", "SZSE"} and _CN.fullmatch(code):
        suffix = ".SH" if venue == "SSE" else ".SZ"
        return venue, code if code.endswith(suffix) else code + suffix
    if venue == "HKEX" and _HK.fullmatch(code):
        return venue, code if code.endswith(".HK") else code + ".HK"
    if venue == "JP" and _JP.fullmatch(code):
        return venue, code if code.endswith(".T") else code + ".T"
    if venue == "GB" and _GB_DE.fullmatch(code):
        return venue, code if code.endswith(".L") else code + ".L"
    if venue == "DE" and _GB_DE.fullmatch(code):
        return venue, code if code.endswith(".DE") else code + ".DE"
    return None


def _scenario(operation: str, adjustment: str, interval: str = "") -> str:
    if operation == "daily_bars" and interval in {"weekly", "monthly"}:
        return "historical.%s_bars.%s.v1" % (interval, adjustment)
    return "historical.%s.%s.v1" % (operation, adjustment) if operation in _BARS else "historical.%s.v1" % operation


def _plan(venue: str, operation: str, adjustment: str, symbol: str, start: str, end: str, interval: str, *, tool_id: str, params: Mapping[str, Any], parser_id: str, source: str, basis: str) -> RoutePlan:
    scenario = _scenario(operation, adjustment, interval)
    return RoutePlan(
        tool_id=tool_id,
        params=dict(params),
        parser_id=parser_id,
        suite="historical_price",
        accepted_variant_id=scenario.replace(".", "-").replace("_", "-"),
        source=source,
        context={"market": venue, "operation": operation, "adjustment": adjustment, "symbol": symbol, "start_date": start, "end_date": end, "interval": interval, "provider_adjustment_basis": basis, "as_of_basis": "last_observation"},
    )


def resolve(semantic: Any) -> RoutePlan | None:
    """Choose one fixed historical provider route; unsupported cells return None."""
    request = _request(semantic)
    if request is None or type(request.get("operation")) is not str:
        return None
    operation = request["operation"]
    resolved = _symbol(request.get("security"))
    start, end = _date_value(request.get("start_date")), _date_value(request.get("end_date"))
    if operation not in _OPS or resolved is None or start is None or end is None or start > end:
        return None
    venue, symbol = resolved
    adjustment = request.get("adjustment", "unadjusted" if operation in _BARS else "not_applicable")
    if type(adjustment) is not str or (operation in _BARS and adjustment not in {"adjusted", "unadjusted"}) or (operation not in _BARS and adjustment != "not_applicable"):
        return None
    interval = request.get("interval", "daily" if operation == "daily_bars" else "intraday" if operation == "intraday_bars" else "not_applicable")
    if type(interval) is not str or (operation == "daily_bars" and interval not in {"daily", "weekly", "monthly"}) or (operation == "intraday_bars" and interval not in {"intraday", "5min", "15min", "30min", "60min"}) or (operation not in _BARS and interval not in {"daily", "not_applicable"}):
        return None
    key = (venue, _scenario(operation, adjustment, interval))
    if key not in SUPPORTED_KEYS:
        return None
    if venue == "US":
        if operation == "daily_bars":
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"ticker": symbol, "startDate": start, "endDate": end}, parser_id="tiingo_daily_bars_v1", source="Tiingo", basis="tiingo_adjusted" if adjustment == "adjusted" else "as_reported")
        if operation == "intraday_bars":
            if start[:7] != end[:7]:
                return None  # Alpha's one-call month selector cannot cover two months.
            minute = interval if interval != "intraday" else "5min"
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"function": "TIME_SERIES_INTRADAY", "symbol": symbol, "interval": minute, "adjusted": adjustment == "adjusted", "extended_hours": False, "month": start[:7], "outputsize": "full", "datatype": "json"}, parser_id=ALPHAVANTAGE_INTRADAY_BARS_V1, source="Alpha Vantage", basis="provider_adjusted" if adjustment == "adjusted" else "as_reported")
        return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"ticker": symbol, "startDate": start, "endDate": end}, parser_id="tiingo_daily_events_v1", source="Tiingo", basis="provider_reported")
    if venue in {"SSE", "SZSE"}:
        if operation == "daily_bars":
            params = {"codes": symbol, "startdate": start, "enddate": end, "cps": 2, "interval": "D"} if adjustment == "adjusted" else {"codes": symbol, "indicators": "stock_all", "startdate": start, "enddate": end, "interval": "D", "cps": 1, "fill": "Blank"}
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params=params, parser_id="cnfp_daily_bars_v1", source="CN Financial Pro", basis="provider_adjusted" if adjustment == "adjusted" else "as_reported")
        if operation == "intraday_bars":
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"codes": symbol, "starttime": start + " 00:00:00", "endtime": end + " 23:59:59", "interval": interval if interval != "intraday" else "5"}, parser_id=CNFP_INTRADAY_BARS_V1, source="CN Financial Pro", basis="as_reported")
        if operation == "adjustment_factors":
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"codes": symbol, "startdate": start, "enddate": end, "cps": 2, "interval": "D"}, parser_id=CNFP_ADJUSTMENT_FACTOR_V1, source="CN Financial Pro", basis="provider_reported")
        return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"symbol": symbol, "startDate": start, "endDate": end, "sort": "asc"}, parser_id=FIU_SSE_DIVIDENDS_V1, source="FIU", basis="provider_reported")
    if venue == "HKEX":
        if operation == "daily_bars":
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"stockObject": [symbol], "beginDate": start, "endDate": end, "pageNo": 1, "pageSize": 500}, parser_id="hangseng_hk_daily_bars_v1", source="Hang Seng", basis="as_reported")
        if operation == "corporate_actions":
            return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"symbol": symbol, "type": "CD", "startDate": start, "endDate": end, "sort": "asc"}, parser_id="fiu_hk_corporate_actions_v1", source="FIU", basis="provider_reported")
        return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"marketcode": "212200", "date_type": "0"}, parser_id=CNFP_HKEX_TRADING_CALENDAR_V1, source="CN Financial Pro", basis="coverage_range")
    if venue in {"JP", "GB", "DE"}:
        return _plan(venue, operation, adjustment, symbol, start, end, interval, tool_id=SUPPORTED_KEYS[key], params={"symbol": symbol, "from": start, "to": end}, parser_id="fmp_historical_eod_v1", source="Financial Modeling Prep", basis="provider_basis_unknown")
    return None


def _number(value: Any) -> str:
    if type(value) not in (str, int, float) or isinstance(value, bool):
        raise ValueError("provider_value_invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("provider_value_invalid") from exc
    if not result.is_finite():
        raise ValueError("provider_value_invalid")
    return format(result, "f")


def _iso(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("provider_date_invalid")
    value = value[:10]
    if _date_value(value) is None:
        raise ValueError("provider_date_invalid")
    return value


def _rows(raw: Any) -> list[Mapping[str, Any]]:
    if type(raw) is not list or not raw or any(not isinstance(row, Mapping) for row in raw):
        raise ValueError("provider_shape_invalid")
    return raw


def _ohlcv(row: Mapping[str, Any], *, prefix: str, date_key: str, symbol_key: str, symbol: str) -> dict[str, Any]:
    if row.get(symbol_key) != symbol:
        raise ValueError(prefix + "_symbol_mismatch")
    values = {field: _number(row[field]) for field in ("open", "high", "low", "close", "volume")}
    if Decimal(values["volume"]) < 0 or Decimal(values["high"]) < max(Decimal(values["open"]), Decimal(values["close"])) or Decimal(values["low"]) > min(Decimal(values["open"]), Decimal(values["close"])):
        raise ValueError(prefix + "_ohlcv_invalid")
    result = {"date": _iso(row[date_key]), **values}
    if "amount" in row and row["amount"] is not None:
        result["amount"] = _number(row["amount"])
        if Decimal(result["amount"]) < 0:
            raise ValueError(prefix + "_amount_invalid")
    return result


def _tiingo(plan: RoutePlan, raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("result"), Mapping):
        raise ValueError("tiingo_shape_invalid")
    rows = _rows(raw["result"].get("data"))
    out = []
    for row in rows:
        # Tiingo's range endpoint binds ticker in the fixed route; it does not
        # echo it per daily row, so accepting a row-level ticker would weaken
        # rather than validate the frozen response schema.
        values = _ohlcv({**row, "ticker": plan.context["symbol"]}, prefix="tiingo", date_key="date", symbol_key="ticker", symbol=plan.context["symbol"])
        if plan.context["adjustment"] == "adjusted":
            try:
                values.update({field: _number(row["adj" + field.capitalize()]) for field in ("open", "high", "low", "close", "volume")})
            except KeyError as exc:
                raise ValueError("tiingo_adjusted_field_missing") from exc
        out.append(values)
    return out


def _cn_daily(plan: RoutePlan, raw: Any) -> list[dict[str, Any]]:
    # QVerisToolGateway deliberately unwraps successful Tool results to
    # ``result.data``.  The direct list-of-lists is therefore the production
    # contract; a retained full envelope is accepted only for offline replay.
    if type(raw) is list:
        groups = raw
    elif isinstance(raw, Mapping) and raw.get("success") is True and isinstance(raw.get("result"), Mapping):
        result = raw["result"]
        if type(result.get("status_code")) is not int or not 200 <= result["status_code"] < 300 or not isinstance(result.get("metadata"), Mapping) or result["metadata"].get("has_results") is not True or result["metadata"].get("interval") != "D":
            raise ValueError("cnfp_daily_envelope_invalid")
        groups = result.get("data")
    else:
        raise ValueError("cnfp_daily_envelope_invalid")
    if type(groups) is not list or len(groups) != 1 or type(groups[0]) is not list or not groups[0] or any(not isinstance(row, Mapping) for row in groups[0]):
        raise ValueError("cnfp_daily_shape_invalid")
    if plan.context["adjustment"] == "adjusted":
        rows = []
        for row in groups[0]:
            if not isinstance(row, Mapping) or row.get("stock_code") != plan.context["symbol"] or Decimal(_number(row["adjustment_factor"])) <= 0:
                raise ValueError("cnfp_adjusted_identity_invalid")
            converted = {"thscode": row["stock_code"], "time": row["date"], "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"], "volume": row["volume"], "amount": row["amount"]}
            item = _ohlcv(converted, prefix="cnfp_adjusted", date_key="time", symbol_key="thscode", symbol=plan.context["symbol"])
            item["adjustment_factor"] = _number(row["adjustment_factor"]); rows.append(item)
        return rows
    return [_ohlcv(row, prefix="cnfp_daily", date_key="time", symbol_key="thscode", symbol=plan.context["symbol"]) for row in groups[0]]


def _hk_daily(plan: RoutePlan, raw: Any) -> list[dict[str, Any]]:
    try:
        rows = raw["data"]["data"]["rows"]
    except (KeyError, TypeError) as exc:
        raise ValueError("hangseng_hk_daily_shape_invalid") from exc
    pagination = raw.get("_qveris_pagination") if isinstance(raw, Mapping) else None
    if raw.get("success") is not True or type(rows) is not list or not rows or any(not isinstance(row, Mapping) for row in rows) or not isinstance(pagination, Mapping) or type(pagination.get("returned_count")) is not int or type(pagination.get("total_count")) is not int or pagination["returned_count"] != len(rows) or pagination["returned_count"] != pagination["total_count"]:
        raise ValueError("hangseng_hk_daily_shape_invalid")
    out = []
    for row in rows:
        if row.get("secucode") != plan.context["symbol"].removesuffix(".HK") or type(row.get("secuabbr")) is not str or not row["secuabbr"] or type(row.get("currency")) is not str or not row["currency"] or len(row["currency"]) > 64 or any(ord(character) < 32 or ord(character) == 127 for character in row["currency"]):
            raise ValueError("hangseng_hk_daily_identity_invalid")
        out.append(_ohlcv({**row, "thscode": plan.context["symbol"], "time": row.get("tradingday")}, prefix="hangseng_hk_daily", date_key="time", symbol_key="thscode", symbol=plan.context["symbol"]))
    return out


def _fmp_daily(plan: RoutePlan, raw: Any) -> list[dict[str, Any]]:
    if type(raw) is not list or not raw:
        raise ValueError("fmp_historical_shape_invalid")
    required = {"symbol", "date", "open", "high", "low", "close", "volume", "change", "changePercent", "vwap"}
    result = []
    for row in raw:
        if not isinstance(row, Mapping) or set(row) != required:
            raise ValueError("fmp_historical_shape_invalid")
        item = _ohlcv(row, prefix="fmp_historical", date_key="date", symbol_key="symbol", symbol=plan.context["symbol"])
        # The observed schema names no currency, adjustment factor, or adjustment flag.
        for field in ("change", "changePercent", "vwap"): _number(row[field])
        result.append(item)
    return result


def _hk_corporate_actions(plan: RoutePlan, raw: Any) -> RouteProjection:
    if not isinstance(raw, Mapping) or set(raw) != {"action", "code", "data", "msg"} or type(raw["action"]) is not str or type(raw["code"]) is not str or type(raw["msg"]) is not str or type(raw["data"]) is not list or not raw["data"]:
        raise ValueError("fiu_hk_actions_shape_invalid")
    required = {"symbol", "type", "eventProgress", "reportDate", "recordDate", "exDate", "paymentDate", "bookClosePeriodStart", "bookClosePeriodEnd", "plan"}
    events = {}
    descriptions = {}
    for row in raw["data"]:
        if not isinstance(row, Mapping) or set(row) != required or row["symbol"] != plan.context["symbol"] or row["type"] != "CD" or type(row["plan"]) is not str or not row["plan"] or len(row["plan"]) > 256 or any(ord(character) < 32 or ord(character) == 127 for character in row["plan"]):
            raise ValueError("fiu_hk_actions_identity_invalid")
        dates = {field: _iso(row[field]) for field in ("reportDate", "recordDate", "exDate", "paymentDate", "bookClosePeriodStart", "bookClosePeriodEnd")}
        if any(value < plan.context["start_date"] or value > plan.context["end_date"] for value in dates.values()) or dates["exDate"] in events:
            raise ValueError("fiu_hk_actions_date_invalid")
        events[dates["exDate"]] = {"amount": (None, "unknown")}
        descriptions[dates["exDate"]] = row["plan"]
    projection = _events_projection(plan, events, max(events), partial=True)
    for event_date, description in descriptions.items():
        projection.data["events"]["d" + event_date.replace("-", "")]["description"] = description
    return RouteProjection(projection.data, projection.as_of, "partial", ("amount", "currency"), "get-response/v2", "known")


def _in_range(rows: list[dict[str, Any]], start: str, end: str, *, date_field: str = "date") -> list[dict[str, Any]]:
    selected = [row for row in rows if start <= row[date_field][:10] <= end]
    if not selected or len({row[date_field] for row in selected}) != len(selected):
        raise ValueError("provider_range_invalid")
    return sorted(selected, key=lambda row: row[date_field])


def _bar_fields(row: Mapping[str, Any], adjustment: str) -> dict[str, Any]:
    keys = ("open", "high", "low", "close", "volume")
    if adjustment == "adjusted" and all("adj" + key.capitalize() in row for key in keys):
        return {key: {"value": row["adj" + key.capitalize()], "unit": "shares" if key == "volume" else "USD_per_share", "nil": False} for key in keys}
    return {key: {"value": row[key], "unit": "shares" if key == "volume" else "unknown", "nil": False} for key in keys}


def _aggregate(rows: list[dict[str, Any]], interval: str) -> tuple[list[dict[str, Any]], bool]:
    if interval == "daily":
        return rows, True
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        current = date.fromisoformat(row["date"])
        if interval == "weekly":
            start = current - timedelta(days=current.weekday()); end = start + timedelta(days=6)
        else:
            start = current.replace(day=1); end = current.replace(day=monthrange(current.year, current.month)[1])
        grouped.setdefault((start.isoformat(), end.isoformat()), []).append(row)
    complete = True
    output = []
    for (start, end), values in sorted(grouped.items()):
        values.sort(key=lambda row: row["date"])
        current = {"date": values[-1]["date"], "period_start": start, "period_end": end, "open": values[0]["open"], "close": values[-1]["close"], "high": format(max(Decimal(row["high"]) for row in values), "f"), "low": format(min(Decimal(row["low"]) for row in values), "f"), "volume": format(sum(Decimal(row["volume"]) for row in values), "f")}
        if len(values) < 2: complete = False
        output.append(current)
    return output, complete


def _price_projection(plan: RoutePlan, rows: list[dict[str, Any]], *, basis_complete: bool = True) -> RouteProjection:
    start, end, interval = plan.context["start_date"], plan.context["end_date"], plan.context["interval"]
    rows = _in_range(rows, start, end)
    rows, complete = _aggregate(rows, interval)
    if interval != "daily" and any(start > row["period_start"] or end < row["period_end"] for row in rows):
        complete = False
    bars = {}
    for row in rows:
        if interval == "daily": key = "d" + row["date"].replace("-", "")
        elif interval == "weekly": key = "w" + row["period_start"].replace("-", "") + "_" + row["period_end"].replace("-", "")
        else: key = "m" + row["period_start"][:7].replace("-", "")
        bars[key] = {"period_key": key, "fields": _bar_fields(row, plan.context["adjustment"])}
    data = {"kind": "historical_price", "accepted_variant_id": plan.accepted_variant_id, "instrument": {"symbol": plan.context["symbol"], "market": plan.context["market"]}, "interval": interval, "adjustment": plan.context["provider_adjustment_basis"], "bars": bars}
    missing = (() if complete else ("interval_coverage",)) + (() if basis_complete else ("adjustment_basis",))
    return RouteProjection(data, max(row["date"] for row in rows), "success" if not missing else "partial", missing)


def _intraday_projection(plan: RoutePlan, parsed: Mapping[str, Any]) -> RouteProjection:
    """Keep every provider timestamp; v1 date keys cannot safely encode intraday bars."""
    rows = parsed.get("bars")
    if type(rows) is not list or not rows:
        raise ValueError("intraday_bars_empty")
    interval = plan.params["interval"]
    if interval.isdigit(): interval += "min"
    if parsed.get("interval") not in {None, interval}:
        raise ValueError("intraday_interval_mismatch")
    bars = {}
    for row in rows:
        if not isinstance(row, Mapping) or type(row.get("timestamp")) is not str or not plan.context["start_date"] <= row["timestamp"][:10] <= plan.context["end_date"]:
            raise ValueError("intraday_timestamp_invalid")
        timestamp = row["timestamp"].replace(" ", "T", 1)
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("intraday_timestamp_invalid") from exc
        if timestamp in bars:
            raise ValueError("intraday_timestamp_duplicate")
        values = {field: _number(row[field]) for field in ("open", "high", "low", "close", "volume")}
        if Decimal(values["volume"]) < 0 or Decimal(values["high"]) < max(Decimal(values["open"]), Decimal(values["close"])) or Decimal(values["low"]) > min(Decimal(values["open"]), Decimal(values["close"])):
            raise ValueError("intraday_ohlcv_invalid")
        bars[timestamp] = {"period": {"timestamp": timestamp, "interval": interval}, "fields": {field: {"value": value, "unit": "shares" if field == "volume" else "unknown", "nil": False} for field, value in values.items()}}
    timezone = parsed.get("timezone", "unknown")
    if type(timezone) is not str or not timezone:
        raise ValueError("intraday_timezone_invalid")
    data = {"kind": "historical_price", "accepted_variant_id": plan.accepted_variant_id, "instrument": {"symbol": plan.context["symbol"], "market": plan.context["market"]}, "interval": interval, "adjustment": plan.context["provider_adjustment_basis"], "time_basis": "provider_timestamp", "timezone": timezone, "bars": bars}
    as_of = parsed.get("last_refreshed", max(bars))
    if type(as_of) is not str: raise ValueError("intraday_as_of_invalid")
    return RouteProjection(data, as_of.replace(" ", "T", 1), schema_version="get-response/v2")


def _events_projection(plan: RoutePlan, events: Mapping[str, Mapping[str, tuple[str | None, str]]], as_of: str, *, partial: bool = False) -> RouteProjection:
    if not events:
        raise ValueError("provider_events_empty")
    data = {"kind": "historical_event", "instrument": {"symbol": plan.context["symbol"], "market": plan.context["market"]}, "event_type": plan.context["operation"], "events": {"d" + item.replace("-", ""): {"period_key": "d" + item.replace("-", ""), "fields": {name: {"value": value, "unit": unit, "nil": value is None} for name, (value, unit) in fields.items()}} for item, fields in events.items()}}
    return RouteProjection(data, as_of, "partial" if partial else "success", ("unparsed_event_rate",) if partial else ())


def _tiingo_events(plan: RoutePlan, raw: Any) -> RouteProjection:
    rows = _in_range(_tiingo(plan, raw), plan.context["start_date"], plan.context["end_date"])
    events = {}
    for row in rows:
        source = next(item for item in _rows(raw["result"]["data"]) if _iso(item["date"]) == row["date"])
        if plan.context["operation"] == "corporate_actions":
            events[row["date"]] = {"dividend": (_number(source["divCash"]) if source.get("divCash") is not None else None, "unknown"), "split_factor": (_number(source["splitFactor"]) if source.get("splitFactor") is not None else None, "ratio")}
        else:
            events[row["date"]] = {"adjustment_factor": (_number(source["splitFactor"]) if source.get("splitFactor") is not None else None, "ratio")}
    return _events_projection(plan, events, max(row["date"] for row in rows), partial=any(any(value is None for value, _ in fields.values()) for fields in events.values()))


def project(plan: RoutePlan, raw: Any) -> RouteProjection:
    """Parse exactly the selected provider schema and emit a public projection."""
    if not isinstance(plan, RoutePlan) or plan.suite != "historical_price":
        raise ValueError("historical_route_plan_required")
    parser_id, operation = plan.parser_id, plan.context["operation"]
    if parser_id == "tiingo_daily_bars_v1": return _price_projection(plan, _tiingo(plan, raw))
    if parser_id == "cnfp_daily_bars_v1": return _price_projection(plan, _cn_daily(plan, raw))
    if parser_id == "hangseng_hk_daily_bars_v1": return _price_projection(plan, _hk_daily(plan, raw))
    if parser_id == "fmp_historical_eod_v1": return _price_projection(plan, _fmp_daily(plan, raw), basis_complete=False)
    if parser_id == "fiu_hk_corporate_actions_v1": return _hk_corporate_actions(plan, raw)
    if parser_id == ALPHAVANTAGE_INTRADAY_BARS_V1:
        parsed = parse_provider_payload(parser_id, raw, expected_symbol=plan.context["symbol"])
        return _intraday_projection(plan, parsed)
    if parser_id == CNFP_INTRADAY_BARS_V1:
        parsed = parse_provider_payload(parser_id, raw, expected_symbol=plan.context["symbol"])
        return _intraday_projection(plan, parsed)
    if parser_id == CNFP_ADJUSTMENT_FACTOR_V1:
        parsed = parse_provider_payload(parser_id, raw, expected_symbol=plan.context["symbol"])
        items = _in_range([{"date": item["trade_date"], "factor": item["adjustment_factor"]} for item in parsed["factors"]], plan.context["start_date"], plan.context["end_date"])
        return _events_projection(plan, {item["date"]: {"adjustment_factor": (item["factor"], "unknown")} for item in items}, max(item["date"] for item in items))
    if parser_id == FIU_SSE_DIVIDENDS_V1:
        parsed = parse_provider_payload(parser_id, raw, expected_symbol={"symbol": plan.context["symbol"], "start_date": plan.context["start_date"], "end_date": plan.context["end_date"]})
        events = {item["ex_date"]: {"dividend_rate": (item.get("rate"), item["rate_unit"])} for item in parsed["events"]}
        return _events_projection(plan, events, max(events), partial=parsed["coverage"] != "complete_for_observed_rows")
    if parser_id == "tiingo_daily_events_v1": return _tiingo_events(plan, raw)
    if parser_id == CNFP_HKEX_TRADING_CALENDAR_V1:
        parsed = parse_provider_payload(parser_id, raw, expected_symbol={"marketcode": "212200", "date_type": "0"})
        dates = [item for item in parsed["trading_dates"] if plan.context["start_date"] <= item <= plan.context["end_date"]]
        if not dates: raise ValueError("calendar_range_empty")
        return RouteProjection({"kind": "market_calendar", "venue": "HKEX", "dates": dates, "range": {"start_date": plan.context["start_date"], "end_date": plan.context["end_date"]}, "time_basis": "coverage_range"}, None, "success", (), "get-response/v2", "unavailable")
    raise ValueError("historical_parser_unsupported")


__all__ = ["SUPPORTED_KEYS", "project", "resolve"]

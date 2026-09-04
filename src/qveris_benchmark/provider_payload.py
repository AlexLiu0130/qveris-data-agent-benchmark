"""Strict parsers for explicitly approved non-JSON provider payloads.

These parsers are intentionally narrow.  They do not infer schemas, convert
units, or attempt a second format when a payload fails its approved contract.
"""

from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Callable


class ProviderPayloadParseError(ValueError):
    """A fail-closed payload error with a stable machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


EODHD_QUOTE_CSV_V1 = "eodhd_quote_csv_v1"
CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1 = "caidazi_a_share_quote_markdown_v1"
FIU_HK_CASH_FLOW_ANNUAL_V2 = "fiu_hk_cash_flow_annual_v2"
FIU_HK_INCOME_ANNUAL_V2 = "fiu_hk_income_annual_v2"
FIU_HK_INCOME_ANNUAL_V3 = "fiu_hk_income_annual_v3"
FIU_US_QUOTE_SNAPSHOT_V1 = "fiu_us_quote_snapshot_v1"
ALPHAVANTAGE_GLOBAL_QUOTE_V1 = "alphavantage_global_quote_v1"
ALPHAVANTAGE_TIME_SERIES_DAILY_V1 = "alphavantage_time_series_daily_v1"
FMP_EOD_V1 = "fmp_eod_v1"
HANGSENG_A_SHARE_QUOTE_V1 = "hangseng_a_share_quote_v1"
ALPHAVANTAGE_BULK_BID_ASK_V1 = "alphavantage_bulk_bid_ask_v1"
FIU_US_MULTI_QUOTE_V1 = "fiu_us_multi_quote_v1"
QVERIS_AFTER_HOURS_V1 = "qveris_after_hours_v1"
ALPHAVANTAGE_INTRADAY_BARS_V1 = "alphavantage_intraday_bars_v1"
ALPHAVANTAGE_INCOME_STATEMENT_LIST_V1 = "alphavantage_income_statement_list_v1_467a92c0"
ALPHAVANTAGE_BALANCE_SHEET_RETRIEVE_V1 = "alphavantage_balance_sheet_retrieve_v1_467a92c0"
ALPHAVANTAGE_CASH_FLOW_RETRIEVE_V1 = "alphavantage_cash_flow_retrieve_v1_7aca3c4a"
FMP_STANDARD_INCOME_STATEMENT_V1 = "fmp_standard_income_statement_v1"
FMP_STANDARD_BALANCE_SHEET_V1 = "fmp_standard_balance_sheet_v1"
FMP_STANDARD_CASH_FLOW_V1 = "fmp_standard_cash_flow_v1"
FMP_AS_REPORTED_INCOME_V1 = "fmp_as_reported_income_v1"
FIU_SSE_INCOME_STATEMENT_V1 = "fiu_sse_income_statement_v1"
FIU_SSE_BALANCE_SHEET_V1 = "fiu_sse_balance_sheet_v1"
FIU_SSE_CASH_FLOW_V1 = "fiu_sse_cash_flow_v1"
CNFP_REALTIME_QUOTE_V1 = "cnfp_realtime_quote_v1"
CNFP_INTRADAY_BARS_V1 = "cnfp_intraday_bars_v1"
CNFP_ADJUSTMENT_FACTOR_V1 = "cnfp_adjustment_factor_v1"
CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1 = "caidazi_a_share_quote_envelope_v1"
CNFP_FINANCIAL_ROW_V1 = "cnfp_financial_row_v1"
HANGSENG_HK_BATCH_QUOTE_V1 = "hangseng_hk_batch_quote_v1"
HANGSENG_HK_L1_V1 = "hangseng_hk_l1_v1"
FIU_SSE_DIVIDEND_SCHEMA_V1 = "fiu_sse_dividend_schema_v1"
FIU_SSE_DIVIDENDS_V1 = "fiu_sse_dividends_v1"
HANGSENG_HK_FORWARD_RANGE_SUMMARY_V1 = "hangseng_hk_forward_range_summary_v1"
CNFP_HKEX_TRADING_CALENDAR_V1 = "cnfp_hkex_trading_calendar_v1"

_EODHD_HEADERS = (
    "code",
    "timestamp",
    "gmtoffset",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "previousClose",
    "change",
    "change_p",
)

_CAIDAZI_HEADERS = (
    "股票代码",
    "股票名称",
    "交易时间",
    "最新价",
    "涨跌额",
    "涨跌幅",
    "成交量",
    "成交额",
    "开盘以来成交笔数",
    "委托卖盘价",
    "委托卖盘量",
    "委托买盘价",
    "委托买盘量",
    "开盘价",
    "最高价",
    "最低价",
    "总市值",
    "流通市值",
    "换手率",
    "量比",
    "总股本",
    "市盈率(TTM)",
    "市净率",
    "涨停价",
    "跌停价",
    "是否停牌（0：否，1:是）",
)

_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_DECIMAL_WITH_UNIT = re.compile(r"(-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)([^0-9.\-]*)\Z")
_EODHD_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,31}\.[A-Z]{2,8}\Z")
_A_SHARE_SYMBOL = re.compile(r"[0-9]{6}\.(?:SH|SZ)\Z")
_HK_SYMBOL = re.compile(r"[0-9]{5}\.HK\Z")
_HK_FISCAL_YEAR = re.compile(r"(?:(?:FY)?([0-9]{4})|([0-9]{4})/FY)\Z")
_CAIDAZI_NAME = re.compile(r"[A-Za-z0-9\u3400-\u9fff .-]{1,80}\Z")
_US_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\Z")
_FIU_US_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\.US\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_MARKDOWN_SEPARATOR = re.compile(r":?-{3,}:?\Z")
_TRADE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\Z")
_BIDI_CONTROLS = frozenset({"LRE", "RLE", "PDF", "LRO", "RLO", "LRI", "RLI", "FSI", "PDI"})
_MAX_PAYLOAD_BYTES = 16 * 1024
_MAX_STRUCTURED_PAYLOAD_BYTES = 2 * 1024 * 1024

_CAIDAZI_NUMERIC_FIELDS = frozenset(_CAIDAZI_HEADERS[3:-1])
_CAIDAZI_SIGNED_FIELDS = frozenset({"涨跌额", "涨跌幅"})
_CAIDAZI_MISSING_SENTINEL_FIELDS = frozenset({
    "开盘以来成交笔数",
    "总市值",
    "流通市值",
    "换手率",
    "量比",
    "总股本",
    "市盈率(TTM)",
    "市净率",
})
_CAIDAZI_ALLOWED_UNITS = {
    "最新价": frozenset({""}),
    "涨跌额": frozenset({""}),
    "涨跌幅": frozenset({"%"}),
    "成交量": frozenset({"万股"}),
    "成交额": frozenset({"亿元"}),
    "开盘以来成交笔数": frozenset({"", "笔"}),
    "委托卖盘价": frozenset({""}),
    "委托卖盘量": frozenset({"股"}),
    "委托买盘价": frozenset({""}),
    "委托买盘量": frozenset({"股"}),
    "开盘价": frozenset({""}),
    "最高价": frozenset({""}),
    "最低价": frozenset({""}),
    "总市值": frozenset({"亿元"}),
    "流通市值": frozenset({"亿元"}),
    "换手率": frozenset({"%"}),
    "量比": frozenset({""}),
    "总股本": frozenset({"亿股"}),
    "市盈率(TTM)": frozenset({""}),
    "市净率": frozenset({""}),
    "涨停价": frozenset({""}),
    "跌停价": frozenset({""}),
}


def _fail(code: str) -> None:
    raise ProviderPayloadParseError(code)


def _require_text(raw: Any, prefix: str) -> str:
    if type(raw) is not str:
        _fail(f"{prefix}_PAYLOAD_TYPE_INVALID")
    if len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        _fail(f"{prefix}_PAYLOAD_TOO_LARGE")
    # LF is the sole structural control accepted by these line-oriented formats.
    if any(
        (unicodedata.category(char) == "Cc" and char != "\n")
        or unicodedata.category(char) == "Cf"
        or unicodedata.bidirectional(char) in _BIDI_CONTROLS
        for char in raw
    ):
        _fail(f"{prefix}_CONTROL_CHARACTER")
    return raw


def _structured(raw: Any, prefix: str) -> Any:
    """Decode the one observed provider JSON-string envelope, never recursively."""
    if type(raw) is not str:
        return raw
    if len(raw.encode("utf-8")) > _MAX_STRUCTURED_PAYLOAD_BYTES:
        _fail(f"{prefix}_PAYLOAD_TOO_LARGE")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        _fail(f"{prefix}_JSON_INVALID")
    if type(value) not in (dict, list):
        _fail(f"{prefix}_JSON_INVALID")
    return value


def _finite_decimal(raw: str, code: str) -> Decimal:
    if type(raw) is not str:
        _fail(code)
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        _fail(code)
    if not value.is_finite():
        _fail(code)
    return value


def _finite_number(raw: Any, code: str) -> Decimal:
    if type(raw) not in (int, float) or isinstance(raw, bool) or not math.isfinite(raw):
        _fail(code)
    return Decimal(str(raw))


def _decimal_string(raw: Any, code: str) -> str:
    """Return a finite JSON-safe decimal without assigning a unit."""
    if type(raw) is str:
        value = _finite_decimal(raw, code)
    else:
        value = _finite_number(raw, code)
    return format(value, "f")


def _expected_symbols(value: Any, pattern: re.Pattern[str], code: str) -> tuple[str, ...]:
    values = (value,) if type(value) is str else tuple(value) if type(value) in (list, tuple) else ()
    if not 1 <= len(values) <= 50 or len(set(values)) != len(values):
        _fail(code)
    if any(type(symbol) is not str or pattern.fullmatch(symbol) is None for symbol in values):
        _fail(code)
    return values


def _period(raw: Any, code: str) -> str:
    if type(raw) is not str:
        _fail(code)
    if re.fullmatch(r"[0-9]{8}", raw):
        try:
            datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            _fail(code)
        return raw
    try:
        return _iso_date(raw, code)
    except ProviderPayloadParseError:
        return _timestamp(raw, code)


def _json_safe(value: Any, code: str) -> Any:
    """Keep provider substructures only when they are finite JSON scalars."""
    if value is None or type(value) is bool or type(value) is str:
        return value
    if type(value) in (int, float):
        return _decimal_string(value, code)
    if type(value) is list:
        return [_json_safe(item, code) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail(code)
        return {key: _json_safe(item, code) for key, item in value.items()}
    _fail(code)


def _rows(raw: Any, prefix: str) -> list[dict[str, Any]]:
    """Accept the two observed direct-list and one-level grouped-list forms."""
    if type(raw) is not list or not raw:
        _fail(f"{prefix}_SHAPE_INVALID")
    if all(type(item) is dict for item in raw):
        values = raw
    elif all(type(group) is list for group in raw):
        values = [row for group in raw for row in group]
    else:
        _fail(f"{prefix}_SHAPE_INVALID")
    if not values or any(type(row) is not dict for row in values):
        _fail(f"{prefix}_SHAPE_INVALID")
    return values


def _iso_date(raw: Any, code: str) -> str:
    if type(raw) is not str:
        _fail(code)
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        _fail(code)
    return raw


def _timestamp(raw: Any, code: str) -> str:
    if type(raw) is not str or ("T" not in raw and " " not in raw):
        _fail(code)
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    return raw


def _integer(raw: str, code: str) -> int:
    if not _INTEGER.fullmatch(raw):
        _fail(code)
    return int(raw)


def _parse_eodhd_quote_csv_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    text = _require_text(raw, "EODHD")
    if _EODHD_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("EODHD_SYMBOL_FORMAT_INVALID")
    try:
        rows = list(csv.reader(StringIO(text, newline=""), strict=True))
    except csv.Error:
        _fail("EODHD_CSV_INVALID")
    if len(rows) != 2:
        _fail("EODHD_CSV_RECORD_COUNT")
    header, row = rows
    if tuple(header) != _EODHD_HEADERS:
        _fail("EODHD_CSV_HEADER_INVALID")
    if len(row) != len(_EODHD_HEADERS):
        _fail("EODHD_CSV_ROW_INVALID")
    values = dict(zip(_EODHD_HEADERS, row))
    if _EODHD_SYMBOL.fullmatch(values["code"]) is None:
        _fail("EODHD_SYMBOL_FORMAT_INVALID")
    if values["code"] != expected_symbol:
        _fail("EODHD_SYMBOL_MISMATCH")

    timestamp = _integer(values["timestamp"], "EODHD_TIMESTAMP_INVALID")
    gmtoffset = _integer(values["gmtoffset"], "EODHD_GMTOFFSET_INVALID")
    decimals = {
        field: _finite_decimal(values[field], "EODHD_DECIMAL_INVALID")
        for field in ("open", "high", "low", "close", "volume", "previousClose", "change", "change_p")
    }
    if decimals["volume"] < 0:
        _fail("EODHD_VOLUME_NEGATIVE")
    if not decimals["low"] <= min(decimals["open"], decimals["close"]) <= max(decimals["open"], decimals["close"]) <= decimals["high"]:
        _fail("EODHD_OHLC_INVALID")
    return {
        "parser_id": EODHD_QUOTE_CSV_V1,
        "symbol": values["code"],
        "timestamp": timestamp,
        "gmtoffset": gmtoffset,
        "open": decimals["open"],
        "high": decimals["high"],
        "low": decimals["low"],
        "close": decimals["close"],
        "volume": decimals["volume"],
        "previous_close": decimals["previousClose"],
        "change": decimals["change"],
        "change_percent": decimals["change_p"],
    }


def _markdown_cells(line: str, code: str) -> list[str]:
    if not (line.startswith("|") and line.endswith("|")):
        _fail(code)
    cells = [cell.strip() for cell in line[1:-1].split("|")]
    if any(not cell for cell in cells):
        _fail(code)
    return cells


def _parse_caidazi_number(field: str, raw: str) -> dict[str, str] | None:
    if field in _CAIDAZI_MISSING_SENTINEL_FIELDS and raw == "-":
        return None
    match = _DECIMAL_WITH_UNIT.fullmatch(raw)
    if not match:
        _fail("CAIDAZI_DECIMAL_INVALID")
    decimal_lexical, unit = match.groups()
    if unit not in _CAIDAZI_ALLOWED_UNITS[field]:
        _fail("CAIDAZI_UNIT_INVALID")
    value = _finite_decimal(decimal_lexical, "CAIDAZI_DECIMAL_INVALID")
    if field not in _CAIDAZI_SIGNED_FIELDS and value < 0:
        _fail("CAIDAZI_NEGATIVE_VALUE")
    return {"decimal": decimal_lexical, "unit": unit}


def _quote_decimal(value: dict[str, str]) -> Decimal:
    return Decimal(value["decimal"])


def _parse_caidazi_a_share_quote_markdown_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    text = _require_text(raw, "CAIDAZI")
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n")
    if len(lines) != 5 or lines[1] != "":
        _fail("CAIDAZI_LINE_COUNT_INVALID")
    if _A_SHARE_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("CAIDAZI_SYMBOL_FORMAT_INVALID")
    if lines[0] != f"## {expected_symbol} - 实时行情数据":
        _fail("CAIDAZI_TITLE_INVALID")
    headers = _markdown_cells(lines[2], "CAIDAZI_HEADER_INVALID")
    if tuple(headers) != _CAIDAZI_HEADERS:
        _fail("CAIDAZI_HEADER_INVALID")
    separator = _markdown_cells(lines[3], "CAIDAZI_SEPARATOR_INVALID")
    if len(separator) != len(_CAIDAZI_HEADERS) or any(not _MARKDOWN_SEPARATOR.fullmatch(cell) for cell in separator):
        _fail("CAIDAZI_SEPARATOR_INVALID")
    cells = _markdown_cells(lines[4], "CAIDAZI_ROW_INVALID")
    if len(cells) != len(_CAIDAZI_HEADERS):
        _fail("CAIDAZI_ROW_INVALID")
    values = dict(zip(_CAIDAZI_HEADERS, cells))
    if _A_SHARE_SYMBOL.fullmatch(values["股票代码"]) is None:
        _fail("CAIDAZI_SYMBOL_FORMAT_INVALID")
    if values["股票代码"] != expected_symbol:
        _fail("CAIDAZI_SYMBOL_MISMATCH")
    if _CAIDAZI_NAME.fullmatch(values["股票名称"]) is None:
        _fail("CAIDAZI_NAME_INVALID")
    if not _TRADE_TIME.fullmatch(values["交易时间"]):
        _fail("CAIDAZI_TRADE_TIME_INVALID")
    try:
        datetime.strptime(values["交易时间"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        _fail("CAIDAZI_TRADE_TIME_INVALID")
    if values["是否停牌（0：否，1:是）"] not in {"0", "1"}:
        _fail("CAIDAZI_HALT_INVALID")

    parsed = {
        field: _parse_caidazi_number(field, values[field])
        for field in _CAIDAZI_NUMERIC_FIELDS
    }
    bid = parsed["委托买盘价"]
    ask = parsed["委托卖盘价"]
    if bid["unit"] != ask["unit"]:
        _fail("CAIDAZI_UNIT_MISMATCH")
    if _quote_decimal(bid) > _quote_decimal(ask):
        _fail("CAIDAZI_BID_ASK_INVALID")
    open_price = parsed["开盘价"]
    high = parsed["最高价"]
    low = parsed["最低价"]
    close = parsed["最新价"]
    if len({open_price["unit"], high["unit"], low["unit"], close["unit"]}) != 1:
        _fail("CAIDAZI_UNIT_MISMATCH")
    if not _quote_decimal(low) <= min(_quote_decimal(open_price), _quote_decimal(close)) <= max(_quote_decimal(open_price), _quote_decimal(close)) <= _quote_decimal(high):
        _fail("CAIDAZI_OHLC_INVALID")
    return {
        "parser_id": CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1,
        "symbol": values["股票代码"],
        "name": values["股票名称"],
        "trade_time": values["交易时间"],
        "halted": values["是否停牌（0：否，1:是）"] == "1",
        "fields": parsed,
    }


def _expected_hk_fiscal_year(expected_fiscal_year: Any, *, required: bool) -> str | None:
    if expected_fiscal_year is None:
        if required:
            _fail("FIU_HK_EXPECTED_FISCAL_YEAR_REQUIRED")
        return None
    if type(expected_fiscal_year) is int and not isinstance(expected_fiscal_year, bool):
        year = str(expected_fiscal_year)
    elif type(expected_fiscal_year) is str:
        year = expected_fiscal_year
    else:
        _fail("FIU_HK_EXPECTED_FISCAL_YEAR_INVALID")
    if _INTEGER.fullmatch(year) is None or len(year) != 4:
        _fail("FIU_HK_EXPECTED_FISCAL_YEAR_INVALID")
    return year


def _fiu_hk_annual_row(
    raw: Any,
    expected_symbol: str,
    *,
    expected_fiscal_year: Any = None,
    require_expected_fiscal_year: bool = False,
) -> tuple[dict[str, Any], str]:
    if _HK_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("FIU_HK_SYMBOL_FORMAT_INVALID")
    if type(raw) is not list or len(raw) != 1 or type(raw[0]) is not dict:
        _fail("FIU_HK_RECORD_COUNT")
    row = raw[0]
    metadata = ("symbol", "currency", "fiscalYear", "reportType", "coverMonths", "reportDate")
    if any(field not in row for field in metadata):
        _fail("FIU_HK_METADATA_MISSING")
    if row["symbol"] != expected_symbol:
        _fail("FIU_HK_SYMBOL_MISMATCH")
    if row["currency"] != "RMB":
        _fail("FIU_HK_CURRENCY_INVALID")
    if row["reportType"] != "F":
        _fail("FIU_HK_REPORT_TYPE_INVALID")
    if type(row["coverMonths"]) is not int or row["coverMonths"] != 12:
        _fail("FIU_HK_COVER_MONTHS_INVALID")
    fiscal_year = _HK_FISCAL_YEAR.fullmatch(row["fiscalYear"]) if type(row["fiscalYear"]) is str else None
    if fiscal_year is None:
        _fail("FIU_HK_FISCAL_YEAR_INVALID")
    year = fiscal_year.group(1) or fiscal_year.group(2)
    expected_year = _expected_hk_fiscal_year(expected_fiscal_year, required=require_expected_fiscal_year)
    if expected_year is not None and year != expected_year:
        _fail("FIU_HK_FISCAL_YEAR_MISMATCH")
    if row["reportDate"] != f"{year}-12-31":
        _fail("FIU_HK_REPORT_DATE_INVALID")
    return row, year


def _fiu_hk_millions(row: dict[str, Any], source_field: str) -> Decimal:
    value = row.get(source_field)
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        _fail("FIU_HK_AMOUNT_INVALID")
    return Decimal(str(value)) / Decimal("1000000")


def _fiu_hk_v3_scaled(row: dict[str, Any], source_field: str) -> Decimal:
    """Read an audited finite JSON number or decimal string and scale it by 1e6."""
    value = row.get(source_field)
    if type(value) is int and not isinstance(value, bool):
        amount = Decimal(value)
    elif type(value) is float and math.isfinite(value):
        amount = Decimal(str(value))
    elif type(value) is str:
        amount = _finite_decimal(value, "FIU_HK_V3_AMOUNT_INVALID")
    else:
        _fail("FIU_HK_V3_AMOUNT_INVALID")
    return amount / Decimal("1000000")


def _fiu_hk_metadata(expected_symbol: str, year: str) -> dict[str, Any]:
    return {
        "symbol": expected_symbol,
        "currency": "RMB",
        "fiscal_year": year,
        "report_date": f"{year}-12-31",
    }


def _parse_fiu_hk_cash_flow_annual_v2(raw: Any, expected_symbol: str, expected_fiscal_year: Any = None) -> dict[str, Any]:
    row, year = _fiu_hk_annual_row(raw, expected_symbol, expected_fiscal_year=expected_fiscal_year)
    return {
        "parser_id": FIU_HK_CASH_FLOW_ANNUAL_V2,
        **_fiu_hk_metadata(expected_symbol, year),
        "normalization_basis": "oracle_crosscheck",
        "net_cash_from_operating": _fiu_hk_millions(row, "netCashFromOperating"),
        "net_cash_from_investing": _fiu_hk_millions(row, "netCashFromInvesting"),
        "net_cash_from_financing": _fiu_hk_millions(row, "netCashFromFinancing"),
        "net_increase_in_cash": _fiu_hk_millions(row, "netIncreaseInCash"),
    }


def _parse_fiu_hk_income_annual_v2(raw: Any, expected_symbol: str, expected_fiscal_year: Any = None) -> dict[str, Any]:
    row, year = _fiu_hk_annual_row(raw, expected_symbol, expected_fiscal_year=expected_fiscal_year)
    return {
        "parser_id": FIU_HK_INCOME_ANNUAL_V2,
        "statement_type": "income_statement",
        **_fiu_hk_metadata(expected_symbol, year),
        "revenue": _fiu_hk_millions(row, "operatingIncome"),
        "cost_of_revenue": _fiu_hk_millions(row, "operatingExpenses"),
        "gross_profit": _fiu_hk_millions(row, "grossProfit"),
        "profit_before_taxation": _fiu_hk_millions(row, "profitBeforeTaxation"),
        "taxation": _fiu_hk_millions(row, "taxation"),
        "profit_the_period": _fiu_hk_millions(row, "profitThePeriod"),
        "net_income_attributable": _fiu_hk_millions(row, "ownersOfTheCom"),
    }


def _parse_fiu_hk_income_annual_v3(raw: Any, expected_symbol: str, expected_fiscal_year: Any = None) -> dict[str, Any]:
    """Map the FY2024-audited HK income fields without schema inference."""
    if expected_symbol != "00700.HK":
        _fail("FIU_HK_V3_EVIDENCE_SCOPE_SYMBOL_UNSUPPORTED")
    if expected_fiscal_year != 2024:
        _fail("FIU_HK_V3_EVIDENCE_SCOPE_FISCAL_YEAR_UNSUPPORTED")
    row, year = _fiu_hk_annual_row(
        raw,
        expected_symbol,
        expected_fiscal_year=expected_fiscal_year,
        require_expected_fiscal_year=True,
    )
    if row["symbol"] != "00700.HK":
        _fail("FIU_HK_V3_EVIDENCE_SCOPE_SYMBOL_UNSUPPORTED")
    if row["fiscalYear"] != "2024/FY":
        _fail("FIU_HK_FISCAL_YEAR_FORMAT_UNSUPPORTED")
    amount = lambda field: _fiu_hk_v3_scaled(row, field)
    expense = lambda field: abs(amount(field))
    return {
        "parser_id": FIU_HK_INCOME_ANNUAL_V3,
        "statement_type": "income_statement",
        **_fiu_hk_metadata(expected_symbol, year),
        "normalization_basis": "official_annual_report_crosscheck",
        "evidence_scope": "00700.HK/FY2024",
        "sign_convention": "expense_positive",
        "amount_unit": "RMB_millions",
        "revenue": amount("operatingIncome"),
        "cost_of_revenue": expense("operatingExpenses"),
        "gross_profit": amount("grossProfit"),
        "profit_before_taxation": amount("profitBeforeTaxation"),
        "taxation": expense("taxation"),
        "profit_the_period": amount("profitThePeriod"),
        "net_income_attributable": amount("ownersOfTheCom"),
        "non_controlling_interests": amount("nonControllingInterests"),
        "other_comprehensive_income": amount("otherComprehensiveIncome"),
        "total_comprehensive_income": amount("totalComprehensiveIncome"),
        "selling_expense": expense("saleExpense"),
        "share_of_profit_from_joint_ventures_and_associates": amount("joinContrEntitiesAssociates"),
        "weighted_average_shares": amount("weightedAveShareNumber"),
        "weighted_average_shares_unit": "million_shares",
    }


def _quote_ohlc(values: dict[str, Decimal], prefix: str) -> None:
    if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
        _fail(f"{prefix}_OHLC_INVALID")


def _parse_fiu_us_quote_snapshot_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not dict or type(raw.get("body")) is not list or len(raw["body"]) != 1 or type(raw["body"][0]) is not dict or type(raw["body"][0].get("snapshot")) is not dict:
        _fail("FIU_US_SNAPSHOT_SHAPE_INVALID")
    snapshot = raw["body"][0]["snapshot"]
    try:
        symbol = snapshot["symbol"]
        trade_time = _timestamp(snapshot["time"], "FIU_US_TIME_INVALID")
        values = {name: _finite_number(snapshot[name], "FIU_US_NUMERIC_INVALID") for name in ("open", "high", "low", "last")}
    except KeyError:
        _fail("FIU_US_SNAPSHOT_FIELD_MISSING")
    if type(symbol) is not str or _FIU_US_SYMBOL.fullmatch(symbol) is None:
        _fail("FIU_US_SYMBOL_FORMAT_INVALID")
    if symbol != expected_symbol:
        _fail("FIU_US_SYMBOL_MISMATCH")
    _quote_ohlc({"open": values["open"], "high": values["high"], "low": values["low"], "close": values["last"]}, "FIU_US")
    return {"parser_id": FIU_US_QUOTE_SNAPSHOT_V1, "symbol": symbol, "trade_time": trade_time, "open": values["open"], "high": values["high"], "low": values["low"], "close": values["last"]}


def _parse_alphavantage_global_quote_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not dict or type(raw.get("Global Quote")) is not dict:
        _fail("ALPHAVANTAGE_GLOBAL_QUOTE_SHAPE_INVALID")
    quote = raw["Global Quote"]
    try:
        symbol = quote["01. symbol"]
        trade_date = _iso_date(quote["07. latest trading day"], "ALPHAVANTAGE_GLOBAL_QUOTE_DATE_INVALID")
        values = {
            "open": _finite_decimal(quote["02. open"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
            "high": _finite_decimal(quote["03. high"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
            "low": _finite_decimal(quote["04. low"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
            "close": _finite_decimal(quote["05. price"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
            "volume": _finite_decimal(quote["06. volume"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
            "previous_close": _finite_decimal(quote["08. previous close"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
            "change": _finite_decimal(quote["09. change"], "ALPHAVANTAGE_GLOBAL_QUOTE_DECIMAL_INVALID"),
        }
        change_percent = quote["10. change percent"]
    except KeyError:
        _fail("ALPHAVANTAGE_GLOBAL_QUOTE_FIELD_MISSING")
    if type(symbol) is not str or _US_SYMBOL.fullmatch(symbol) is None:
        _fail("ALPHAVANTAGE_SYMBOL_FORMAT_INVALID")
    if symbol != expected_symbol:
        _fail("ALPHAVANTAGE_SYMBOL_MISMATCH")
    if type(change_percent) is not str or not change_percent.endswith("%"):
        _fail("ALPHAVANTAGE_GLOBAL_QUOTE_PERCENT_INVALID")
    values["change_percent"] = _finite_decimal(change_percent[:-1], "ALPHAVANTAGE_GLOBAL_QUOTE_PERCENT_INVALID")
    if values["volume"] < 0:
        _fail("ALPHAVANTAGE_GLOBAL_QUOTE_VOLUME_NEGATIVE")
    _quote_ohlc(values, "ALPHAVANTAGE_GLOBAL_QUOTE")
    return {"parser_id": ALPHAVANTAGE_GLOBAL_QUOTE_V1, "symbol": symbol, "trade_date": trade_date, **{name: format(value, "f") for name, value in values.items()}}


def _parse_alphavantage_time_series_daily_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not dict or type(raw.get("Meta Data")) is not dict or type(raw.get("Time Series (Daily)")) is not dict:
        _fail("ALPHAVANTAGE_DAILY_SHAPE_INVALID")
    metadata, series = raw["Meta Data"], raw["Time Series (Daily)"]
    if not 1 <= len(series) <= 100:
        _fail("ALPHAVANTAGE_DAILY_RECORD_COUNT")
    try:
        symbol = metadata["2. Symbol"]
        refreshed = _iso_date(metadata["3. Last Refreshed"], "ALPHAVANTAGE_DAILY_REFRESHED_INVALID")
        timezone = metadata["5. Time Zone"]
    except KeyError:
        _fail("ALPHAVANTAGE_DAILY_METADATA_MISSING")
    if type(symbol) is not str or _US_SYMBOL.fullmatch(symbol) is None:
        _fail("ALPHAVANTAGE_SYMBOL_FORMAT_INVALID")
    if symbol != expected_symbol:
        _fail("ALPHAVANTAGE_SYMBOL_MISMATCH")
    if type(timezone) is not str or not timezone:
        _fail("ALPHAVANTAGE_DAILY_TIMEZONE_INVALID")
    bars = []
    for trade_date in sorted(series, reverse=True):
        _iso_date(trade_date, "ALPHAVANTAGE_DAILY_DATE_INVALID")
        row = series[trade_date]
        if type(row) is not dict:
            _fail("ALPHAVANTAGE_DAILY_ROW_INVALID")
        try:
            values = {"open": _finite_decimal(row["1. open"], "ALPHAVANTAGE_DAILY_DECIMAL_INVALID"), "high": _finite_decimal(row["2. high"], "ALPHAVANTAGE_DAILY_DECIMAL_INVALID"), "low": _finite_decimal(row["3. low"], "ALPHAVANTAGE_DAILY_DECIMAL_INVALID"), "close": _finite_decimal(row["4. close"], "ALPHAVANTAGE_DAILY_DECIMAL_INVALID"), "volume": _finite_decimal(row["5. volume"], "ALPHAVANTAGE_DAILY_DECIMAL_INVALID")}
        except KeyError:
            _fail("ALPHAVANTAGE_DAILY_ROW_INVALID")
        if values["volume"] < 0:
            _fail("ALPHAVANTAGE_DAILY_VOLUME_NEGATIVE")
        _quote_ohlc(values, "ALPHAVANTAGE_DAILY")
        bars.append({"trade_date": trade_date, **values})
    return {"parser_id": ALPHAVANTAGE_TIME_SERIES_DAILY_V1, "symbol": symbol, "last_refreshed": refreshed, "timezone": timezone, "bars": bars}


def _parse_fmp_eod_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not list or len(raw) != 1 or type(raw[0]) is not dict:
        _fail("FMP_EOD_RECORD_COUNT")
    row = raw[0]
    try:
        symbol, trade_date = row["symbol"], _iso_date(row["date"], "FMP_EOD_DATE_INVALID")
        values = {name: _finite_number(row[name], "FMP_EOD_NUMERIC_INVALID") for name in ("open", "high", "low", "close", "volume")}
    except KeyError:
        _fail("FMP_EOD_FIELD_MISSING")
    if type(symbol) is not str or _US_SYMBOL.fullmatch(symbol) is None:
        _fail("FMP_EOD_SYMBOL_FORMAT_INVALID")
    if symbol != expected_symbol:
        _fail("FMP_EOD_SYMBOL_MISMATCH")
    if values["volume"] < 0:
        _fail("FMP_EOD_VOLUME_NEGATIVE")
    _quote_ohlc(values, "FMP_EOD")
    return {"parser_id": FMP_EOD_V1, "symbol": symbol, "trade_date": trade_date, **values}


def _parse_hangseng_a_share_quote_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not dict or type(raw.get("data")) is not dict or type(raw["data"].get("data")) is not dict or type(raw["data"]["data"].get("rows")) is not list or len(raw["data"]["data"]["rows"]) != 1 or type(raw["data"]["data"]["rows"][0]) is not dict:
        _fail("HANGSENG_A_SHARE_SHAPE_INVALID")
    row = raw["data"]["data"]["rows"][0]
    try:
        symbol, name = row["stockCode"], row["stockName"]
        trade_time = _timestamp(row["tradingTimestamp"], "HANGSENG_A_SHARE_TIME_INVALID")
        currency, status = row["currency"], row["tradeStatus"]
        values = {"open": _finite_number(row["openPrice"], "HANGSENG_A_SHARE_NUMERIC_INVALID"), "high": _finite_number(row["highPrice"], "HANGSENG_A_SHARE_NUMERIC_INVALID"), "low": _finite_number(row["lowPrice"], "HANGSENG_A_SHARE_NUMERIC_INVALID"), "close": _finite_number(row["latestPrice"], "HANGSENG_A_SHARE_NUMERIC_INVALID"), "previous_close": _finite_number(row["prevClosePrice"], "HANGSENG_A_SHARE_NUMERIC_INVALID"), "change": _finite_number(row["changeOfPrice"], "HANGSENG_A_SHARE_NUMERIC_INVALID"), "change_percent": _finite_number(row["changePCT"], "HANGSENG_A_SHARE_NUMERIC_INVALID")}
        raw_lot_fields = {field: row[field] for field in ("turnoverVolumeLot", "realTimeVolumeLot", "selloutVolumeLot", "buyinVolumeLot", "sharesPerHand")}
        raw_l1 = {field: row[field] for field in ("bidGrp", "offerGrp")}
        raw_turnover_value = row["turnoverValue"]
    except KeyError:
        _fail("HANGSENG_A_SHARE_FIELD_MISSING")
    if type(symbol) is not str or not symbol or type(name) is not str or _CAIDAZI_NAME.fullmatch(name) is None:
        _fail("HANGSENG_A_SHARE_IDENTITY_INVALID")
    if name != expected_symbol:
        _fail("HANGSENG_A_SHARE_SYMBOL_MISMATCH")
    if type(currency) is not str or _CURRENCY.fullmatch(currency) is None or type(status) is not str or not status:
        _fail("HANGSENG_A_SHARE_METADATA_INVALID")
    if type(raw_turnover_value) is not str or any(type(value) not in (int, float, str) or isinstance(value, bool) for value in raw_lot_fields.values()) or any(type(value) is not str for value in raw_l1.values()):
        _fail("HANGSENG_A_SHARE_RAW_FIELD_INVALID")
    _quote_ohlc(values, "HANGSENG_A_SHARE")
    return {"parser_id": HANGSENG_A_SHARE_QUOTE_V1, "symbol": symbol, "name": name, "trade_time": trade_time, "currency": currency, "status": status, **values, "raw_lot_fields": raw_lot_fields, "raw_turnover_value": raw_turnover_value, "raw_l1": raw_l1}


def _parse_alphavantage_bulk_bid_ask_v1(raw: Any, expected_symbol: Any) -> dict[str, Any]:
    expected = _expected_symbols(expected_symbol, _US_SYMBOL, "ALPHAVANTAGE_BULK_SYMBOL_INVALID")
    if type(raw) is not dict or type(raw.get("data")) is not list or not raw["data"]:
        _fail("ALPHAVANTAGE_BULK_SHAPE_INVALID")
    quotes = []
    for row in raw["data"]:
        if type(row) is not dict:
            _fail("ALPHAVANTAGE_BULK_ROW_INVALID")
        try:
            symbol, timestamp = row["symbol"], _timestamp(row["timestamp"], "ALPHAVANTAGE_BULK_TIME_INVALID")
            bid, ask = _decimal_string(row["bid_price"], "ALPHAVANTAGE_BULK_NUMBER_INVALID"), _decimal_string(row["ask_price"], "ALPHAVANTAGE_BULK_NUMBER_INVALID")
            bid_size, ask_size = _decimal_string(row["bid_size"], "ALPHAVANTAGE_BULK_NUMBER_INVALID"), _decimal_string(row["ask_size"], "ALPHAVANTAGE_BULK_NUMBER_INVALID")
        except KeyError:
            _fail("ALPHAVANTAGE_BULK_ROW_INVALID")
        if symbol not in expected or Decimal(bid) > Decimal(ask) or Decimal(bid_size) < 0 or Decimal(ask_size) < 0:
            _fail("ALPHAVANTAGE_BULK_QUOTE_INVALID")
        quotes.append({"symbol": symbol, "timestamp": timestamp, "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size, "unit": "unknown"})
    if {quote["symbol"] for quote in quotes} != set(expected):
        _fail("ALPHAVANTAGE_BULK_SYMBOL_MISMATCH")
    return {"parser_id": ALPHAVANTAGE_BULK_BID_ASK_V1, "quotes": quotes}


def _parse_fiu_us_multi_quote_v1(raw: Any, expected_symbol: Any) -> dict[str, Any]:
    expected = _expected_symbols(expected_symbol, _FIU_US_SYMBOL, "FIU_US_MULTI_SYMBOL_INVALID")
    if type(raw) is not dict or type(raw.get("body")) is not list or not raw["body"]:
        _fail("FIU_US_MULTI_SHAPE_INVALID")
    quotes = []
    for row in raw["body"]:
        if type(row) is not dict or type(row.get("snapshot")) is not dict or type(row.get("trade")) is not dict or type(row.get("order")) is not dict:
            _fail("FIU_US_MULTI_ROW_INVALID")
        snapshot = row["snapshot"]
        symbol = row.get("symbol", snapshot.get("symbol"))
        if symbol not in expected:
            _fail("FIU_US_MULTI_SYMBOL_MISMATCH")
        price = next((snapshot[key] for key in ("last", "price", "latest", "close") if key in snapshot), None)
        if price is None:
            _fail("FIU_US_MULTI_PRICE_MISSING")
        trade_time = next((snapshot[key] for key in ("time", "timestamp") if key in snapshot), row.get("time"))
        quotes.append({"symbol": symbol, "last": _decimal_string(price, "FIU_US_MULTI_NUMBER_INVALID"), "trade_time": _timestamp(trade_time, "FIU_US_MULTI_TIME_INVALID"), "snapshot": _json_safe(snapshot, "FIU_US_MULTI_VALUE_INVALID"), "trade": _json_safe(row["trade"], "FIU_US_MULTI_VALUE_INVALID"), "order": _json_safe(row["order"], "FIU_US_MULTI_VALUE_INVALID"), "unit": "unknown"})
    if {quote["symbol"] for quote in quotes} != set(expected):
        _fail("FIU_US_MULTI_SYMBOL_MISMATCH")
    return {"parser_id": FIU_US_MULTI_QUOTE_V1, "quotes": quotes}


def _parse_qveris_after_hours_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if _US_SYMBOL.fullmatch(expected_symbol) is None or type(raw) is not dict:
        _fail("AFTER_HOURS_SHAPE_INVALID")
    try:
        symbol, timestamp = raw["symbol"], _timestamp(raw["timestamp"], "AFTER_HOURS_TIME_INVALID")
        price = _decimal_string(raw["price"], "AFTER_HOURS_NUMBER_INVALID")
    except KeyError:
        _fail("AFTER_HOURS_FIELD_MISSING")
    if symbol != expected_symbol:
        _fail("AFTER_HOURS_SYMBOL_MISMATCH")
    result = {"parser_id": QVERIS_AFTER_HOURS_V1, "symbol": symbol, "timestamp": timestamp, "price": price, "unit": "unknown"}
    for key in ("change", "change_percent", "previous_close", "volume"):
        if key in raw:
            result[key] = _decimal_string(raw[key], "AFTER_HOURS_NUMBER_INVALID")
    for key in ("currency", "exchange", "name", "pre_post_timestamp"):
        if key in raw:
            if type(raw[key]) is not str or not raw[key]:
                _fail("AFTER_HOURS_METADATA_INVALID")
            result[key] = raw[key]
    return result


def _parse_alphavantage_intraday_bars_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not dict or type(raw.get("Meta Data")) is not dict:
        _fail("ALPHAVANTAGE_INTRADAY_SHAPE_INVALID")
    metadata = raw["Meta Data"]
    series = next((value for key, value in raw.items() if key.startswith("Time Series (") and type(value) is dict), None)
    if type(series) is not dict or not series:
        _fail("ALPHAVANTAGE_INTRADAY_SERIES_INVALID")
    try:
        symbol, refreshed, interval, timezone = metadata["2. Symbol"], _timestamp(metadata["3. Last Refreshed"], "ALPHAVANTAGE_INTRADAY_TIME_INVALID"), metadata["4. Interval"], metadata["6. Time Zone"]
    except KeyError:
        _fail("ALPHAVANTAGE_INTRADAY_METADATA_INVALID")
    if symbol != expected_symbol or _US_SYMBOL.fullmatch(symbol) is None or type(interval) is not str or type(timezone) is not str or not timezone:
        _fail("ALPHAVANTAGE_INTRADAY_SYMBOL_OR_METADATA_INVALID")
    bars = []
    for timestamp, row in sorted(series.items(), reverse=True):
        if type(row) is not dict:
            _fail("ALPHAVANTAGE_INTRADAY_ROW_INVALID")
        try:
            values = {name: _decimal_string(row[key], "ALPHAVANTAGE_INTRADAY_NUMBER_INVALID") for name, key in (("open", "1. open"), ("high", "2. high"), ("low", "3. low"), ("close", "4. close"), ("volume", "5. volume"))}
        except KeyError:
            _fail("ALPHAVANTAGE_INTRADAY_ROW_INVALID")
        if Decimal(values["volume"]) < 0:
            _fail("ALPHAVANTAGE_INTRADAY_VOLUME_NEGATIVE")
        _quote_ohlc({key: Decimal(value) for key, value in values.items() if key != "volume"}, "ALPHAVANTAGE_INTRADAY")
        bars.append({"timestamp": _timestamp(timestamp, "ALPHAVANTAGE_INTRADAY_TIME_INVALID"), **values, "unit": "unknown"})
    return {"parser_id": ALPHAVANTAGE_INTRADAY_BARS_V1, "symbol": symbol, "last_refreshed": refreshed, "interval": interval, "timezone": timezone, "adjustment_status": "unknown", "bars": bars}


def _parse_alphavantage_financial_statement(
    raw: Any,
    expected_symbol: str,
    *,
    parser_id: str,
    statement_type: str,
    fields: dict[str, str],
) -> dict[str, Any]:
    """Read one inline, observed Alpha statement row without report-order inference."""
    if expected_symbol != "AAPL" or type(raw) is not dict:
        _fail("ALPHAVANTAGE_FINANCIAL_SHAPE_INVALID")
    result = raw.get("result")
    if type(result) is not dict or type(result.get("data")) is not dict:
        _fail("ALPHAVANTAGE_FINANCIAL_INLINE_DATA_REQUIRED")
    data = result["data"]
    if data.get("symbol") != "AAPL":
        _fail("ALPHAVANTAGE_FINANCIAL_SYMBOL_MISMATCH")
    reports_key = "annualReports" if data.get("annualReports") else "quarterlyReports"
    reports = data.get(reports_key)
    if type(reports) is not list or not reports or type(reports[0]) is not dict:
        _fail("ALPHAVANTAGE_FINANCIAL_REPORTS_INVALID")
    row = reports[0]
    date = row.get("fiscalDateEnding", row.get("reportedDate"))
    currency = row.get("reportedCurrency", row.get("currency"))
    if _iso_date(date, "ALPHAVANTAGE_FINANCIAL_DATE_INVALID") != date:
        _fail("ALPHAVANTAGE_FINANCIAL_DATE_INVALID")
    if type(currency) is not str or _CURRENCY.fullmatch(currency) is None:
        _fail("ALPHAVANTAGE_FINANCIAL_CURRENCY_INVALID")
    metrics = {
        canonical: _decimal_string(row[source], "ALPHAVANTAGE_FINANCIAL_NUMBER_INVALID")
        for source, canonical in fields.items()
        if source in row and row[source] is not None
    }
    if not metrics:
        _fail("ALPHAVANTAGE_FINANCIAL_CORE_FIELD_MISSING")
    return {
        "parser_id": parser_id,
        "symbol": "AAPL",
        "statement_type": statement_type,
        "period": "annual" if reports_key == "annualReports" else "quarterly",
        "report_date": date,
        "reported_currency": currency,
        "metrics": metrics,
    }


def _parse_alphavantage_income_statement_list_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    return _parse_alphavantage_financial_statement(
        raw, expected_symbol, parser_id=ALPHAVANTAGE_INCOME_STATEMENT_LIST_V1,
        statement_type="income_statement", fields={"revenue": "revenue", "netIncome": "net_income"},
    )


def _parse_alphavantage_balance_sheet_retrieve_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    return _parse_alphavantage_financial_statement(
        raw, expected_symbol, parser_id=ALPHAVANTAGE_BALANCE_SHEET_RETRIEVE_V1,
        statement_type="balance_sheet", fields={
            "totalAssets": "total_assets", "totalLiabilities": "total_liabilities",
            "totalStockholdersEquity": "total_equity",
        },
    )


def _parse_alphavantage_cash_flow_retrieve_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    return _parse_alphavantage_financial_statement(
        raw, expected_symbol, parser_id=ALPHAVANTAGE_CASH_FLOW_RETRIEVE_V1,
        statement_type="cash_flow", fields={
            "operatingCashflow": "net_cash_from_operating",
            "cashflowFromInvestment": "net_cash_from_investing",
            "cashflowFromFinancing": "net_cash_from_financing",
            "changeInCash": "net_increase_in_cash",
            "cashAndCashEquivalentsAtEnd": "cash_and_cash_equivalents_at_end",
        },
    )


_FMP_STANDARD_FIELDS = {
    "income_statement": {
        "revenue": "revenue",
        "costOfRevenue": "cost_of_revenue",
        "grossProfit": "gross_profit",
        "researchAndDevelopmentExpenses": "research_and_development_expense",
        "sellingGeneralAndAdministrativeExpenses": "selling_general_and_administrative_expense",
        "operatingIncome": "operating_income",
        "incomeBeforeTax": "income_before_tax",
        "incomeTaxExpense": "income_tax_expense",
    },
    "balance_sheet": {
        "totalAssets": "total_assets",
        "totalLiabilities": "total_liabilities",
        "totalStockholdersEquity": "total_equity",
    },
    "cash_flow": {
        "netCashProvidedByOperatingActivities": "net_cash_from_operating",
        "netCashProvidedByInvestingActivities": "net_cash_from_investing",
        "netCashProvidedByFinancingActivities": "net_cash_from_financing",
        "netChangeInCash": "net_increase_in_cash",
    },
}


def _expected_fmp_statement_identity(expected_identity: Any, expected_symbol: str, prefix: str) -> dict[str, str]:
    """Require the caller's requested filing identity; never infer one from order."""
    if type(expected_identity) is not dict or set(expected_identity) != {
        "symbol", "report_date", "fiscal_year", "period", "reported_currency",
    }:
        _fail(f"{prefix}_EXPECTED_IDENTITY_REQUIRED")
    symbol = expected_identity["symbol"]
    report_date = _iso_date(expected_identity["report_date"], f"{prefix}_EXPECTED_IDENTITY_INVALID")
    fiscal_year = str(expected_identity["fiscal_year"])
    period = expected_identity["period"]
    currency = expected_identity["reported_currency"]
    if (
        symbol != expected_symbol
        or re.fullmatch(r"[0-9]{4}", fiscal_year) is None
        or type(period) is not str
        or re.fullmatch(r"(?:Q[1-4]|FY)", period) is None
        or type(currency) is not str
        or _CURRENCY.fullmatch(currency) is None
    ):
        _fail(f"{prefix}_EXPECTED_IDENTITY_INVALID")
    return {
        "symbol": symbol,
        "report_date": report_date,
        "fiscal_year": fiscal_year,
        "period": period,
        "reported_currency": currency,
    }


def _parse_fmp_standard_statement(
    raw: Any,
    expected_symbol: str,
    *,
    parser_id: str,
    statement_type: str,
    expected_identity: Any,
) -> dict[str, Any]:
    """Read the one observed FMP standard-statement row; do not rank reports."""
    if _US_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("FMP_STANDARD_SYMBOL_INVALID")
    rows = _rows(_structured(raw, "FMP_STANDARD"), "FMP_STANDARD")
    if len(rows) != 1:
        _fail("FMP_STANDARD_RECORD_COUNT")
    row = rows[0]
    if row.get("symbol") != expected_symbol:
        _fail("FMP_STANDARD_SYMBOL_MISMATCH")
    report_date = _iso_date(row.get("date"), "FMP_STANDARD_DATE_INVALID")
    period = row.get("period")
    fiscal_year = row.get("fiscalYear")
    currency = row.get("reportedCurrency")
    if (
        type(period) is not str
        or not period
        or period.strip() != period
        or type(fiscal_year) is not str
        or re.fullmatch(r"[0-9]{4}", fiscal_year) is None
        or type(currency) is not str
        or _CURRENCY.fullmatch(currency) is None
    ):
        _fail("FMP_STANDARD_METADATA_INVALID")
    identity = _expected_fmp_statement_identity(expected_identity, expected_symbol, "FMP_STANDARD")
    if {
        "symbol": expected_symbol,
        "report_date": report_date,
        "fiscal_year": fiscal_year,
        "period": period,
        "reported_currency": currency,
    } != identity:
        _fail("FMP_STANDARD_EXPECTED_IDENTITY_MISMATCH")
    metrics = {
        canonical: _decimal_string(row[source], "FMP_STANDARD_NUMBER_INVALID")
        for source, canonical in _FMP_STANDARD_FIELDS[statement_type].items()
        if source in row and row[source] is not None
    }
    if not metrics:
        _fail("FMP_STANDARD_FIELDS_MISSING")
    return {
        "parser_id": parser_id,
        "symbol": expected_symbol,
        "statement_type": statement_type,
        "period": period,
        "period_status": "provider_reported",
        "fiscal_year": fiscal_year,
        "report_date": report_date,
        "reported_currency": currency,
        "unit": "unknown",
        "identity_status": "matched_expected_request",
        "metrics": metrics,
    }


def _parse_fmp_standard_income_statement_v1(raw: Any, expected_symbol: str, expected_identity: Any) -> dict[str, Any]:
    return _parse_fmp_standard_statement(raw, expected_symbol, parser_id=FMP_STANDARD_INCOME_STATEMENT_V1, statement_type="income_statement", expected_identity=expected_identity)


def _parse_fmp_standard_balance_sheet_v1(raw: Any, expected_symbol: str, expected_identity: Any) -> dict[str, Any]:
    return _parse_fmp_standard_statement(raw, expected_symbol, parser_id=FMP_STANDARD_BALANCE_SHEET_V1, statement_type="balance_sheet", expected_identity=expected_identity)


def _parse_fmp_standard_cash_flow_v1(raw: Any, expected_symbol: str, expected_identity: Any) -> dict[str, Any]:
    return _parse_fmp_standard_statement(raw, expected_symbol, parser_id=FMP_STANDARD_CASH_FLOW_V1, statement_type="cash_flow", expected_identity=expected_identity)


def _parse_fmp_as_reported_income_v1(raw: Any, expected_symbol: str, expected_identity: Any) -> dict[str, Any]:
    """Project only the two observed XBRL tags from one quarterly filing row."""
    if _US_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("FMP_AS_REPORTED_SYMBOL_INVALID")
    rows = _rows(_structured(raw, "FMP_AS_REPORTED"), "FMP_AS_REPORTED")
    if len(rows) != 1:
        _fail("FMP_AS_REPORTED_RECORD_COUNT")
    row = rows[0]
    if row.get("symbol") != expected_symbol:
        _fail("FMP_AS_REPORTED_SYMBOL_MISMATCH")
    report_date = _iso_date(row.get("date"), "FMP_AS_REPORTED_DATE_INVALID")
    fiscal_year = row.get("fiscalYear")
    period = row.get("period")
    currency = row.get("reportedCurrency")
    if (
        type(fiscal_year) not in (str, int)
        or re.fullmatch(r"[0-9]{4}", str(fiscal_year)) is None
        or type(period) is not str
        or re.fullmatch(r"Q[1-4]", period) is None
        or type(currency) is not str
        or _CURRENCY.fullmatch(currency) is None
        or type(row.get("data")) is not dict
    ):
        _fail("FMP_AS_REPORTED_METADATA_INVALID")
    identity = _expected_fmp_statement_identity(expected_identity, expected_symbol, "FMP_AS_REPORTED")
    if {
        "symbol": expected_symbol,
        "report_date": report_date,
        "fiscal_year": str(fiscal_year),
        "period": period,
        "reported_currency": currency,
    } != identity:
        _fail("FMP_AS_REPORTED_EXPECTED_IDENTITY_MISMATCH")
    tags = row["data"]
    try:
        metrics = {
            "revenue": _decimal_string(tags["revenuefromcontractwithcustomerexcludingassessedtax"], "FMP_AS_REPORTED_NUMBER_INVALID"),
            "net_income": _decimal_string(tags["netincomeloss"], "FMP_AS_REPORTED_NUMBER_INVALID"),
        }
    except KeyError:
        _fail("FMP_AS_REPORTED_CORE_TAGS_MISSING")
    return {
        "parser_id": FMP_AS_REPORTED_INCOME_V1,
        "symbol": expected_symbol,
        "statement_type": "income_statement",
        "reporting_basis": "as_reported",
        "period": period,
        "period_status": "provider_reported",
        "fiscal_year": str(fiscal_year),
        "report_date": report_date,
        "reported_currency": currency,
        "unit": "unknown",
        "identity_status": "matched_expected_request",
        "metrics": metrics,
    }


_FIU_SSE_FIELDS = {
    "income_statement": {"totalOperIncome": "revenue", "netProfit": "net_income"},
    "balance_sheet": {"totalAsset": "total_assets", "totalLiab": "total_liabilities", "totalSHEquity": "total_equity"},
    "cash_flow": {
        "netCashFlowOper": "net_cash_from_operating",
        "netCashFlowInv": "net_cash_from_investing",
        "netCashFlowFina": "net_cash_from_financing",
        "cashEquiNetIncr": "net_increase_in_cash",
    },
}


def _parse_fiu_sse_standard_statement(
    raw: Any,
    expected_symbol: str,
    *,
    parser_id: str,
    statement_type: str,
) -> dict[str, Any]:
    """Read the one observed FIU SSE statement envelope without unit inference."""
    if _A_SHARE_SYMBOL.fullmatch(expected_symbol) is None or not expected_symbol.endswith(".SH"):
        _fail("FIU_SSE_SYMBOL_INVALID")
    raw = _structured(raw, "FIU_SSE")
    if type(raw) is not dict or type(raw.get("data")) is not list:
        _fail("FIU_SSE_SHAPE_INVALID")
    rows = _rows(raw["data"], "FIU_SSE")
    if len(rows) != 1:
        _fail("FIU_SSE_RECORD_COUNT")
    row = rows[0]
    if row.get("symbol") != expected_symbol:
        _fail("FIU_SSE_SYMBOL_MISMATCH")
    report_date = _iso_date(row.get("reportDate"), "FIU_SSE_REPORT_DATE_INVALID")
    metrics = {
        canonical: _decimal_string(row[source], "FIU_SSE_NUMBER_INVALID")
        for source, canonical in _FIU_SSE_FIELDS[statement_type].items()
        if source in row and row[source] is not None
    }
    if not metrics:
        _fail("FIU_SSE_FIELDS_MISSING")
    return {
        "parser_id": parser_id,
        "symbol": expected_symbol,
        "statement_type": statement_type,
        "period": report_date,
        "period_status": "reported_end_date_not_fiscal_basis",
        "fiscal_year": None,
        "report_date": report_date,
        "reported_currency": "unknown",
        "unit": "unknown",
        "metrics": metrics,
    }


def _parse_fiu_sse_income_statement_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    return _parse_fiu_sse_standard_statement(raw, expected_symbol, parser_id=FIU_SSE_INCOME_STATEMENT_V1, statement_type="income_statement")


def _parse_fiu_sse_balance_sheet_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    return _parse_fiu_sse_standard_statement(raw, expected_symbol, parser_id=FIU_SSE_BALANCE_SHEET_V1, statement_type="balance_sheet")


def _parse_fiu_sse_cash_flow_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    return _parse_fiu_sse_standard_statement(raw, expected_symbol, parser_id=FIU_SSE_CASH_FLOW_V1, statement_type="cash_flow")


def _parse_cnfp_realtime_quote_v1(raw: Any, expected_symbol: Any) -> dict[str, Any]:
    raw = _structured(raw, "CNFP_QUOTE")
    expected = _expected_symbols(expected_symbol, _A_SHARE_SYMBOL, "CNFP_QUOTE_SYMBOL_INVALID")
    raw = _rows(raw, "CNFP_QUOTE")
    quotes = []
    for row in raw:
        if type(row) is not dict:
            _fail("CNFP_QUOTE_ROW_INVALID")
        try:
            symbol, timestamp = row["thscode"], _timestamp(row["time"], "CNFP_QUOTE_TIME_INVALID")
            values = {name: _decimal_string(row[key], "CNFP_QUOTE_NUMBER_INVALID") for name, key in (("previous_close", "preClose"), ("open", "open"), ("high", "high"), ("low", "low"), ("close", "latest"), ("volume", "volume"), ("amount", "amount"))}
        except KeyError:
            _fail("CNFP_QUOTE_ROW_INVALID")
        if symbol not in expected or Decimal(values["volume"]) < 0 or Decimal(values["amount"]) < 0:
            _fail("CNFP_QUOTE_VALUE_INVALID")
        _quote_ohlc({key: Decimal(values[key]) for key in ("open", "high", "low", "close")}, "CNFP_QUOTE")
        quotes.append({"symbol": symbol, "timestamp": timestamp, **values, "unit": "unknown"})
    if {quote["symbol"] for quote in quotes} != set(expected):
        _fail("CNFP_QUOTE_SYMBOL_MISMATCH")
    return {"parser_id": CNFP_REALTIME_QUOTE_V1, "quotes": quotes}


def _parse_cnfp_intraday_bars_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    raw = _structured(raw, "CNFP_INTRADAY")
    if _A_SHARE_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("CNFP_INTRADAY_SHAPE_INVALID")
    raw = _rows(raw, "CNFP_INTRADAY")
    bars = []
    for row in raw:
        if type(row) is not dict:
            _fail("CNFP_INTRADAY_ROW_INVALID")
        try:
            symbol, timestamp = row["thscode"], _timestamp(row["time"], "CNFP_INTRADAY_TIME_INVALID")
            values = {name: _decimal_string(row[key], "CNFP_INTRADAY_NUMBER_INVALID") for name, key in (("open", "开盘价"), ("high", "最高价"), ("low", "最低价"), ("close", "收盘价"), ("volume", "成交量"), ("amount", "成交额"))}
        except KeyError:
            _fail("CNFP_INTRADAY_ROW_INVALID")
        if symbol != expected_symbol or Decimal(values["volume"]) < 0 or Decimal(values["amount"]) < 0:
            _fail("CNFP_INTRADAY_VALUE_INVALID")
        _quote_ohlc({key: Decimal(values[key]) for key in ("open", "high", "low", "close")}, "CNFP_INTRADAY")
        bars.append({"timestamp": timestamp, **values, "unit": "unknown"})
    return {"parser_id": CNFP_INTRADAY_BARS_V1, "symbol": expected_symbol, "bars": bars}


def _parse_cnfp_adjustment_factor_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    raw = _structured(raw, "CNFP_ADJUSTMENT")
    if _A_SHARE_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("CNFP_ADJUSTMENT_SHAPE_INVALID")
    raw = _rows(raw, "CNFP_ADJUSTMENT")
    factors = []
    for row in raw:
        if type(row) is not dict:
            _fail("CNFP_ADJUSTMENT_ROW_INVALID")
        try:
            symbol, trade_date = row["stock_code"], _period(row["date"], "CNFP_ADJUSTMENT_DATE_INVALID")
            factor = _decimal_string(row["adjustment_factor"], "CNFP_ADJUSTMENT_NUMBER_INVALID")
        except KeyError:
            _fail("CNFP_ADJUSTMENT_ROW_INVALID")
        if symbol != expected_symbol or Decimal(factor) <= 0:
            _fail("CNFP_ADJUSTMENT_VALUE_INVALID")
        factors.append({"trade_date": trade_date, "adjustment_factor": factor, "unit": "unknown"})
    return {"parser_id": CNFP_ADJUSTMENT_FACTOR_V1, "symbol": expected_symbol, "factors": factors}


def _parse_caidazi_a_share_quote_envelope_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    if type(raw) is not dict or raw.get("error") is not None or type(raw.get("result")) is not str:
        _fail("CAIDAZI_ENVELOPE_INVALID")
    result = _parse_caidazi_a_share_quote_markdown_v1(raw["result"], expected_symbol)
    fields = result["fields"]
    bid, ask = fields.get("委托买盘价"), fields.get("委托卖盘价")
    if bid is None or ask is None:
        _fail("CAIDAZI_ENVELOPE_BID_ASK_MISSING")
    return {"parser_id": CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1, "symbol": result["symbol"], "trade_time": result["trade_time"], "bid": bid["decimal"], "ask": ask["decimal"], "unit": "unknown", "halted": result["halted"]}


_CNFP_FINANCIAL_METRICS = {
    "ths_operating_total_revenue_stock": "revenue",
    "ths_np_stock": "net_income",
    "ths_total_assets_stock": "total_assets",
    "ths_total_liab_stock": "total_liabilities",
    "ths_total_owner_equity_stock": "total_equity",
    "ths_ncf_from_oa_stock": "net_cash_from_operating",
    "ths_ncf_from_ia_stock": "net_cash_from_investing",
    "ths_ncf_from_fa_stock": "net_cash_from_financing",
    "ths_net_increase_in_cce_stock": "net_increase_in_cash",
}


# Direct-field requests arrive here only after the semantic agent has resolved
# user wording to canonical fields. This layer intentionally has no aliases.
_FINANCIAL_STATEMENT_FIELDS = {
    "US": {
        "income_statement": frozenset({
        "revenue", "cost_of_revenue", "gross_profit", "research_and_development_expense",
        "selling_general_and_administrative_expense", "operating_income",
        "income_before_tax", "income_tax_expense",
        }),
        "balance_sheet": frozenset({"total_assets", "total_liabilities", "total_equity"}),
    },
    "SSE": {
        "income_statement": frozenset({"revenue", "net_income"}),
        "balance_sheet": frozenset({"total_assets", "total_liabilities", "total_equity"}),
        "cash_flow": frozenset({
            "net_cash_from_operating", "net_cash_from_investing", "net_cash_from_financing",
            "net_increase_in_cash",
        }),
    },
    "SZSE": {
        "income_statement": frozenset({"revenue", "net_income"}),
        "balance_sheet": frozenset({"total_assets", "total_liabilities", "total_equity"}),
        "cash_flow": frozenset({
            "net_cash_from_operating", "net_cash_from_investing", "net_cash_from_financing",
            "net_increase_in_cash",
        }),
    },
    "HKEX": {
        "income_statement": frozenset({
        "revenue", "cost_of_revenue", "gross_profit", "profit_before_taxation", "taxation",
        "profit_the_period", "net_income_attributable", "non_controlling_interests",
        "other_comprehensive_income", "total_comprehensive_income", "selling_expense",
        "share_of_profit_from_joint_ventures_and_associates", "weighted_average_shares",
        }),
    },
}
_DIRECT_FINANCIAL_FIELD_ALLOWLISTS = {
    market: frozenset().union(*statement_fields.values())
    for market, statement_fields in _FINANCIAL_STATEMENT_FIELDS.items()
}
_FINANCIAL_PROJECTION_METADATA = (
    "symbol", "period", "period_status", "fiscal_year", "report_date",
    "currency", "reported_currency", "unit", "amount_unit",
)


def _projection_json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            _fail("FINANCIAL_PROJECTION_VALUE_INVALID")
        return format(value, "f")
    return _json_safe(value, "FINANCIAL_PROJECTION_VALUE_INVALID")


def _projection_metric_decimal(value: Any) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            _fail("FINANCIAL_PROJECTION_VALUE_INVALID")
        return format(value, "f")
    return _decimal_string(value, "FINANCIAL_PROJECTION_VALUE_INVALID")


def _requested_statement_type(market: str, fields: list[str]) -> str:
    statement_types = {
        statement_type
        for field in fields
        for statement_type, allowed_fields in _FINANCIAL_STATEMENT_FIELDS[market].items()
        if field in allowed_fields
    }
    if len(statement_types) != 1:
        _fail("FINANCIAL_STATEMENT_TYPE_UNRESOLVED" if not statement_types else "FINANCIAL_CROSS_STATEMENT_REQUEST")
    return statement_types.pop()


_US_DIRECT_LINE_PARSERS = {
    FMP_STANDARD_INCOME_STATEMENT_V1: "income_statement",
    FMP_STANDARD_BALANCE_SHEET_V1: "balance_sheet",
}


def _validate_direct_financial_evidence(market: str, statement_type: str, statement: dict[str, Any]) -> None:
    if market != "US":
        return
    parser_id = statement.get("parser_id")
    if _US_DIRECT_LINE_PARSERS.get(parser_id) != statement_type:
        _fail("FINANCIAL_US_DIRECT_EVIDENCE_REQUIRED")
    if statement.get("identity_status") != "matched_expected_request":
        _fail("FINANCIAL_US_DIRECT_IDENTITY_UNVERIFIED")


def project_requested_financial_fields(
    market: str,
    requested_fields: Any,
    normalized_statement: Any,
) -> dict[str, Any]:
    """Project exact canonical fields from one already-normalized statement."""
    allowlist = _DIRECT_FINANCIAL_FIELD_ALLOWLISTS.get(market)
    if allowlist is None or type(normalized_statement) is not dict:
        _fail("FINANCIAL_PROJECTION_INPUT_INVALID")
    if type(requested_fields) not in (list, tuple):
        _fail("FINANCIAL_REQUESTED_FIELDS_INVALID")
    fields: list[str] = []
    for field in requested_fields:
        if type(field) is not str or not field or field.strip() != field or field in fields:
            if type(field) is not str or not field or field.strip() != field:
                _fail("FINANCIAL_REQUESTED_FIELDS_INVALID")
            continue
        fields.append(field)
    if not fields:
        _fail("FINANCIAL_REQUESTED_FIELDS_INVALID")
    statement_type = _requested_statement_type(market, fields)
    _validate_direct_financial_evidence(market, statement_type, normalized_statement)
    declared_statement_type = normalized_statement.get("statement_type")
    if declared_statement_type is not None and declared_statement_type != statement_type:
        _fail("FINANCIAL_STATEMENT_TYPE_MISMATCH")
    metric_source = normalized_statement.get("metrics", normalized_statement)
    if type(metric_source) is not dict:
        _fail("FINANCIAL_PROJECTION_INPUT_INVALID")
    data = {
        field: _projection_metric_decimal(metric_source[field])
        for field in fields
        if field in allowlist and metric_source.get(field) is not None
    }
    unsupported = [field for field in fields if field not in data]
    status = "success" if not unsupported else "partial" if data else "unsupported"
    metadata = {
        field: _projection_json_safe(normalized_statement[field])
        for field in _FINANCIAL_PROJECTION_METADATA if field in normalized_statement
    }
    return {
        "status": status,
        "market": market,
        "statement_type": statement_type,
        "requested_fields": fields,
        "data": data,
        "unsupported_fields": unsupported,
        "metadata": metadata,
    }


def _parse_cnfp_financial_row_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    raw = _structured(raw, "CNFP_FINANCIAL")
    if _A_SHARE_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("CNFP_FINANCIAL_SHAPE_INVALID")
    raw = _rows(raw, "CNFP_FINANCIAL")
    if len(raw) != 1:
        _fail("CNFP_FINANCIAL_SHAPE_INVALID")
    row = raw[0]
    if row.get("thscode") != expected_symbol:
        _fail("CNFP_FINANCIAL_SYMBOL_MISMATCH")
    raw_period = row.get("time")
    if raw_period is not None and type(raw_period) is not str:
        _fail("CNFP_FINANCIAL_PERIOD_INVALID")
    period = None if raw_period is None else _period(raw_period, "CNFP_FINANCIAL_PERIOD_INVALID")
    source_fields = {
        key: None if value is None else _decimal_string(value, "CNFP_FINANCIAL_NUMBER_INVALID")
        for key, value in row.items() if key.startswith("ths_")
    }
    if not source_fields:
        _fail("CNFP_FINANCIAL_FIELDS_MISSING")
    metrics = {canonical: source_fields[source] for source, canonical in _CNFP_FINANCIAL_METRICS.items() if source_fields.get(source) is not None}
    if not metrics:
        _fail("CNFP_FINANCIAL_TYPE_UNSUPPORTED")
    kinds = ("income_statement", "balance_sheet", "cash_flow")
    statement_type = next((kind for kind, source in zip(kinds, ("ths_np_stock", "ths_total_assets_stock", "ths_ncf_from_oa_stock")) if source in source_fields), None)
    return {"parser_id": CNFP_FINANCIAL_ROW_V1, "symbol": expected_symbol, "period": period, "period_status": "unknown" if period is None else "reported", "statement_type": statement_type, "metrics": metrics, "source_fields": source_fields, "unit": "unknown"}


def _parse_hangseng_hk_batch_quote_v1(raw: Any, expected_symbol: Any) -> dict[str, Any]:
    expected = _expected_symbols(expected_symbol, _CAIDAZI_NAME, "HANGSENG_HK_SYMBOL_INVALID")
    try:
        rows = raw["data"]["data"]["rows"]
    except (KeyError, TypeError):
        _fail("HANGSENG_HK_SHAPE_INVALID")
    if type(rows) is not list or not rows:
        _fail("HANGSENG_HK_SHAPE_INVALID")
    quotes = []
    for row in rows:
        if type(row) is not dict:
            _fail("HANGSENG_HK_ROW_INVALID")
        try:
            name, code, timestamp, currency, status = row["stockName"], row["stockCode"], _timestamp(row["tradingTimestamp"], "HANGSENG_HK_TIME_INVALID"), row["currency"], row["tradeStatus"]
            values = {name: _decimal_string(row[key], "HANGSENG_HK_NUMBER_INVALID") for name, key in (("open", "openPrice"), ("high", "highPrice"), ("low", "lowPrice"), ("close", "latestPrice"), ("previous_close", "prevClosePrice"))}
        except KeyError:
            _fail("HANGSENG_HK_ROW_INVALID")
        if name not in expected or type(code) is not str or not code or type(currency) is not str or _CURRENCY.fullmatch(currency) is None or type(status) is not str or not status:
            _fail("HANGSENG_HK_IDENTITY_INVALID")
        _quote_ohlc({key: Decimal(values[key]) for key in ("open", "high", "low", "close")}, "HANGSENG_HK")
        quotes.append({"symbol": code, "name": name, "timestamp": timestamp, "currency": currency, "status": status, **values, "unit": "unknown"})
    if {quote["name"] for quote in quotes} != set(expected):
        _fail("HANGSENG_HK_SYMBOL_MISMATCH")
    return {"parser_id": HANGSENG_HK_BATCH_QUOTE_V1, "quotes": quotes}


def _parse_hangseng_hk_l1_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    """Parse the one observed Hang Seng HK best-bid/best-offer presentation."""
    if type(expected_symbol) is not str or _HK_SYMBOL.fullmatch(expected_symbol) is None:
        _fail("HANGSENG_HK_L1_SYMBOL_INVALID")
    try:
        rows = raw["data"]["data"]["rows"]
    except (KeyError, TypeError):
        _fail("HANGSENG_HK_L1_SHAPE_INVALID")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        _fail("HANGSENG_HK_L1_SHAPE_INVALID")
    row = rows[0]
    code = row.get("stockCode")
    if type(code) is not str or re.fullmatch(r"[0-9]{5}", code) is None or code + ".HK" != expected_symbol:
        _fail("HANGSENG_HK_L1_SYMBOL_MISMATCH")
    if type(row.get("stockName")) is not str or not row["stockName"] or row.get("currency") != "HKD" or type(row.get("tradeStatus")) is not str or not row["tradeStatus"]:
        _fail("HANGSENG_HK_L1_METADATA_INVALID")
    timestamp = _timestamp(row.get("tradingTimestamp"), "HANGSENG_HK_L1_TIME_INVALID")
    def group(field: str, side: str) -> tuple[str, str]:
        value = row.get(field)
        if type(value) is not str:
            _fail("HANGSENG_HK_L1_GROUP_INVALID")
        matched = re.fullmatch(side + r"一价:([^,]+)HKD," + side + r"一挂单量:([^,]+)手", value)
        if matched is None:
            _fail("HANGSENG_HK_L1_GROUP_INVALID")
        price, size = (_decimal_string(part, "HANGSENG_HK_L1_NUMBER_INVALID") for part in matched.groups())
        if Decimal(price) <= 0 or Decimal(size) < 0:
            _fail("HANGSENG_HK_L1_NUMBER_INVALID")
        return price, size
    bid, bid_size = group("bidGrp", "买")
    ask, ask_size = group("offerGrp", "卖")
    if Decimal(bid) > Decimal(ask):
        _fail("HANGSENG_HK_L1_CROSSED_BOOK")
    return {
        "parser_id": HANGSENG_HK_L1_V1,
        "symbol": expected_symbol,
        "name": row["stockName"],
        "timestamp": timestamp,
        "status": row["tradeStatus"],
        "bid": bid,
        "bid_size": bid_size,
        "ask": ask,
        "ask_size": ask_size,
        "price_unit": "HKD",
        "size_unit": "lots",
    }


def _parse_fiu_sse_dividend_schema_v1(raw: Any, expected_symbol: str) -> dict[str, Any]:
    """Validate only the observed FIU corporate-action envelope before fields freeze."""
    if type(expected_symbol) is not str or _A_SHARE_SYMBOL.fullmatch(expected_symbol) is None or not expected_symbol.endswith(".SH"):
        _fail("FIU_SSE_DIVIDEND_SYMBOL_INVALID")
    if type(raw) is not dict or type(raw.get("action")) is not str or not raw["action"] or type(raw.get("code")) is not str or type(raw.get("msg")) is not str:
        _fail("FIU_SSE_DIVIDEND_ENVELOPE_INVALID")
    events = raw.get("data")
    if type(events) is not list or not events or any(type(event) is not dict for event in events):
        _fail("FIU_SSE_DIVIDEND_EVENTS_INVALID")
    if any(event.get("symbol") != expected_symbol for event in events):
        _fail("FIU_SSE_DIVIDEND_SYMBOL_MISMATCH")
    return {"parser_id": FIU_SSE_DIVIDEND_SCHEMA_V1, "symbol": expected_symbol, "event_count": len(events), "field_contract": "unfrozen"}


def _parse_fiu_sse_dividends_v1(raw: Any, expected: dict[str, str]) -> dict[str, Any]:
    """Project only observed SSE dividend events; a null rate remains partial."""
    symbol, start, end = expected["symbol"], expected["start_date"], expected["end_date"]
    if type(raw) is not dict or type(raw.get("action")) is not str or not raw["action"] or type(raw.get("code")) is not str or type(raw.get("msg")) is not str:
        _fail("FIU_SSE_DIVIDEND_ENVELOPE_INVALID")
    source_events = raw.get("data")
    if type(source_events) is not list or not source_events or any(type(event) is not dict for event in source_events):
        _fail("FIU_SSE_DIVIDEND_EVENTS_INVALID")
    events = []
    parsed_count = 0
    for event in source_events:
        if event.get("symbol") != symbol:
            _fail("FIU_SSE_DIVIDEND_SYMBOL_MISMATCH")
        ex_date = _iso_date(event.get("exDate"), "FIU_SSE_DIVIDEND_DATE_INVALID")
        record_date = _iso_date(event.get("recordDate"), "FIU_SSE_DIVIDEND_DATE_INVALID")
        if not start <= ex_date <= end or not start <= record_date <= end:
            _fail("FIU_SSE_DIVIDEND_DATE_OUT_OF_RANGE")
        plan = event.get("plan")
        if type(plan) is not str or not plan:
            _fail("FIU_SSE_DIVIDEND_PLAN_INVALID")
        rate = event.get("dividendPaidRate")
        if rate is None:
            events.append({"symbol": symbol, "event_type": "dividend", "ex_date": ex_date, "record_date": record_date, "rate_status": "unparsed", "plan": plan, "rate_unit": "unknown"})
            continue
        parsed_count += 1
        events.append({"symbol": symbol, "event_type": "dividend", "ex_date": ex_date, "record_date": record_date, "rate": _decimal_string(rate, "FIU_SSE_DIVIDEND_RATE_INVALID"), "rate_status": "provider_reported", "plan": plan, "rate_unit": "unknown"})
    if not parsed_count:
        _fail("FIU_SSE_DIVIDEND_RATE_MISSING")
    return {"parser_id": FIU_SSE_DIVIDENDS_V1, "symbol": symbol, "events": events, "coverage": "partial" if parsed_count != len(events) else "complete_for_observed_rows"}


def _parse_hangseng_hk_forward_range_summary_v1(raw: Any, expected: dict[str, str]) -> dict[str, Any]:
    """Parse one observed forward-adjusted range summary, never daily bars."""
    symbol, start, end = expected["symbol"], expected["start_date"], expected["end_date"]
    try:
        rows = raw["data"]["data"]["rows"]
    except (KeyError, TypeError):
        _fail("HANGSENG_HK_RANGE_SHAPE_INVALID")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        _fail("HANGSENG_HK_RANGE_SHAPE_INVALID")
    row = rows[0]
    if row.get("stockcode") != symbol.removesuffix(".HK") or row.get("begindate") != start or row.get("enddate") != end or row.get("restorationstatus") != "前复权":
        _fail("HANGSENG_HK_RANGE_IDENTITY_OR_BASIS_INVALID")
    values = {name: _decimal_string(row.get(source), "HANGSENG_HK_RANGE_NUMBER_INVALID") for name, source in (("open", "openpricebt"), ("high", "highpricebt"), ("low", "lowpricebt"), ("close", "closepricebt"), ("volume", "turnovervolumebt"), ("turnover", "turnovervaluebt"))}
    if Decimal(values["volume"]) < 0 or Decimal(values["turnover"]) < 0:
        _fail("HANGSENG_HK_RANGE_NUMBER_INVALID")
    _quote_ohlc({key: Decimal(values[key]) for key in ("open", "high", "low", "close")}, "HANGSENG_HK_RANGE")
    return {"parser_id": HANGSENG_HK_FORWARD_RANGE_SUMMARY_V1, "symbol": symbol, "start_date": start, "end_date": end, "adjustment_basis": "forward", "summary": values, "currency": row.get("currency"), "amount_unit": "unknown", "bar_granularity": "range_summary"}


def render_gildata_bonusstock_query(symbol: str, start_date: str, end_date: str) -> str:
    """Render the sole admitted Gildata company-actions query shape."""
    if _A_SHARE_SYMBOL.fullmatch(symbol) is None or not symbol.endswith(".SZ"):
        _fail("GILDATA_BONUSSTOCK_SYMBOL_INVALID")
    start = _iso_date(start_date, "GILDATA_BONUSSTOCK_DATE_INVALID")
    end = _iso_date(end_date, "GILDATA_BONUSSTOCK_DATE_INVALID")
    if start > end:
        _fail("GILDATA_BONUSSTOCK_DATE_RANGE_INVALID")
    return f"查询深交所股票 {symbol} 在 {start} 至 {end} 期间的分红送配记录，包含现金分红、送股、转增和配股。"


def _parse_cnfp_hkex_trading_calendar_v1(raw: Any, expected_calendar: dict[str, str]) -> dict[str, Any]:
    """Project only the observed HKEX trading-date list, without calendar inference."""
    raw = _structured(raw, "CNFP_HKEX_CALENDAR")
    if type(raw) is not dict or type(raw.get("time")) is not list or not raw["time"] or type(raw.get("metadata")) is not dict:
        _fail("CNFP_HKEX_CALENDAR_SHAPE_INVALID")
    metadata = raw["metadata"]
    if (
        metadata.get("marketcode") != expected_calendar["marketcode"]
        or metadata.get("date_type") != expected_calendar["date_type"]
        or metadata.get("has_results") is not True
    ):
        _fail("CNFP_HKEX_CALENDAR_METADATA_MISMATCH")
    dates = [_iso_date(value, "CNFP_HKEX_CALENDAR_DATE_INVALID") for value in raw["time"]]
    return {
        "parser_id": CNFP_HKEX_TRADING_CALENDAR_V1,
        "trading_dates": list(dict.fromkeys(dates)),
        "metadata": {
            "marketcode": metadata["marketcode"],
            "date_type": metadata["date_type"],
            "has_results": metadata["has_results"],
        },
    }


_PARSERS: dict[str, Callable[[Any, Any], dict[str, Any]]] = {
    EODHD_QUOTE_CSV_V1: _parse_eodhd_quote_csv_v1,
    CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1: _parse_caidazi_a_share_quote_markdown_v1,
    FIU_HK_CASH_FLOW_ANNUAL_V2: _parse_fiu_hk_cash_flow_annual_v2,
    FIU_HK_INCOME_ANNUAL_V2: _parse_fiu_hk_income_annual_v2,
    FIU_HK_INCOME_ANNUAL_V3: _parse_fiu_hk_income_annual_v3,
    FIU_US_QUOTE_SNAPSHOT_V1: _parse_fiu_us_quote_snapshot_v1,
    ALPHAVANTAGE_GLOBAL_QUOTE_V1: _parse_alphavantage_global_quote_v1,
    ALPHAVANTAGE_TIME_SERIES_DAILY_V1: _parse_alphavantage_time_series_daily_v1,
    FMP_EOD_V1: _parse_fmp_eod_v1,
    HANGSENG_A_SHARE_QUOTE_V1: _parse_hangseng_a_share_quote_v1,
    ALPHAVANTAGE_BULK_BID_ASK_V1: _parse_alphavantage_bulk_bid_ask_v1,
    FIU_US_MULTI_QUOTE_V1: _parse_fiu_us_multi_quote_v1,
    QVERIS_AFTER_HOURS_V1: _parse_qveris_after_hours_v1,
    ALPHAVANTAGE_INTRADAY_BARS_V1: _parse_alphavantage_intraday_bars_v1,
    ALPHAVANTAGE_INCOME_STATEMENT_LIST_V1: _parse_alphavantage_income_statement_list_v1,
    ALPHAVANTAGE_BALANCE_SHEET_RETRIEVE_V1: _parse_alphavantage_balance_sheet_retrieve_v1,
    ALPHAVANTAGE_CASH_FLOW_RETRIEVE_V1: _parse_alphavantage_cash_flow_retrieve_v1,
    FMP_STANDARD_INCOME_STATEMENT_V1: _parse_fmp_standard_income_statement_v1,
    FMP_STANDARD_BALANCE_SHEET_V1: _parse_fmp_standard_balance_sheet_v1,
    FMP_STANDARD_CASH_FLOW_V1: _parse_fmp_standard_cash_flow_v1,
    FMP_AS_REPORTED_INCOME_V1: _parse_fmp_as_reported_income_v1,
    FIU_SSE_INCOME_STATEMENT_V1: _parse_fiu_sse_income_statement_v1,
    FIU_SSE_BALANCE_SHEET_V1: _parse_fiu_sse_balance_sheet_v1,
    FIU_SSE_CASH_FLOW_V1: _parse_fiu_sse_cash_flow_v1,
    CNFP_REALTIME_QUOTE_V1: _parse_cnfp_realtime_quote_v1,
    CNFP_INTRADAY_BARS_V1: _parse_cnfp_intraday_bars_v1,
    CNFP_ADJUSTMENT_FACTOR_V1: _parse_cnfp_adjustment_factor_v1,
    CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1: _parse_caidazi_a_share_quote_envelope_v1,
    CNFP_FINANCIAL_ROW_V1: _parse_cnfp_financial_row_v1,
    HANGSENG_HK_BATCH_QUOTE_V1: _parse_hangseng_hk_batch_quote_v1,
    HANGSENG_HK_L1_V1: _parse_hangseng_hk_l1_v1,
    FIU_SSE_DIVIDEND_SCHEMA_V1: _parse_fiu_sse_dividend_schema_v1,
    FIU_SSE_DIVIDENDS_V1: _parse_fiu_sse_dividends_v1,
    HANGSENG_HK_FORWARD_RANGE_SUMMARY_V1: _parse_hangseng_hk_forward_range_summary_v1,
    CNFP_HKEX_TRADING_CALENDAR_V1: _parse_cnfp_hkex_trading_calendar_v1,
}
_PARSER_IDENTITY_KEYS = {
    EODHD_QUOTE_CSV_V1: "ticker",
    CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1: "symbol",
    FIU_HK_CASH_FLOW_ANNUAL_V2: "symbol",
    FIU_HK_INCOME_ANNUAL_V2: "symbol",
    FIU_HK_INCOME_ANNUAL_V3: "symbol",
    FIU_US_QUOTE_SNAPSHOT_V1: "symbols",
    ALPHAVANTAGE_GLOBAL_QUOTE_V1: "symbol",
    ALPHAVANTAGE_TIME_SERIES_DAILY_V1: "symbol",
    FMP_EOD_V1: "symbol",
    HANGSENG_A_SHARE_QUOTE_V1: "stockObject",
    ALPHAVANTAGE_BULK_BID_ASK_V1: "symbols",
    FIU_US_MULTI_QUOTE_V1: "symbols",
    QVERIS_AFTER_HOURS_V1: "symbol",
    ALPHAVANTAGE_INTRADAY_BARS_V1: "symbol",
    ALPHAVANTAGE_INCOME_STATEMENT_LIST_V1: "symbol",
    ALPHAVANTAGE_BALANCE_SHEET_RETRIEVE_V1: "symbol",
    ALPHAVANTAGE_CASH_FLOW_RETRIEVE_V1: "symbol",
    FMP_STANDARD_INCOME_STATEMENT_V1: "symbol",
    FMP_STANDARD_BALANCE_SHEET_V1: "symbol",
    FMP_STANDARD_CASH_FLOW_V1: "symbol",
    FMP_AS_REPORTED_INCOME_V1: "symbol",
    FIU_SSE_INCOME_STATEMENT_V1: "symbol",
    FIU_SSE_BALANCE_SHEET_V1: "symbol",
    FIU_SSE_CASH_FLOW_V1: "symbol",
    CNFP_REALTIME_QUOTE_V1: "thscode",
    CNFP_INTRADAY_BARS_V1: "thscode",
    CNFP_ADJUSTMENT_FACTOR_V1: "stock_code",
    CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1: "symbol",
    CNFP_FINANCIAL_ROW_V1: "thscode",
    HANGSENG_HK_BATCH_QUOTE_V1: "stockObject",
    HANGSENG_HK_L1_V1: "symbol",
    FIU_SSE_DIVIDEND_SCHEMA_V1: "symbol",
    FIU_SSE_DIVIDENDS_V1: "dividend_request",
    HANGSENG_HK_FORWARD_RANGE_SUMMARY_V1: "range_request",
    CNFP_HKEX_TRADING_CALENDAR_V1: "calendar",
}


def is_supported_provider_payload_parser(parser_id: Any) -> bool:
    """Return whether parser_id names one of the fixed, audited parsers."""
    return type(parser_id) is str and parser_id in _PARSERS


def provider_payload_identity_key(parser_id: str) -> str:
    """Return the fixed request key that binds an approved parser's identity."""
    if not is_supported_provider_payload_parser(parser_id):
        _fail("PROVIDER_PAYLOAD_PARSER_UNSUPPORTED")
    return _PARSER_IDENTITY_KEYS[parser_id]


def validate_provider_payload_identity(parser_id: str, value: Any) -> Any:
    """Validate a parser-bound request identity before any paid execution."""
    provider_payload_identity_key(parser_id)
    if parser_id == FIU_SSE_DIVIDENDS_V1:
        if type(value) is not dict or set(value) != {"symbol", "start_date", "end_date"}:
            _fail("FIU_SSE_DIVIDEND_REQUEST_INVALID")
        symbol = value["symbol"]
        if type(symbol) is not str or _A_SHARE_SYMBOL.fullmatch(symbol) is None or not symbol.endswith(".SH"):
            _fail("FIU_SSE_DIVIDEND_SYMBOL_INVALID")
        start = _iso_date(value["start_date"], "FIU_SSE_DIVIDEND_DATE_INVALID")
        end = _iso_date(value["end_date"], "FIU_SSE_DIVIDEND_DATE_INVALID")
        if start > end:
            _fail("FIU_SSE_DIVIDEND_DATE_RANGE_INVALID")
        return {"symbol": symbol, "start_date": start, "end_date": end}
    if parser_id == HANGSENG_HK_FORWARD_RANGE_SUMMARY_V1:
        if type(value) is not dict or set(value) != {"symbol", "start_date", "end_date"} or value.get("symbol") != "00700.HK":
            _fail("HANGSENG_HK_RANGE_REQUEST_INVALID")
        start = _iso_date(value["start_date"], "HANGSENG_HK_RANGE_DATE_INVALID")
        end = _iso_date(value["end_date"], "HANGSENG_HK_RANGE_DATE_INVALID")
        if start > end:
            _fail("HANGSENG_HK_RANGE_DATE_RANGE_INVALID")
        return {"symbol": "00700.HK", "start_date": start, "end_date": end}
    if parser_id == FIU_US_QUOTE_SNAPSHOT_V1:
        if type(value) is list and len(value) == 1:
            value = value[0]
        if type(value) is not str or _FIU_US_SYMBOL.fullmatch(value) is None:
            _fail("FIU_US_SYMBOL_FORMAT_INVALID")
        return value
    if parser_id == HANGSENG_A_SHARE_QUOTE_V1:
        if type(value) is list and len(value) == 1:
            value = value[0]
        if type(value) is not str or _CAIDAZI_NAME.fullmatch(value) is None:
            _fail("HANGSENG_A_SHARE_IDENTITY_INVALID")
        return value
    if parser_id == ALPHAVANTAGE_BULK_BID_ASK_V1:
        return _expected_symbols(value, _US_SYMBOL, "ALPHAVANTAGE_BULK_SYMBOL_INVALID")
    if parser_id == FIU_US_MULTI_QUOTE_V1:
        return _expected_symbols(value, _FIU_US_SYMBOL, "FIU_US_MULTI_SYMBOL_INVALID")
    if parser_id == CNFP_REALTIME_QUOTE_V1:
        return _expected_symbols(value, _A_SHARE_SYMBOL, "CNFP_QUOTE_SYMBOL_INVALID")
    if parser_id == HANGSENG_HK_BATCH_QUOTE_V1:
        return _expected_symbols(value, _CAIDAZI_NAME, "HANGSENG_HK_SYMBOL_INVALID")
    if parser_id == CNFP_HKEX_TRADING_CALENDAR_V1:
        if type(value) is not dict or value.get("marketcode") != "212200" or value.get("date_type") != "0" or set(value) != {"marketcode", "date_type"}:
            _fail("CNFP_HKEX_CALENDAR_IDENTITY_INVALID")
        return {"marketcode": "212200", "date_type": "0"}
    if parser_id in {FIU_SSE_INCOME_STATEMENT_V1, FIU_SSE_BALANCE_SHEET_V1, FIU_SSE_CASH_FLOW_V1}:
        if type(value) is not str or _A_SHARE_SYMBOL.fullmatch(value) is None or not value.endswith(".SH"):
            _fail("FIU_SSE_SYMBOL_INVALID")
        return value
    pattern, code = {
        EODHD_QUOTE_CSV_V1: (_EODHD_SYMBOL, "EODHD_SYMBOL_FORMAT_INVALID"),
        CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1: (_A_SHARE_SYMBOL, "CAIDAZI_SYMBOL_FORMAT_INVALID"),
        FIU_HK_CASH_FLOW_ANNUAL_V2: (_HK_SYMBOL, "FIU_HK_SYMBOL_FORMAT_INVALID"),
        FIU_HK_INCOME_ANNUAL_V2: (_HK_SYMBOL, "FIU_HK_SYMBOL_FORMAT_INVALID"),
        FIU_HK_INCOME_ANNUAL_V3: (_HK_SYMBOL, "FIU_HK_SYMBOL_FORMAT_INVALID"),
        HANGSENG_HK_L1_V1: (_HK_SYMBOL, "HANGSENG_HK_L1_SYMBOL_INVALID"),
        FIU_SSE_DIVIDEND_SCHEMA_V1: (_A_SHARE_SYMBOL, "FIU_SSE_DIVIDEND_SYMBOL_INVALID"),
        ALPHAVANTAGE_GLOBAL_QUOTE_V1: (_US_SYMBOL, "ALPHAVANTAGE_SYMBOL_FORMAT_INVALID"),
        ALPHAVANTAGE_TIME_SERIES_DAILY_V1: (_US_SYMBOL, "ALPHAVANTAGE_SYMBOL_FORMAT_INVALID"),
        FMP_EOD_V1: (_US_SYMBOL, "FMP_EOD_SYMBOL_FORMAT_INVALID"),
        QVERIS_AFTER_HOURS_V1: (_US_SYMBOL, "AFTER_HOURS_SHAPE_INVALID"),
        ALPHAVANTAGE_INTRADAY_BARS_V1: (_US_SYMBOL, "ALPHAVANTAGE_SYMBOL_FORMAT_INVALID"),
        ALPHAVANTAGE_INCOME_STATEMENT_LIST_V1: (_US_SYMBOL, "ALPHAVANTAGE_SYMBOL_FORMAT_INVALID"),
        ALPHAVANTAGE_BALANCE_SHEET_RETRIEVE_V1: (_US_SYMBOL, "ALPHAVANTAGE_SYMBOL_FORMAT_INVALID"),
        ALPHAVANTAGE_CASH_FLOW_RETRIEVE_V1: (_US_SYMBOL, "ALPHAVANTAGE_SYMBOL_FORMAT_INVALID"),
        FMP_STANDARD_INCOME_STATEMENT_V1: (_US_SYMBOL, "FMP_STANDARD_SYMBOL_INVALID"),
        FMP_STANDARD_BALANCE_SHEET_V1: (_US_SYMBOL, "FMP_STANDARD_SYMBOL_INVALID"),
        FMP_STANDARD_CASH_FLOW_V1: (_US_SYMBOL, "FMP_STANDARD_SYMBOL_INVALID"),
        FMP_AS_REPORTED_INCOME_V1: (_US_SYMBOL, "FMP_AS_REPORTED_SYMBOL_INVALID"),
        FIU_SSE_INCOME_STATEMENT_V1: (_A_SHARE_SYMBOL, "FIU_SSE_SYMBOL_INVALID"),
        FIU_SSE_BALANCE_SHEET_V1: (_A_SHARE_SYMBOL, "FIU_SSE_SYMBOL_INVALID"),
        FIU_SSE_CASH_FLOW_V1: (_A_SHARE_SYMBOL, "FIU_SSE_SYMBOL_INVALID"),
        CNFP_INTRADAY_BARS_V1: (_A_SHARE_SYMBOL, "CNFP_INTRADAY_SHAPE_INVALID"),
        CNFP_ADJUSTMENT_FACTOR_V1: (_A_SHARE_SYMBOL, "CNFP_ADJUSTMENT_SHAPE_INVALID"),
        CAIDAZI_A_SHARE_QUOTE_ENVELOPE_V1: (_A_SHARE_SYMBOL, "CAIDAZI_SYMBOL_FORMAT_INVALID"),
        CNFP_FINANCIAL_ROW_V1: (_A_SHARE_SYMBOL, "CNFP_FINANCIAL_SHAPE_INVALID"),
    }[parser_id]
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def parse_provider_payload(
    parser_id: str,
    raw: Any,
    *,
    expected_symbol: Any,
    expected_fiscal_year: int | str | None = None,
    expected_statement_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse one approved payload format without schema guessing or fallback."""
    parser = _PARSERS.get(parser_id)
    if parser is None:
        _fail("PROVIDER_PAYLOAD_PARSER_UNSUPPORTED")
    symbol = validate_provider_payload_identity(parser_id, expected_symbol)
    if parser_id in {FIU_HK_CASH_FLOW_ANNUAL_V2, FIU_HK_INCOME_ANNUAL_V2, FIU_HK_INCOME_ANNUAL_V3}:
        return parser(raw, symbol, expected_fiscal_year)
    if parser_id in {
        FMP_STANDARD_INCOME_STATEMENT_V1,
        FMP_STANDARD_BALANCE_SHEET_V1,
        FMP_STANDARD_CASH_FLOW_V1,
        FMP_AS_REPORTED_INCOME_V1,
    }:
        return parser(raw, symbol, expected_statement_identity)
    return parser(raw, symbol)


_FMP_PERIOD_SELECTABLE_PARSERS = frozenset({
    FMP_STANDARD_INCOME_STATEMENT_V1,
    FMP_STANDARD_BALANCE_SHEET_V1,
    FMP_STANDARD_CASH_FLOW_V1,
    FMP_AS_REPORTED_INCOME_V1,
})


def parse_fmp_statement_for_period(
    parser_id: str,
    raw: Any,
    *,
    expected_symbol: str,
    fiscal_year: int | str,
    fiscal_period: str,
) -> dict[str, Any]:
    """Select one provider-reported FMP period, then parse its provider identity.

    Report date and currency deliberately come only from the selected provider
    row.  They are then passed back through the existing strict parser rather
    than being supplied by the semantic request.
    """
    if parser_id not in _FMP_PERIOD_SELECTABLE_PARSERS:
        _fail("FMP_PERIOD_SELECTION_PARSER_UNSUPPORTED")
    symbol = validate_provider_payload_identity(parser_id, expected_symbol)
    if (
        type(fiscal_year) not in (str, int)
        or isinstance(fiscal_year, bool)
        or re.fullmatch(r"[0-9]{4}", str(fiscal_year)) is None
        or type(fiscal_period) is not str
        or re.fullmatch(r"(?:Q[1-4]|FY)", fiscal_period) is None
        or (parser_id == FMP_AS_REPORTED_INCOME_V1 and fiscal_period == "FY")
    ):
        _fail("FMP_PERIOD_SELECTION_REQUEST_INVALID")
    prefix = "FMP_AS_REPORTED" if parser_id == FMP_AS_REPORTED_INCOME_V1 else "FMP_STANDARD"
    candidates = [
        row for row in _rows(_structured(raw, prefix), prefix)
        if row.get("symbol") == symbol
        and str(row.get("fiscalYear")) == str(fiscal_year)
        and row.get("period") == fiscal_period
    ]
    if not candidates:
        _fail("FMP_PERIOD_SELECTION_NO_DATA")
    if len(candidates) != 1:
        _fail("FMP_PERIOD_SELECTION_NOT_UNIQUE")
    row = candidates[0]
    return parse_provider_payload(
        parser_id,
        [row],
        expected_symbol=symbol,
        expected_statement_identity={
            "symbol": row.get("symbol"),
            "report_date": row.get("date"),
            "fiscal_year": row.get("fiscalYear"),
            "period": row.get("period"),
            "reported_currency": row.get("reportedCurrency"),
        },
    )

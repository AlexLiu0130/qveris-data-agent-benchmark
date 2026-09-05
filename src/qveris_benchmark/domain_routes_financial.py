"""Fixed, provider-free financial-statement route selection and projection.

This module deliberately has no catalog, oracle, network, or fallback import.
One plan selects one tool; projection uses only that tool's completed payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from .domain_route_contract import RoutePlan, RouteProjection
from .provider_payload import (
    FIU_HK_CASH_FLOW_ANNUAL_V2,
    FIU_HK_INCOME_ANNUAL_V2,
    FIU_SSE_BALANCE_SHEET_V1,
    FIU_SSE_CASH_FLOW_V1,
    FIU_SSE_INCOME_STATEMENT_V1,
    CNFP_FINANCIAL_ROW_V1,
    FMP_AS_REPORTED_INCOME_V1,
    FMP_STANDARD_BALANCE_SHEET_V1,
    FMP_STANDARD_CASH_FLOW_V1,
    FMP_STANDARD_INCOME_STATEMENT_V1,
    ProviderPayloadParseError,
    parse_fmp_statement_for_period,
    parse_provider_payload,
)


_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,31}\Z")
_FMP_GLOBAL_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,31}\Z")
_CN = re.compile(r"[0-9]{6}\.(?:SH|SZ)\Z")
_HK_SYMBOL = re.compile(r"[0-9]{5}\.HK\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_HK_FISCAL_YEAR = re.compile(r"(?:(?:FY)?([0-9]{4})|([0-9]{4})/FY)\Z")
_ALPHA_FISCAL_YEAR = re.compile(r"(?:FY)?([0-9]{4})\Z")
_ALPHA_PARSER = "alpha_financial_inline_v1"
_HK_BALANCE_PARSER = "fiu_hk_balance_sheet_v1"
_FMP_LATEST_PARSER = "fmp_latest_statement_v1"

_STATEMENTS = {"income": "income_statement", "balance": "balance_sheet", "cash_flow": "cash_flow"}
_ALPHA_FIELDS = {
    "income_statement": {"revenue": "totalRevenue", "net_income": "netIncome"},
    "balance_sheet": {"total_assets": "totalAssets", "total_liabilities": "totalLiabilities", "total_equity": "totalStockholdersEquity"},
    "cash_flow": {"net_cash_from_operating": "operatingCashflow", "net_cash_from_investing": "cashflowFromInvestment", "net_cash_from_financing": "cashflowFromFinancing", "net_increase_in_cash": "changeInCash", "cash_and_cash_equivalents_at_end": "cashAndCashEquivalentsAtEnd"},
}
_FMP_PARSERS = {
    "income_statement": FMP_STANDARD_INCOME_STATEMENT_V1,
    "balance_sheet": FMP_STANDARD_BALANCE_SHEET_V1,
    "cash_flow": FMP_STANDARD_CASH_FLOW_V1,
}
_FMP_TOOLS = {
    "income_statement": "financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f",
    "balance_sheet": "financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1",
    "cash_flow": "financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354",
}
_FMP_FIELDS = {
    "income_statement": frozenset({"revenue", "cost_of_revenue", "gross_profit", "research_and_development_expense", "selling_general_and_administrative_expense", "operating_income", "income_before_tax", "income_tax_expense"}),
    "balance_sheet": frozenset({"total_assets", "total_liabilities", "total_equity"}),
    "cash_flow": frozenset({"net_cash_from_operating", "net_cash_from_investing", "net_cash_from_financing", "net_increase_in_cash"}),
}
_FMP_SOURCES = {
    "income_statement": {"revenue": "revenue", "cost_of_revenue": "costOfRevenue", "gross_profit": "grossProfit", "research_and_development_expense": "researchAndDevelopmentExpenses", "selling_general_and_administrative_expense": "sellingGeneralAndAdministrativeExpenses", "operating_income": "operatingIncome", "income_before_tax": "incomeBeforeTax", "income_tax_expense": "incomeTaxExpense"},
    "balance_sheet": {"total_assets": "totalAssets", "total_liabilities": "totalLiabilities", "total_equity": "totalStockholdersEquity"},
    "cash_flow": {"net_cash_from_operating": "netCashProvidedByOperatingActivities", "net_cash_from_investing": "netCashProvidedByInvestingActivities", "net_cash_from_financing": "netCashProvidedByFinancingActivities", "net_increase_in_cash": "netChangeInCash"},
}
_ALPHA_TOOLS = {
    "income_statement": ("alphavantage.income_statement.retrieve.v1.7aca3c4a", "INCOME_STATEMENT"),
    "balance_sheet": ("alphavantage.balance_sheet.retrieve.v1.467a92c0", "BALANCE_SHEET"),
    "cash_flow": ("alphavantage.cash_flow.retrieve.v1.7aca3c4a", "CASH_FLOW"),
}
_SSE = {
    "income_statement": ("fiu_mcp_server.postapihsf10financeincome.create.v2.6f98cc58", FIU_SSE_INCOME_STATEMENT_V1),
    "balance_sheet": ("fiu_mcp_server.postapihsf10financebalance.create.v2.481102ad", FIU_SSE_BALANCE_SHEET_V1),
    "cash_flow": ("fiu_mcp_server.postapihsf10financecash.create.v2.93172fa6", FIU_SSE_CASH_FLOW_V1),
}
_SZSE_TOOLS = {
    "income_statement": "cn_financial_pro.income_statement.v1",
    "balance_sheet": "cn_financial_pro.balance_sheet.v1",
    "cash_flow": "cn_financial_pro.cash_flow_statement.v1",
}
_HK_TOOLS = {
    "income_statement": ("fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2", FIU_HK_INCOME_ANNUAL_V2),
    "balance_sheet": ("fiu_mcp_server.postapihkf10financebalance.create.v2.2c215b4b", _HK_BALANCE_PARSER),
    "cash_flow": ("fiu_mcp_server.postapihkf10financecash.create.v2.baf7f651", FIU_HK_CASH_FLOW_ANNUAL_V2),
}

# This is intentionally a capability declaration rather than a runtime catalog.
SUPPORTED_KEYS = MappingProxyType({
    ("US", "financial.income_statement.standard.specified_period.v1"): ("alphavantage.income_statement.retrieve.v1.7aca3c4a", _FMP_TOOLS["income_statement"]),
    ("US", "financial.balance_sheet.standard.specified_period.v1"): (_ALPHA_TOOLS["balance_sheet"][0], _FMP_TOOLS["balance_sheet"]),
    ("US", "financial.cash_flow.standard.specified_period.v1"): (_ALPHA_TOOLS["cash_flow"][0], _FMP_TOOLS["cash_flow"]),
    ("US", "financial.direct_line_items.specified_period.v1"): tuple(tool_id for tool_id, _ in _ALPHA_TOOLS.values()) + tuple(_FMP_TOOLS.values()),
    ("US", "financial.income_statement.as_reported.specified_period.v1"): ("financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47",),
    ("SSE", "financial.income_statement.standard.specified_period.v1"): (_SSE["income_statement"][0],),
    ("SSE", "financial.balance_sheet.standard.specified_period.v1"): (_SSE["balance_sheet"][0],),
    ("SSE", "financial.cash_flow.standard.specified_period.v1"): (_SSE["cash_flow"][0],),
    ("SSE", "financial.direct_line_items.specified_period.v1"): tuple(tool_id for tool_id, _ in _SSE.values()),
    ("SZSE", "financial.income_statement.standard.specified_period.v1"): (_SZSE_TOOLS["income_statement"],),
    ("SZSE", "financial.balance_sheet.standard.specified_period.v1"): (_SZSE_TOOLS["balance_sheet"],),
    ("SZSE", "financial.cash_flow.standard.specified_period.v1"): (_SZSE_TOOLS["cash_flow"],),
    ("SZSE", "financial.direct_line_items.specified_period.v1"): tuple(_SZSE_TOOLS.values()),
    ("HKEX", "financial.income_statement.standard.specified_period.v1"): (_HK_TOOLS["income_statement"][0],),
    ("HKEX", "financial.balance_sheet.standard.specified_period.v1"): (_HK_TOOLS["balance_sheet"][0],),
    ("HKEX", "financial.cash_flow.standard.specified_period.v1"): (_HK_TOOLS["cash_flow"][0],),
    ("HKEX", "financial.direct_line_items.specified_period.v1"): tuple(tool_id for tool_id, _ in _HK_TOOLS.values()),
    ("JP", "financial.income_statement.standard.specified_period.v1"): (_FMP_TOOLS["income_statement"],),
    ("JP", "financial.balance_sheet.standard.specified_period.v1"): (_FMP_TOOLS["balance_sheet"],),
    ("JP", "financial.cash_flow.standard.specified_period.v1"): (_FMP_TOOLS["cash_flow"],),
    ("JP", "financial.direct_line_items.specified_period.v1"): tuple(_FMP_TOOLS.values()),
    ("GB", "financial.income_statement.standard.specified_period.v1"): (_FMP_TOOLS["income_statement"],),
    ("GB", "financial.balance_sheet.standard.specified_period.v1"): (_FMP_TOOLS["balance_sheet"],),
    ("GB", "financial.cash_flow.standard.specified_period.v1"): (_FMP_TOOLS["cash_flow"],),
    ("GB", "financial.direct_line_items.specified_period.v1"): tuple(_FMP_TOOLS.values()),
    ("DE", "financial.income_statement.standard.specified_period.v1"): (_FMP_TOOLS["income_statement"],),
    ("DE", "financial.balance_sheet.standard.specified_period.v1"): (_FMP_TOOLS["balance_sheet"],),
    ("DE", "financial.cash_flow.standard.specified_period.v1"): (_FMP_TOOLS["cash_flow"],),
    ("DE", "financial.direct_line_items.specified_period.v1"): tuple(_FMP_TOOLS.values()),
    ("US", "financial.latest_filed.direct_metric.v1"): tuple(_FMP_TOOLS.values()),
    ("JP", "financial.latest_filed.direct_metric.v1"): tuple(_FMP_TOOLS.values()),
    ("GB", "financial.latest_filed.direct_metric.v1"): tuple(_FMP_TOOLS.values()),
    ("DE", "financial.latest_filed.direct_metric.v1"): tuple(_FMP_TOOLS.values()),
})

# A pointer is not data.  Gateway ownership must provide a host allowlist and
# bounded fetch before `project` sees inline content; this helper never fetches.
ALPHA_POINTER_HOST_CANDIDATES = frozenset({"www.alphavantage.co"})
FMP_CANDIDATE_MARKETS = frozenset({"JP", "GB", "DE"})
# The semantic boundary must normalize these before route selection.  Domain
# routes intentionally receive canonical fields only and never retry aliases.
SEMANTIC_FIELD_ALIASES = MappingProxyType({
    "operating_cash_flow": "net_cash_from_operating",
    "investing_cash_flow": "net_cash_from_investing",
    "financing_cash_flow": "net_cash_from_financing",
})


def _request(value: Any) -> Mapping[str, Any] | None:
    if type(value) is not dict:
        return None
    if set(value) == {"schema_version", "request"} and value.get("schema_version") == "public-get.semantic/v1":
        value = value["request"]
    if type(value) is not dict or set(value) != {"kind", "security", "statement"} or value.get("kind") != "financial_statement":
        return None
    security, statement = value["security"], value["statement"]
    if type(security) is not dict or set(security) not in ({"asset_class", "venue", "symbol"}, {"asset_class", "venue", "local_code"}) or security.get("asset_class") != "equity":
        return None
    symbol = security.get("symbol", security.get("local_code"))
    if type(statement) is not dict or set(statement) != {"type", "presentation", "period", "fields"} or statement.get("type") not in _STATEMENTS or statement.get("presentation") not in {"standardized", "as_reported"}:
        return None
    period, fields = statement["period"], statement["fields"]
    if type(period) is not dict or type(fields) is not list or not fields or len(fields) != len(set(fields)) or not all(type(field) is str and field for field in fields):
        return None
    if set(period) == {"kind", "fiscal_year", "fiscal_period"} and period.get("kind") == "specified_period" and type(period.get("fiscal_year")) is int and 1900 <= period["fiscal_year"] <= 9999 and period.get("fiscal_period") in {"FY", "Q1", "Q2", "Q3", "Q4"}:
        fiscal_year, fiscal_period, latest_basis, frequency = period["fiscal_year"], period["fiscal_period"], None, None
    elif set(period) == {"kind", "basis", "frequency"} and period.get("kind") == "latest" and period.get("basis") in {"filed", "report"} and period.get("frequency") in {"annual", "quarter"}:
        fiscal_year, fiscal_period, latest_basis, frequency = None, None, period["basis"], period["frequency"]
    else:
        return None
    venue = security.get("venue")
    if venue == "US" and type(symbol) is str and _TICKER.fullmatch(symbol):
        pass
    elif venue in FMP_CANDIDATE_MARKETS and type(symbol) is str and _FMP_GLOBAL_TICKER.fullmatch(symbol):
        pass
    elif venue == "SSE" and type(symbol) is str and (_CN.fullmatch(symbol) or re.fullmatch(r"[0-9]{6}", symbol)):
        symbol = symbol if symbol.endswith(".SH") else symbol + ".SH"
    elif venue == "SZSE" and type(symbol) is str and (_CN.fullmatch(symbol) or re.fullmatch(r"[0-9]{6}", symbol)):
        symbol = symbol if symbol.endswith(".SZ") else symbol + ".SZ"
    elif venue == "HKEX" and type(symbol) is str and (_HK_SYMBOL.fullmatch(symbol) or re.fullmatch(r"[0-9]{5}", symbol)):
        symbol = symbol if symbol.endswith(".HK") else symbol + ".HK"
    else:
        return None
    return {"venue": venue, "symbol": symbol, "statement_type": _STATEMENTS[statement["type"]], "presentation": statement["presentation"], "fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "latest_basis": latest_basis, "frequency": frequency, "fields": tuple(fields)}


def _plan(*, tool_id: str, params: Mapping[str, Any], parser_id: str, source: str, request: Mapping[str, Any], variant: str) -> RoutePlan:
    return RoutePlan(tool_id, dict(params), parser_id, "financial_statements", variant, source, dict(request))


def resolve(semantic: Mapping[str, Any]) -> RoutePlan | None:
    """Resolve the default fixed route.  US standardized statements use Alpha."""
    request = _request(semantic)
    if request is None:
        return None
    venue, statement_type, presentation = request["venue"], request["statement_type"], request["presentation"]
    if request["latest_basis"] is not None:
        if venue not in {"US", *FMP_CANDIDATE_MARKETS} or presentation != "standardized" or not set(request["fields"]).issubset(_FMP_FIELDS[statement_type]):
            return None
        return _plan(tool_id=_FMP_TOOLS[statement_type], params={"symbol": request["symbol"], "period": request["frequency"], "limit": 5}, parser_id=_FMP_LATEST_PARSER, source="Financial Modeling Prep", request=request, variant="financial-fmp-latest-%s-v1" % request["latest_basis"])
    if venue == "US" and presentation == "standardized":
        if request["fiscal_period"] == "FY" and set(request["fields"]).issubset(_ALPHA_FIELDS[statement_type]):
            tool_id, function = _ALPHA_TOOLS[statement_type]
            return _plan(tool_id=tool_id, params={"function": function, "symbol": request["symbol"]}, parser_id=_ALPHA_PARSER, source="Alpha Vantage", request=request, variant="financial-alpha-standard-v1")
        if set(request["fields"]).issubset(_FMP_FIELDS[statement_type]):
            return resolve_fmp(semantic)
    if venue == "US" and presentation == "as_reported" and statement_type == "income_statement" and request["fiscal_period"] != "FY":
        return _plan(tool_id="financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47", params={"symbol": request["symbol"], "period": "quarter", "limit": 1}, parser_id=FMP_AS_REPORTED_INCOME_V1, source="Financial Modeling Prep", request=request, variant="financial-fmp-as-reported-v1")
    if venue in FMP_CANDIDATE_MARKETS and presentation == "standardized" and request["fiscal_period"] == "FY" and set(request["fields"]).issubset(_FMP_FIELDS[statement_type]):
        return resolve_fmp(semantic)
    if presentation != "standardized" or request["fiscal_period"] != "FY":
        return None
    if venue == "SSE":
        tool_id, parser_id = _SSE[statement_type]
        return _plan(tool_id=tool_id, params={"symbol": request["symbol"], "reportType": "12", "sort": "desc"}, parser_id=parser_id, source="FIU", request=request, variant="financial-fiu-sse-standard-v1")
    if venue == "SZSE":
        return _plan(tool_id=_SZSE_TOOLS[statement_type], params={"codes": request["symbol"], "year": str(request["fiscal_year"]), "period": "1231", "type": "1"}, parser_id=CNFP_FINANCIAL_ROW_V1, source="CN Financial Pro", request=request, variant="financial-cnfp-szse-standard-v1")
    if venue == "HKEX" and statement_type in _HK_TOOLS:
        tool_id, parser_id = _HK_TOOLS[statement_type]
        ending = "%04d-12-31" % request["fiscal_year"]
        return _plan(tool_id=tool_id, params={"symbol": request["symbol"], "startDate": ending, "endDate": ending, "reportType": "F", "sort": "asc"}, parser_id=parser_id, source="FIU", request=request, variant="financial-fiu-hk-standard-v1")
    return None


def resolve_fmp(semantic: Mapping[str, Any]) -> RoutePlan | None:
    """Explicit FMP development route; callers must opt in, never fall back."""
    request = _request(semantic)
    if request is None or request["latest_basis"] is not None or request["venue"] not in {"US", *FMP_CANDIDATE_MARKETS} or request["presentation"] != "standardized" or request["venue"] in FMP_CANDIDATE_MARKETS and request["fiscal_period"] != "FY":
        return None
    statement_type = request["statement_type"]
    variant = "financial-fmp-standard-v1" if request["venue"] == "US" else "financial-fmp-global-income-v1"
    return _plan(tool_id=_FMP_TOOLS[statement_type], params={"symbol": request["symbol"], "period": "annual" if request["fiscal_period"] == "FY" else "quarter", "limit": 1 if request["venue"] == "US" else 5}, parser_id=_FMP_PARSERS[statement_type], source="Financial Modeling Prep", request=request, variant=variant)


def alpha_content_pointer(raw: Any) -> str | None:
    """Return a syntactically safe, still-untrusted Alpha content pointer."""
    try:
        result = raw["result"]
        value = result.get("full_content_file_url", result.get("data", {}).get("full_content_file_url"))
        parsed = urlsplit(value)
    except (KeyError, TypeError, ValueError):
        return None
    if type(value) is not str or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port is not None or parsed.fragment:
        return None
    return value


def _decimal(value: Any) -> str | None:
    if isinstance(value, bool) or type(value) not in (str, int, float):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return format(number, "f") if number.is_finite() else None


def _date(value: Any) -> str | None:
    if type(value) is not str:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _projection(request: Mapping[str, Any], metrics: Mapping[str, Any], *, report_date: str | None, fiscal_year: Any, fiscal_period: Any, currency: Any, unit: Any = "unknown") -> RouteProjection:
    actual_year, actual_period = str(fiscal_year), fiscal_period
    period_ok = actual_year == str(request["fiscal_year"]) and actual_period == request["fiscal_period"]
    available = {} if not period_ok else {field: metrics[field] for field in request["fields"] if metrics.get(field) is not None}
    missing = tuple(field for field in request["fields"] if field not in available)
    status = "success" if not missing else "partial"
    safe_currency = currency if type(currency) is str and _CURRENCY.fullmatch(currency) else "unknown"
    period = "%s%s" % (actual_period, actual_year)
    fact_unit = unit if type(unit) is str and unit else "unknown"
    facts = {
        field: {"value": value, "period": period, "currency": safe_currency, "unit": fact_unit, "nil": False}
        for field, value in available.items()
    }
    known_as_of = report_date if facts and report_date else None
    return RouteProjection({"kind": "financial_statement", "instrument": {"symbol": request["symbol"], "market": request["venue"]}, "statement_type": request["statement_type"], "presentation": request["presentation"], "facts": facts}, known_as_of, status, missing, "get-response/v2", "known" if known_as_of else "unavailable")


def _alpha(raw: Any, request: Mapping[str, Any]) -> RouteProjection:
    try:
        data = raw.get("result", {}).get("data", raw) if type(raw) is dict else None
        reports = data["annualReports"]
    except (KeyError, TypeError):
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    if type(data) is not dict or data.get("symbol") != request["symbol"] or type(reports) is not list:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    rows = [row for row in reports if type(row) is dict and _alpha_report_year(row) == str(request["fiscal_year"])]
    if len(rows) != 1:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    row = rows[0]
    metrics = {canonical: _decimal(row.get(source)) for canonical, source in _ALPHA_FIELDS[request["statement_type"]].items()}
    return _projection(request, metrics, report_date=_date(row.get("fiscalDateEnding")), fiscal_year=request["fiscal_year"], fiscal_period="FY", currency=row.get("reportedCurrency", row.get("currency")))


def _alpha_report_year(row: Mapping[str, Any]) -> str | None:
    """Return an Alpha annual report's provider-declared fiscal year.

    Alpha may label an issuer's fiscal year independently of its period-end
    date.  When supplied, that label is authoritative.  Otherwise an annual
    report's ISO period-end year is the only deterministic FY identity.
    """
    declared = row.get("fiscalYear")
    if type(declared) is int and 1000 <= declared <= 9999:
        return str(declared)
    if type(declared) is str:
        match = _ALPHA_FISCAL_YEAR.fullmatch(declared)
        if match:
            return match.group(1)
    ending = _date(row.get("fiscalDateEnding"))
    return ending[:4] if ending else None


def _hk_balance(raw: Any, request: Mapping[str, Any]) -> RouteProjection:
    rows = raw.get("data") if type(raw) is dict else raw
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    row = rows[0]
    fiscal = _HK_FISCAL_YEAR.fullmatch(row.get("fiscalYear", "")) if type(row.get("fiscalYear")) is str else None
    year = (fiscal.group(1) or fiscal.group(2)) if fiscal else ""
    report_date = _date(row.get("reportDate"))
    if row.get("symbol") != request["symbol"] or row.get("reportType") != "F" or report_date is None:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    metrics = {"total_assets": _decimal(row.get("totalAssets")), "total_liabilities": _decimal(row.get("totalLiabilities")), "total_equity": _decimal(row.get("totalEquity"))}
    return _projection(request, metrics, report_date=report_date, fiscal_year=year, fiscal_period="FY", currency=row.get("currency"))


def _fmp_latest(raw: Any, request: Mapping[str, Any]) -> RouteProjection:
    if type(raw) is not list:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    basis_field = "filingDate" if request["latest_basis"] == "filed" else "date"
    expected_period = "FY" if request["frequency"] == "annual" else None
    rows = []
    for row in raw:
        if type(row) is not dict or row.get("symbol") != request["symbol"]:
            continue
        fiscal_year, period, ranking_date = row.get("fiscalYear"), row.get("period"), _date(row.get(basis_field))
        if type(fiscal_year) not in (str, int) or not re.fullmatch(r"[0-9]{4}", str(fiscal_year)) or type(period) is not str or (expected_period is not None and period != expected_period) or expected_period is None and period not in {"Q1", "Q2", "Q3", "Q4"} or ranking_date is None:
            continue
        rows.append((ranking_date, row))
    if not rows:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    latest_date = max(item[0] for item in rows)
    selected = [row for ranking_date, row in rows if ranking_date == latest_date]
    if len(selected) != 1:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    row = selected[0]
    metrics = {canonical: _decimal(row.get(source)) for canonical, source in _FMP_SOURCES[request["statement_type"]].items()}
    report_date = _date(row.get("date"))
    currency = row.get("reportedCurrency")
    fiscal_year, fiscal_period = row["fiscalYear"], row["period"]
    projected = _projection(request | {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period}, metrics, report_date=latest_date, fiscal_year=fiscal_year, fiscal_period=fiscal_period, currency=currency)
    return projected


def _fmp_global(raw: Any, request: Mapping[str, Any]) -> RouteProjection:
    """Project a verified global FMP FY row without the US-only parser gate."""
    if type(raw) is not list:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    candidates = []
    for row in raw:
        if type(row) is not dict or row.get("symbol") != request["symbol"]:
            continue
        fiscal_year, period = row.get("fiscalYear"), row.get("period")
        if (
            type(fiscal_year) is str
            and re.fullmatch(r"[0-9]{4}", fiscal_year)
            and fiscal_year == str(request["fiscal_year"])
            and period == request["fiscal_period"]
            and _date(row.get("date")) is not None
            and type(row.get("reportedCurrency")) is str
            and _CURRENCY.fullmatch(row["reportedCurrency"])
        ):
            candidates.append(row)
    if len(candidates) != 1:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    row = candidates[0]
    metrics = {canonical: _decimal(row.get(source)) for canonical, source in _FMP_SOURCES[request["statement_type"]].items()}
    return _projection(
        request,
        metrics,
        report_date=_date(row.get("date")),
        fiscal_year=row["fiscalYear"],
        fiscal_period=row["period"],
        currency=row["reportedCurrency"],
    )


def _parsed(plan: RoutePlan, raw: Any) -> Mapping[str, Any] | None:
    request = plan.context
    try:
        if plan.parser_id in _FMP_PARSERS.values() or plan.parser_id == FMP_AS_REPORTED_INCOME_V1:
            return parse_fmp_statement_for_period(plan.parser_id, raw, expected_symbol=request["symbol"], fiscal_year=request["fiscal_year"], fiscal_period=request["fiscal_period"])
        if plan.parser_id in {FIU_SSE_INCOME_STATEMENT_V1, FIU_SSE_BALANCE_SHEET_V1, FIU_SSE_CASH_FLOW_V1}:
            rows = raw.get("data") if type(raw) is dict else None
            if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict or type(rows[0].get("reportType")) is not int or isinstance(rows[0]["reportType"], bool) or rows[0]["reportType"] != 12:
                return None
        if plan.parser_id in {FIU_HK_CASH_FLOW_ANNUAL_V2, FIU_HK_INCOME_ANNUAL_V2} and type(raw) is dict:
            raw = raw.get("data")
        expected_year = request["fiscal_year"] if plan.parser_id in {FIU_HK_CASH_FLOW_ANNUAL_V2, FIU_HK_INCOME_ANNUAL_V2} else None
        return parse_provider_payload(plan.parser_id, raw, expected_symbol=request["symbol"], expected_fiscal_year=expected_year)
    except ProviderPayloadParseError:
        return None


def project(plan: RoutePlan, raw: Any) -> RouteProjection:
    """Project one completed route payload; malformed, wrong-period data is partial."""
    request = plan.context
    if plan.suite != "financial_statements" or not isinstance(request, Mapping):
        raise ValueError("financial route plan is invalid")
    if plan.parser_id == _ALPHA_PARSER:
        return _alpha(raw, request)
    if plan.parser_id == _HK_BALANCE_PARSER:
        return _hk_balance(raw, request)
    if plan.parser_id == _FMP_LATEST_PARSER:
        return _fmp_latest(raw, request)
    if request.get("venue") in FMP_CANDIDATE_MARKETS and plan.parser_id in _FMP_PARSERS.values():
        return _fmp_global(raw, request)
    parsed = _parsed(plan, raw)
    if parsed is None:
        return _projection(request, {}, report_date=None, fiscal_year="", fiscal_period="", currency=None)
    fiscal_year = parsed.get("fiscal_year")
    fiscal_period = parsed.get("period")
    report_date = parsed.get("report_date")
    if plan.parser_id in {FIU_HK_CASH_FLOW_ANNUAL_V2, FIU_HK_INCOME_ANNUAL_V2}:
        fiscal_period = "FY"
    if plan.parser_id in {FIU_SSE_INCOME_STATEMENT_V1, FIU_SSE_BALANCE_SHEET_V1, FIU_SSE_CASH_FLOW_V1, CNFP_FINANCIAL_ROW_V1}:
        report_date = report_date or parsed.get("period")
        fiscal_year, fiscal_period = (str(report_date)[:4], "FY") if type(report_date) is str else ("", "")
    return _projection(request, parsed.get("metrics", parsed), report_date=report_date, fiscal_year=fiscal_year, fiscal_period=fiscal_period, currency=parsed.get("reported_currency", parsed.get("currency")), unit=parsed.get("unit", parsed.get("amount_unit", "unknown")))


__all__ = ["ALPHA_POINTER_HOST_CANDIDATES", "FMP_CANDIDATE_MARKETS", "SEMANTIC_FIELD_ALIASES", "SUPPORTED_KEYS", "alpha_content_pointer", "project", "resolve", "resolve_fmp"]

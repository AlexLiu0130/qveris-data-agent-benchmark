"""Static public-get admission catalog; it never reads the evidence registry at runtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal

from .domain_routes_financial import SUPPORTED_KEYS as _FINANCIAL_ROUTE_TOOLS
from .domain_routes_historical import SUPPORTED_KEYS as _HISTORICAL_ROUTE_TOOLS
from .domain_routes_realtime import SUPPORTED_KEYS as _REALTIME_ROUTE_TOOLS


CatalogDisposition = Literal[
    "dispatchable", "unsupported", "not_applicable", "rejected", "unverified", "gap"
]
RegistryState = Literal[
    "provisional_basic", "gap", "rejected", "unverified", "not_applicable", "blocked"
]


@dataclass(frozen=True)
class RuntimeCatalogEntry:
    market: str
    scenario: str
    registry_state: RegistryState
    disposition: CatalogDisposition
    tool_ids: tuple[str, ...]
    processors: tuple[str, ...]
    reason: str
    evidence: str | None


_ENTRIES = (
    RuntimeCatalogEntry(
        market='HKEX', scenario='financial.balance_sheet.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihkf10financebalance.create.v2.2c215b4b',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='financial.cash_flow.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihkf10financecash.create.v2.baf7f651',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; mapped cash-flow fields returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='financial.direct_line_items.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2',), processors=('deterministic_requested_fields_projection',),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; requested income fields project from the validated 13-field summary',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='financial.income_statement.as_reported.specified_period.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='standard FIU statement is not as-reported evidence', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='financial.income_statement.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; 13 user-required summary fields mapped',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='financial.latest_filed.direct_metric.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='latest filing ordering/provenance not established', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.adjustment_factors.v1', registry_state='unverified',
        disposition='unverified', tool_ids=(), processors=(),
        reason='no observed HKEX adjustment-factor contract', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.corporate_actions.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihkf10summarycadividends.create.v2.d3fe48e2',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; dividend-event payload returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.daily_bars.adjusted.v1', registry_state='unverified',
        disposition='unverified', tool_ids=('hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4',), processors=('hangseng_hk_forward_range_summary_v1',),
        reason='response is a single forward-adjusted range summary, not dated daily OHLCV bars', evidence='HTTP 200; 00700.HK forward-adjusted range-summary parser passed for one requested window',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.daily_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; identity/date/core OHLCV returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.intraday_bars.adjusted.v1', registry_state='rejected',
        disposition='rejected', tool_ids=('fiu_mcp_server.postv1chartklinelist.create.v2.41a84fef',), processors=(),
        reason='exact exploratory input did not return usable provider data', evidence='HTTP 200 but outer success=false for the fixed 00700.HK candleMode=1 exploratory input; no business data',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.intraday_bars.unadjusted.v1', registry_state='rejected',
        disposition='rejected', tool_ids=('fiu_mcp_server.postv1chartklinelist.create.v2.41a84fef',), processors=(),
        reason='exact exploratory input did not return usable provider data', evidence='HTTP 200 but outer success=false for the fixed 00700.HK candleMode=0 exploratory input; no business data',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='historical.trading_calendar.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.trade_dates.v1',), processors=('cnfp_hkex_trading_calendar_v1',),
        reason='fixed renderer/parser evidence exists, but the provider contract has no trustworthy source-data as_of for public output', evidence='business_success; four HKEX ISO trading dates returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.batch_quote_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; multi-symbol quote returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.bid_ask_l1.v1', registry_state='provisional_basic',
        disposition='dispatchable', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=('hangseng_hk_l1_v1',),
        reason='fixed renderer, parser, and postprocess contract admitted for runtime', evidence='HTTP 200; one 00700.HK receipt passed exact stockCode normalization, bid/ask, timestamp, currency, size and non-crossed-book gates',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.extended_hours_price.v1', registry_state='not_applicable',
        disposition='not_applicable', tool_ids=(), processors=(),
        reason='product taxonomy decision', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.last_price.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; latest price returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.latest_trade.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; trade fields returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.quote_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; identity and core quote fields returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.trading_status.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; status fields returned',
    ),
    RuntimeCatalogEntry(
        market='HKEX', scenario='realtime.equity.volume_turnover_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('hangseng_polysource.quote.hkshares.live.v2.dec427af',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; volume/turnover fields returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='financial.balance_sheet.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihsf10financebalance.create.v2.481102ad',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='financial.cash_flow.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihsf10financecash.create.v2.93172fa6',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='financial.direct_line_items.specified_period.v1', registry_state='gap',
        disposition='gap', tool_ids=('fiu_mcp_server.postapihsf10financeincome.create.v2.6f98cc58', 'fiu_mcp_server.postapihsf10financebalance.create.v2.481102ad', 'fiu_mcp_server.postapihsf10financecash.create.v2.93172fa6'), processors=('fiu_sse_balance_sheet_v1', 'fiu_sse_cash_flow_v1', 'fiu_sse_income_statement_v1'),
        reason='one selected FIU standard statement tool is projected into requested direct fields', evidence='three owner-only 600519.SH annual-category receipts: single-row envelope with symbol/reportDate/reportType/reportKind/currency and validated core income, balance, or cash-flow keys; unit remains unknown',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='financial.income_statement.as_reported.specified_period.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='standard statement is not as-reported evidence', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='financial.income_statement.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihsf10financeincome.create.v2.6f98cc58',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='financial.latest_filed.direct_metric.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='latest filing ordering/provenance not established', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.adjustment_factors.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.adjusted_price.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; adjusted-price basis returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.corporate_actions.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postapihsf10summarycadividends.create.v2.88186c04',), processors=('fiu_sse_dividends_v1',),
        reason='fixed renderer/parser evidence exists, but the provider contract has no trustworthy source-data as_of for public output', evidence='HTTP 200; two 600519.SH dividend events replayed with in-window dates, one finite provider rate and one explicitly unparsed null rate',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.daily_bars.adjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.adjusted_price.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; adjusted daily series returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.daily_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.history_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; raw daily series returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.intraday_bars.adjusted.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='no observed adjusted intraday contract', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.intraday_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.hf_basic_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; raw intraday rows returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='historical.trading_calendar.v1', registry_state='unverified',
        disposition='unverified', tool_ids=(), processors=(),
        reason='no exact observed SSE calendar contract', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.batch_quote_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; multi-code quote returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.bid_ask_l1.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('caidazi.get_real_time_record.execute.v1.7a43f96e',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; fixed parser extracted L1 bid/ask',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.extended_hours_price.v1', registry_state='not_applicable',
        disposition='not_applicable', tool_ids=(), processors=(),
        reason='product taxonomy decision for mainland A shares', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.last_price.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; latest price returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.latest_trade.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; quote trade-time fields returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.quote_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; structured quote returned',
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.trading_status.v1', registry_state='unverified',
        disposition='unverified', tool_ids=(), processors=(),
        reason='quote receipt does not yet establish trading-status semantics', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SSE', scenario='realtime.equity.volume_turnover_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; volume and turnover returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='financial.balance_sheet.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.balance_sheet.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='financial.cash_flow.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.cash_flow_statement.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='financial.direct_line_items.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.balance_sheet.v1', 'cn_financial_pro.cash_flow_statement.v1', 'cn_financial_pro.income_statement.v1'), processors=('deterministic_requested_fields_projection',),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; requested canonical fields project from one observed standard statement',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='financial.income_statement.as_reported.specified_period.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='standard statement is not as-reported evidence', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='financial.income_statement.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.income_statement.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; statement rows returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='financial.latest_filed.direct_metric.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='latest filing ordering/provenance not established', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.adjustment_factors.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.adjusted_price.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; adjusted-price basis returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.corporate_actions.v1', registry_state='rejected',
        disposition='rejected', tool_ids=('mcp_gildata.bonusstock.v1',), processors=(),
        reason='exact query contract did not return usable provider data', evidence='HTTP 200 but outer success=false for the fixed deterministic 000001.SZ query; no business data',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.daily_bars.adjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.adjusted_price.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; adjusted daily series returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.daily_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.history_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; raw daily series returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.intraday_bars.adjusted.v1', registry_state='unverified',
        disposition='unverified', tool_ids=(), processors=(),
        reason='no observed adjusted intraday contract', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.intraday_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.hf_basic_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; raw intraday rows returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='historical.trading_calendar.v1', registry_state='unverified',
        disposition='unverified', tool_ids=(), processors=(),
        reason='no exact observed SZSE calendar contract', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.batch_quote_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; multi-code quote returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.bid_ask_l1.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('caidazi.get_real_time_record.execute.v1.7a43f96e',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; raw replay parser extracted bid, ask, trade_time and halted',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.extended_hours_price.v1', registry_state='not_applicable',
        disposition='not_applicable', tool_ids=(), processors=(),
        reason='product taxonomy decision for mainland A shares', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.last_price.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; latest price returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.latest_trade.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; quote trade-time fields returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.quote_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; structured quote returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.trading_status.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; status fields returned',
    ),
    RuntimeCatalogEntry(
        market='SZSE', scenario='realtime.equity.volume_turnover_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('cn_financial_pro.real_time_quotation.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; volume and turnover returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='financial.balance_sheet.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; limited deterministic mapping replay',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='financial.cash_flow.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; deterministic mapping replay passed',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='financial.direct_line_items.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1', 'financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f'), processors=('deterministic_requested_fields_projection',),
        reason='offline-only replay requires a five-field filing identity; public semantic request cannot yet select one safely', evidence='one FMP income and one FMP balance-sheet receipt replayed through fixed same-statement mappers',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='financial.income_statement.as_reported.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47',), processors=('fmp_as_reported_income_v1',),
        reason='offline-only replay requires a five-field filing identity; public semantic request cannot yet select one safely', evidence='one TSLA quarterly as-reported receipt replayed; XBRL revenue and net-income tags passed identity, period, currency and finite-value gates',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='financial.income_statement.standard.specified_period.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; deterministic mapping replay passed',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='financial.latest_filed.direct_metric.v1', registry_state='gap',
        disposition='gap', tool_ids=(), processors=(),
        reason='latest filing ordering/filing provenance not established', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.adjustment_factors.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('tiingo.daily.ticker.prices.list.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; split factor returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.corporate_actions.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('tiingo.daily.ticker.prices.list.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; dividend and split fields returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.daily_bars.adjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('qveris_finance.mkt_bars_adjusted',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; adjusted OHLC and factor returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.daily_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('alphavantage.time-series.daily.v1',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; raw daily OHLCV returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.intraday_bars.adjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('alphavantage.time_series_intraday.retrieve.v1.1e18340d',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; adjusted intraday series returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.intraday_bars.unadjusted.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('alphavantage.time_series_intraday.retrieve.v1.1e18340d',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; intraday series returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='historical.trading_calendar.v1', registry_state='rejected',
        disposition='rejected', tool_ids=('theta_data.calendar.ondate.retrieve.v3.e44984f2',), processors=(),
        reason='returned data cannot establish the requested trading-calendar semantics', evidence='receipt did not satisfy the calendar contract',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.batch_quote_snapshot.v1', registry_state='rejected',
        disposition='rejected', tool_ids=('fiu_mcp_server.postv3stockquote.create.v2.a49a2677',), processors=(),
        reason='exact receipt has no usable batch quote snapshot', evidence='business_success envelope with two rows, but snapshot and trade are null and order is empty',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.bid_ask_l1.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('alphavantage.realtime_bulk_bid_ask_prices.retrieve.v1.9b8a7c6d',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; bid/ask rows returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.extended_hours_price.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('qveris_finance.mkt_after_hours',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; after-hours price returned',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.last_price.v1', registry_state='provisional_basic',
        disposition='dispatchable', tool_ids=('alphavantage.global_quote.retrieve.v1.9b8a7c6d',), processors=('alphavantage_global_quote_v1',),
        reason='fixed renderer, parser, and postprocess contract admitted for runtime', evidence='HTTP 200; business_success; core quote parser passed and returned price for one symbol',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.latest_trade.v1', registry_state='rejected',
        disposition='rejected', tool_ids=('fiu_mcp_server.postv3stockquote.create.v2.a49a2677',), processors=(),
        reason='exact receipt has no usable latest-trade data', evidence='business_success envelope with two rows, but snapshot and trade are null and order is empty',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.quote_snapshot.v1', registry_state='provisional_basic',
        disposition='dispatchable', tool_ids=('alphavantage.global_quote.retrieve.v1.9b8a7c6d',), processors=('alphavantage_global_quote_v1',),
        reason='fixed renderer, parser, and postprocess contract admitted for runtime', evidence='HTTP 200; business_success; core quote parser passed for one symbol',
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.trading_status.v1', registry_state='unverified',
        disposition='unverified', tool_ids=(), processors=(),
        reason='marketstatus receipt was semantically uncertain; do not infer a tradable session state', evidence=None,
    ),
    RuntimeCatalogEntry(
        market='US', scenario='realtime.equity.volume_turnover_snapshot.v1', registry_state='provisional_basic',
        disposition='unsupported', tool_ids=('fiu_mcp_server.postv1stockquote.create.v2.1790f84e',), processors=(),
        reason='tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen', evidence='business_success; snapshot turnover fields returned',
    ),
)

BASELINE_ENTRY_COUNT = 84

# These are explicit runtime outcomes for candidate capabilities outside the
# four-market frozen matrix.  They carry no inferred provider/tool contract.
_UNMAPPED_EXTENSION_MARKETS = ("JP", "GB", "DE")
_UNMAPPED_EXTENSION_SCENARIOS = (
    "historical.daily_bars.unadjusted.v1",
    "historical.daily_bars.adjusted.v1",
    "financial.income_statement.standard.specified_period.v1",
    "financial.balance_sheet.standard.specified_period.v1",
    "financial.cash_flow.standard.specified_period.v1",
    "financial.direct_line_items.specified_period.v1",
    "financial.latest_filed.direct_metric.v1",
)
_UNMAPPED_CADENCE_MARKETS = ("SSE", "SZSE", "HKEX", "US")
_UNMAPPED_CADENCE_SCENARIOS = (
    "historical.weekly_bars.unadjusted.v1",
    "historical.monthly_bars.unadjusted.v1",
)
_EXTENSION_ENTRIES = tuple(
    RuntimeCatalogEntry(market, scenario, "unverified", "unsupported", (), (), "route_unmapped", None)
    for markets, scenarios in (
        (_UNMAPPED_EXTENSION_MARKETS, _UNMAPPED_EXTENSION_SCENARIOS),
        (_UNMAPPED_CADENCE_MARKETS, _UNMAPPED_CADENCE_SCENARIOS),
    )
    for market in markets
    for scenario in scenarios
)
EXTENSION_ENTRY_COUNT = len(_EXTENSION_ENTRIES)


def _tool_ids(value: str | tuple[str, ...]) -> tuple[str, ...]:
    if type(value) is str:
        return (value,)
    if type(value) is tuple and value and all(type(item) is str and item for item in value):
        return value
    raise ValueError("domain route tool contract is invalid")


_DOMAIN_ROUTE_TOOLS = MappingProxyType({
    key: _tool_ids(value)
    for routes in (_FINANCIAL_ROUTE_TOOLS, _HISTORICAL_ROUTE_TOOLS, _REALTIME_ROUTE_TOOLS)
    for key, value in routes.items()
})
DISPATCHABLE_ENTRY_COUNT = len(_DOMAIN_ROUTE_TOOLS)


def _runtime_entry(entry: RuntimeCatalogEntry) -> RuntimeCatalogEntry:
    tools = _DOMAIN_ROUTE_TOOLS.get((entry.market, entry.scenario))
    if tools is None:
        return entry
    evidence = entry.evidence
    if evidence is None and entry.scenario in _UNMAPPED_CADENCE_SCENARIOS:
        evidence = "fixed daily route with provider row identity/date/OHLCV validation; deterministic Monday-Sunday or natural-month aggregation; partial when the requested range lacks a full aggregate period; no extra Tool call"
    if evidence is None and entry.market in _UNMAPPED_EXTENSION_MARKETS and entry.scenario == "historical.daily_bars.unadjusted.v1":
        evidence = "live FMP historical EOD schema: top-level rows with symbol/date/open/high/low/close/volume/change/changePercent/vwap; identity, in-range ISO date, numeric, and OHLCV gates; adjustment basis, currency, unit, pagination, and source as_of remain unreported"
    if evidence is None and entry.market in _UNMAPPED_EXTENSION_MARKETS and entry.scenario in {
        "financial.income_statement.standard.specified_period.v1",
        "financial.balance_sheet.standard.specified_period.v1",
        "financial.cash_flow.standard.specified_period.v1",
        "financial.direct_line_items.specified_period.v1",
    }:
        evidence = "live FMP annual statement schema: rows with symbol/reportedCurrency/date/fiscalYear/period; FY identity and currency are validated, while unit, pagination, and source as_of remain unreported"
    if evidence is None and entry.scenario == "financial.latest_filed.direct_metric.v1":
        evidence = "FMP statement projection admits latest-filed only when one selected provider row carries an unambiguous filingDate; absent or ambiguous filingDate terminalizes no_data"
    return replace(
        entry,
        disposition="dispatchable",
        tool_ids=tools,
        reason="fixed public GET domain route and evidence schema admitted for runtime",
        evidence=evidence,
    )


RUNTIME_CATALOG = MappingProxyType({
    (entry.market, entry.scenario): _runtime_entry(entry)
    for entry in (*_ENTRIES, *_EXTENSION_ENTRIES)
})


def catalog_entry(market: str, scenario: str) -> RuntimeCatalogEntry | None:
    """Return the frozen admission decision for one canonical market/scenario pair."""
    return RUNTIME_CATALOG.get((market, scenario))

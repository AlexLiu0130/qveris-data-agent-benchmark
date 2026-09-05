# Public GET route coverage

This is the current static admission inventory, not a live-acceptance report. `runtime_catalog.RUNTIME_CATALOG` has 113 entries: 86 `dispatchable` (financial 33, historical 29, realtime 24) and 27 non-dispatch entries. The original frozen matrix had 84 entries. The remaining 29 entries add JP/GB/DE candidate coverage (21 entries) and weekly/monthly cadence coverage for SSE/SZSE/HKEX/US (8 entries). A dispatchable entry has a fixed renderer/parser/projection and one Tool schema path; it does not claim a successful live GET, fresh data, or a scored case.

All dispatchable routes preserve the one-Agent, one structured semantic output, and at-most-one Tool execution contract. Provider-direct schema and single-Tool path checks exist. `sandbox_get_entry`/broker has also passed historical and Alpha-pointer full JSONL offline model→Tool→host-bounded-download flow; the image remains `--network none` and the Oracle remains outside it. Six representative post-fix live requests succeeded with one model and one Tool each: AAPL FY2024 revenue, assets, and operating cash flow; one CN daily-bar request; one HK daily-bar request; and 7203.T FY2024 revenue. No raw value, usage receipt, or run artifact is published here. The formal 600-cell evaluation (three explicitly configured model Variants × 200 cases) has not run. The Oracle is isolated in the Runner/Scorer process and is never an input to public `get`.

## Current dispatchable Tool mapping

This controlled table is derived from the three current `SUPPORTED_KEYS`
declarations and reconciled through `runtime_catalog.RUNTIME_CATALOG`. A row
is a current `dispatchable` market/scenario. Where the Tool cell contains more
than one ID, the semantic route selects one before dispatch; the list is not a
runtime fallback chain and never permits a second Tool call.

### Financial statements (33)

| Market | Scenario | Tool ID(s) |
| --- | --- | --- |
| DE | balance_sheet.standard.specified_period | financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1 |
| DE | cash_flow.standard.specified_period | financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| DE | direct_line_items.specified_period | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| DE | income_statement.standard.specified_period | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f |
| DE | latest_filed.direct_metric | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| GB | balance_sheet.standard.specified_period | financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1 |
| GB | cash_flow.standard.specified_period | financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| GB | direct_line_items.specified_period | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| GB | income_statement.standard.specified_period | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f |
| GB | latest_filed.direct_metric | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| HKEX | balance_sheet.standard.specified_period | fiu_mcp_server.postapihkf10financebalance.create.v2.2c215b4b |
| HKEX | cash_flow.standard.specified_period | fiu_mcp_server.postapihkf10financecash.create.v2.baf7f651 |
| HKEX | direct_line_items.specified_period | fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2; fiu_mcp_server.postapihkf10financebalance.create.v2.2c215b4b; fiu_mcp_server.postapihkf10financecash.create.v2.baf7f651 |
| HKEX | income_statement.standard.specified_period | fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2 |
| JP | balance_sheet.standard.specified_period | financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1 |
| JP | cash_flow.standard.specified_period | financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| JP | direct_line_items.specified_period | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| JP | income_statement.standard.specified_period | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f |
| JP | latest_filed.direct_metric | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| SSE | balance_sheet.standard.specified_period | fiu_mcp_server.postapihsf10financebalance.create.v2.481102ad |
| SSE | cash_flow.standard.specified_period | fiu_mcp_server.postapihsf10financecash.create.v2.93172fa6 |
| SSE | direct_line_items.specified_period | fiu_mcp_server.postapihsf10financeincome.create.v2.6f98cc58; fiu_mcp_server.postapihsf10financebalance.create.v2.481102ad; fiu_mcp_server.postapihsf10financecash.create.v2.93172fa6 |
| SSE | income_statement.standard.specified_period | fiu_mcp_server.postapihsf10financeincome.create.v2.6f98cc58 |
| SZSE | balance_sheet.standard.specified_period | cn_financial_pro.balance_sheet.v1 |
| SZSE | cash_flow.standard.specified_period | cn_financial_pro.cash_flow_statement.v1 |
| SZSE | direct_line_items.specified_period | cn_financial_pro.income_statement.v1; cn_financial_pro.balance_sheet.v1; cn_financial_pro.cash_flow_statement.v1 |
| SZSE | income_statement.standard.specified_period | cn_financial_pro.income_statement.v1 |
| US | balance_sheet.standard.specified_period | alphavantage.balance_sheet.retrieve.v1.467a92c0; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1 |
| US | cash_flow.standard.specified_period | alphavantage.cash_flow.retrieve.v1.7aca3c4a; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| US | direct_line_items.specified_period | alphavantage.income_statement.retrieve.v1.7aca3c4a; alphavantage.balance_sheet.retrieve.v1.467a92c0; alphavantage.cash_flow.retrieve.v1.7aca3c4a; financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |
| US | income_statement.as_reported.specified_period | financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47 |
| US | income_statement.standard.specified_period | alphavantage.income_statement.retrieve.v1.7aca3c4a; financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f |
| US | latest_filed.direct_metric | financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f; financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1; financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354 |

### Historical prices (29)

| Market | Scenario | Tool ID |
| --- | --- | --- |
| DE | daily_bars.unadjusted | financialmodelingprep.historical_price_eod.full.retrieve.v1.f9aefe40 |
| GB | daily_bars.unadjusted | financialmodelingprep.historical_price_eod.full.retrieve.v1.f9aefe40 |
| HKEX | corporate_actions | fiu_mcp_server.postapihkf10summarycadividends.create.v2.d3fe48e2 |
| HKEX | daily_bars.unadjusted | hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924 |
| HKEX | monthly_bars.unadjusted | hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924 |
| HKEX | trading_calendar | cn_financial_pro.trade_dates.v1 |
| HKEX | weekly_bars.unadjusted | hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924 |
| JP | daily_bars.unadjusted | financialmodelingprep.historical_price_eod.full.retrieve.v1.f9aefe40 |
| SSE | adjustment_factors | cn_financial_pro.adjusted_price.v1 |
| SSE | corporate_actions | fiu_mcp_server.postapihsf10summarycadividends.create.v2.88186c04 |
| SSE | daily_bars.adjusted | cn_financial_pro.adjusted_price.v1 |
| SSE | daily_bars.unadjusted | cn_financial_pro.history_quotation.v1 |
| SSE | intraday_bars.unadjusted | cn_financial_pro.hf_basic_quotation.v1 |
| SSE | monthly_bars.unadjusted | cn_financial_pro.history_quotation.v1 |
| SSE | weekly_bars.unadjusted | cn_financial_pro.history_quotation.v1 |
| SZSE | adjustment_factors | cn_financial_pro.adjusted_price.v1 |
| SZSE | daily_bars.adjusted | cn_financial_pro.adjusted_price.v1 |
| SZSE | daily_bars.unadjusted | cn_financial_pro.history_quotation.v1 |
| SZSE | intraday_bars.unadjusted | cn_financial_pro.hf_basic_quotation.v1 |
| SZSE | monthly_bars.unadjusted | cn_financial_pro.history_quotation.v1 |
| SZSE | weekly_bars.unadjusted | cn_financial_pro.history_quotation.v1 |
| US | adjustment_factors | tiingo.daily.ticker.prices.list.v1 |
| US | corporate_actions | tiingo.daily.ticker.prices.list.v1 |
| US | daily_bars.adjusted | tiingo.daily.ticker.prices.list.v1 |
| US | daily_bars.unadjusted | tiingo.daily.ticker.prices.list.v1 |
| US | intraday_bars.adjusted | alphavantage.time_series_intraday.retrieve.v1.1e18340d |
| US | intraday_bars.unadjusted | alphavantage.time_series_intraday.retrieve.v1.1e18340d |
| US | monthly_bars.unadjusted | tiingo.daily.ticker.prices.list.v1 |
| US | weekly_bars.unadjusted | tiingo.daily.ticker.prices.list.v1 |

### Realtime quotes (24)

| Market | Scenario | Tool ID |
| --- | --- | --- |
| HKEX | batch_quote_snapshot | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| HKEX | bid_ask_l1 | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| HKEX | last_price | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| HKEX | latest_trade | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| HKEX | quote_snapshot | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| HKEX | trading_status | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| HKEX | volume_turnover_snapshot | hangseng_polysource.quote.hkshares.live.v2.dec427af |
| SSE | batch_quote_snapshot | cn_financial_pro.real_time_quotation.v1 |
| SSE | bid_ask_l1 | caidazi.get_real_time_record.execute.v1.7a43f96e |
| SSE | last_price | cn_financial_pro.real_time_quotation.v1 |
| SSE | latest_trade | cn_financial_pro.real_time_quotation.v1 |
| SSE | quote_snapshot | cn_financial_pro.real_time_quotation.v1 |
| SSE | volume_turnover_snapshot | cn_financial_pro.real_time_quotation.v1 |
| SZSE | batch_quote_snapshot | cn_financial_pro.real_time_quotation.v1 |
| SZSE | bid_ask_l1 | caidazi.get_real_time_record.execute.v1.7a43f96e |
| SZSE | last_price | cn_financial_pro.real_time_quotation.v1 |
| SZSE | latest_trade | cn_financial_pro.real_time_quotation.v1 |
| SZSE | quote_snapshot | cn_financial_pro.real_time_quotation.v1 |
| SZSE | volume_turnover_snapshot | cn_financial_pro.real_time_quotation.v1 |
| US | bid_ask_l1 | alphavantage.realtime_bulk_bid_ask_prices.retrieve.v1.9b8a7c6d |
| US | extended_hours_price | qveris_finance.mkt_after_hours |
| US | last_price | alphavantage.global_quote.retrieve.v1.9b8a7c6d |
| US | quote_snapshot | alphavantage.global_quote.retrieve.v1.9b8a7c6d |
| US | volume_turnover_snapshot | alphavantage.global_quote.retrieve.v1.9b8a7c6d |

## This-round route changes and evidence metadata

- US historical daily, corporate-action, adjustment-factor, weekly, and monthly routes now select Tiingo; US intraday remains the one-month Alpha Vantage route.
- US volume/turnover now projects from Alpha Vantage global quote. It can be `partial` when the requested turnover field is absent; it is not a separate FIU endpoint.
- US FY standard statements use the three Alpha Vantage statement routes. A pointer is only an untrusted locator: the host must allowlist, bounded-fetch, and parse it before projection; no pointer URL is public data.
- SSE and HKEX direct line items reuse the respective income, balance-sheet, and cash-flow statement routes selected by statement type. They do not introduce a fourth direct-fields endpoint.
- `latest_filed.direct_metric` selects the provider row only from an unambiguous provider date (`filingDate` for `filed`, `date` for `report`); absent or tied dates terminalize `no_data`.
- Checked source metadata: `domain_routes_realtime.SUPPORTED_KEYS`, `domain_routes_historical.SUPPORTED_KEYS`, `domain_routes_financial.SUPPORTED_KEYS`, and the reconciled `runtime_catalog.RUNTIME_CATALOG`. No local `live_alpha_remaining_statements` or `global_market_probe` receipt artifact was present in this worktree or `/private/tmp` at documentation time, so this document records no receipt source hash, raw response, URL, credential, account, execution ID, or cost.

## Non-dispatch entries

`Tool` is the observed candidate Tool ID, or `—` when no Tool contract is admitted. Reasons below are the catalog's current terminal boundary; they are not permission to substitute another provider or infer missing facts.

| Market | Scenario | State | Tool | Exact reason |
| --- | --- | --- | --- | --- |
| HKEX | financial.income_statement.as_reported.specified_period.v1 | gap | — | standard FIU statement is not as-reported evidence |
| HKEX | financial.latest_filed.direct_metric.v1 | gap | — | latest filing ordering/provenance not established |
| HKEX | historical.adjustment_factors.v1 | unverified | — | no observed HKEX adjustment-factor contract |
| HKEX | historical.daily_bars.adjusted.v1 | unverified | hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4 | response is a single forward-adjusted range summary, not dated daily OHLCV bars |
| HKEX | historical.intraday_bars.adjusted.v1 | rejected | fiu_mcp_server.postv1chartklinelist.create.v2.41a84fef | exact exploratory input did not return usable provider data |
| HKEX | historical.intraday_bars.unadjusted.v1 | rejected | fiu_mcp_server.postv1chartklinelist.create.v2.41a84fef | exact exploratory input did not return usable provider data |
| HKEX | realtime.equity.extended_hours_price.v1 | not_applicable | — | product taxonomy decision |
| SSE | financial.income_statement.as_reported.specified_period.v1 | gap | — | standard statement is not as-reported evidence |
| SSE | financial.latest_filed.direct_metric.v1 | gap | — | latest filing ordering/provenance not established |
| SSE | historical.intraday_bars.adjusted.v1 | gap | — | no observed adjusted intraday contract |
| SSE | historical.trading_calendar.v1 | unverified | — | no exact observed SSE calendar contract |
| SSE | realtime.equity.extended_hours_price.v1 | not_applicable | — | product taxonomy decision for mainland A shares |
| SSE | realtime.equity.trading_status.v1 | unverified | — | quote receipt does not yet establish trading-status semantics |
| SZSE | financial.income_statement.as_reported.specified_period.v1 | gap | — | standard statement is not as-reported evidence |
| SZSE | financial.latest_filed.direct_metric.v1 | gap | — | latest filing ordering/provenance not established |
| SZSE | historical.corporate_actions.v1 | rejected | mcp_gildata.bonusstock.v1 | exact query contract did not return usable provider data |
| SZSE | historical.intraday_bars.adjusted.v1 | unverified | — | no observed adjusted intraday contract |
| SZSE | historical.trading_calendar.v1 | unverified | — | no exact observed SZSE calendar contract |
| SZSE | realtime.equity.extended_hours_price.v1 | not_applicable | — | product taxonomy decision for mainland A shares |
| SZSE | realtime.equity.trading_status.v1 | unsupported | cn_financial_pro.real_time_quotation.v1 | tool evidence is provisional, but the runtime renderer/parser/postprocess contract is not frozen |
| US | historical.trading_calendar.v1 | rejected | theta_data.calendar.ondate.retrieve.v3.e44984f2 | returned data cannot establish the requested trading-calendar semantics |
| US | realtime.equity.batch_quote_snapshot.v1 | rejected | fiu_mcp_server.postv3stockquote.create.v2.a49a2677 | exact receipt has no usable batch quote snapshot |
| US | realtime.equity.latest_trade.v1 | rejected | fiu_mcp_server.postv3stockquote.create.v2.a49a2677 | exact receipt has no usable latest-trade data |
| US | realtime.equity.trading_status.v1 | unverified | — | marketstatus receipt was semantically uncertain; do not infer a tradable session state |
| JP | historical.daily_bars.adjusted.v1 | unsupported | — | route_unmapped |
| GB | historical.daily_bars.adjusted.v1 | unsupported | — | route_unmapped |
| DE | historical.daily_bars.adjusted.v1 | unsupported | — | route_unmapped |

## Handoff use

Construct a live client with `QVerisPublicGetConfig.from_environment()` and `build_qveris_public_get_client(...)`; the required environment names are `QVERIS_MODEL_GATEWAY_API_KEY`, `QVERIS_API_KEY`, and `QVERIS_MODEL_GATEWAY_MODEL`. Configure each of the three evaluation models separately. The factory returns a `PublicGetAdapter` compatible with `RunService`; each call must return `PublicGetResult(public_response, execution_evidence)`. `public_response` is the strictly normalized `get-response/v1` or `get-response/v2` user result. `execution_evidence` is private trusted adapter evidence (identity, counts, tool alias and timing), not a public field and not Oracle evidence. `meta.usage` only represents a qualifying model receipt; unavailable usage remains explicitly unavailable.

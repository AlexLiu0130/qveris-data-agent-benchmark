# GET all-scenarios frozen metadata v1

Source: local `docs/scenarios/qveris-market-tool-registry-v2.json`, frozen
`2026-09-04`. This is the dispatch-planning projection only: it contains no
credentials, receipt IDs, raw provider responses, response links, or costs.
It does not authorize a runtime call, fallback, benchmark scoring, or provider
certification.

**Superseded mapping.** This v1 file is retained only as the 84-cell migration
baseline. It is not the immutable current dispatch map: see the current
113-cell admission and Tool mapping in [`../get-route-coverage.md`](../get-route-coverage.md).

Every dispatched case still has one public `get`, one structured output, one
selected tool, and `fallback=none`.

## Counts

| Category | P | U | G | N | R | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| realtime | 25 | 2 | 0 | 3 | 2 | 32 |
| historical | 18 | 5 | 1 | 0 | 4 | 28 |
| financial | 16 | 0 | 8 | 0 | 0 | 24 |
| **all active cells** | **59** | **7** | **9** | **3** | **6** | **84** |

- `P` — `provisional_basic`: frozen tool mapping; not a runtime admission.
- `U` / `G` — no admissible GET route (`unverified` / capability `gap`).
- `R` — a precise tool was tried but its frozen contract was rejected; it is
  not a missing-tool case and must not be retried or used as fallback.
- `N` — intentional product-taxonomy exclusion, not a missing-tool case.
  The source does not call these explicit user cancellations; all three are
  the extended-hours subdomain for SSE, SZSE, and HKEX.

## Frozen cell-to-tool map

Each cell is `state: tool-id`; `-` means no dispatchable tool. `P` cells are
the only candidates for later GET wiring. `U`, `G`, `N`, and `R` must remain
explicit non-success routing outcomes until a separately authorized admission.

### Realtime (8 cells x 4 markets)

| Scenario | US | SSE | SZSE | HKEX |
| --- | --- | --- | --- | --- |
| `quote_snapshot` | P: `alphavantage.global_quote.retrieve.v1.9b8a7c6d` | P: `cn_financial_pro.real_time_quotation.v1` | P: `cn_financial_pro.real_time_quotation.v1` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |
| `last_price` | P: `alphavantage.global_quote.retrieve.v1.9b8a7c6d` | P: `cn_financial_pro.real_time_quotation.v1` | P: `cn_financial_pro.real_time_quotation.v1` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |
| `bid_ask_l1` | P: `alphavantage.realtime_bulk_bid_ask_prices.retrieve.v1.9b8a7c6d` | P: `caidazi.get_real_time_record.execute.v1.7a43f96e` | P: `caidazi.get_real_time_record.execute.v1.7a43f96e` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |
| `volume_turnover_snapshot` | P: `fiu_mcp_server.postv1stockquote.create.v2.1790f84e` | P: `cn_financial_pro.real_time_quotation.v1` | P: `cn_financial_pro.real_time_quotation.v1` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |
| `latest_trade` | R: `fiu_mcp_server.postv3stockquote.create.v2.a49a2677` | P: `cn_financial_pro.real_time_quotation.v1` | P: `cn_financial_pro.real_time_quotation.v1` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |
| `extended_hours_price` | P: `qveris_finance.mkt_after_hours` | N: `-` | N: `-` | N: `-` |
| `trading_status` | U: `-` | U: `-` | P: `cn_financial_pro.real_time_quotation.v1` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |
| `batch_quote_snapshot` | R: `fiu_mcp_server.postv3stockquote.create.v2.a49a2677` | P: `cn_financial_pro.real_time_quotation.v1` | P: `cn_financial_pro.real_time_quotation.v1` | P: `hangseng_polysource.quote.hkshares.live.v2.dec427af` |

### Historical (7 cells x 4 markets)

| Scenario | US | SSE | SZSE | HKEX |
| --- | --- | --- | --- | --- |
| `daily_bars.unadjusted` | P: `alphavantage.time-series.daily.v1` | P: `cn_financial_pro.history_quotation.v1` | P: `cn_financial_pro.history_quotation.v1` | P: `hangseng_polysource.hk.stock.daily.quote.create.v2.dd094924` |
| `daily_bars.adjusted` | P: `qveris_finance.mkt_bars_adjusted` | P: `cn_financial_pro.adjusted_price.v1` | P: `cn_financial_pro.adjusted_price.v1` | U: `hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4` |
| `intraday_bars.unadjusted` | P: `alphavantage.time_series_intraday.retrieve.v1.1e18340d` | P: `cn_financial_pro.hf_basic_quotation.v1` | P: `cn_financial_pro.hf_basic_quotation.v1` | R: `fiu_mcp_server.postv1chartklinelist.create.v2.41a84fef` |
| `intraday_bars.adjusted` | P: `alphavantage.time_series_intraday.retrieve.v1.1e18340d` | G: `-` | U: `-` | R: `fiu_mcp_server.postv1chartklinelist.create.v2.41a84fef` |
| `corporate_actions` | P: `tiingo.daily.ticker.prices.list.v1` | P: `fiu_mcp_server.postapihsf10summarycadividends.create.v2.88186c04` | R: `mcp_gildata.bonusstock.v1` | P: `fiu_mcp_server.postapihkf10summarycadividends.create.v2.d3fe48e2` |
| `adjustment_factors` | P: `tiingo.daily.ticker.prices.list.v1` | P: `cn_financial_pro.adjusted_price.v1` | P: `cn_financial_pro.adjusted_price.v1` | U: `-` |
| `trading_calendar` | R: `theta_data.calendar.ondate.retrieve.v3.e44984f2` | U: `-` | U: `-` | P: `cn_financial_pro.trade_dates.v1` |

### Financial (6 cells x 4 markets)

| Scenario | US | SSE | SZSE | HKEX |
| --- | --- | --- | --- | --- |
| `income_statement.standard.specified_period` | P: `financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f` | P: `fiu_mcp_server.postapihsf10financeincome.create.v2.6f98cc58` | P: `cn_financial_pro.income_statement.v1` | P: `fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2` |
| `balance_sheet.standard.specified_period` | P: `financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1` | P: `fiu_mcp_server.postapihsf10financebalance.create.v2.481102ad` | P: `cn_financial_pro.balance_sheet.v1` | P: `fiu_mcp_server.postapihkf10financebalance.create.v2.2c215b4b` |
| `cash_flow.standard.specified_period` | P: `financialmodelingprep.stable.cashflowstatement.retrieve.v1.dfeb9354` | P: `fiu_mcp_server.postapihsf10financecash.create.v2.93172fa6` | P: `cn_financial_pro.cash_flow_statement.v1` | P: `fiu_mcp_server.postapihkf10financecash.create.v2.baf7f651` |
| `income_statement.as_reported.specified_period` | P: `financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47` | G: `-` | G: `-` | G: `-` |
| `direct_line_items.specified_period` | P: `financialmodelingprep.stable.incomestatement.retrieve.v1.dd6d583f` or `financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1` by statement type | G: `-` | P: `cn_financial_pro.income_statement.v1`, `cn_financial_pro.balance_sheet.v1`, or `cn_financial_pro.cash_flow_statement.v1` by statement type | P: `fiu_mcp_server.postapihkf10financeincome.create.v2.c2e039d2` (income only) |
| `latest_filed.direct_metric` | G: `-` | G: `-` | G: `-` | G: `-` |

## Required guards retained from the source

- A direct-line-item request chooses exactly one statement type and tool before
  dispatch; cross-statement requests have no fallback or second execution.
- HKEX adjusted daily bars are `U`: its named tool returns a forward-adjusted
  range summary rather than dated daily OHLCV.
- HKEX direct line items are only the 13-field income-summary contract for
  `00700.HK` FY2024; unsupported lines stay unsupported.
- SSE direct line items remain `G`: raw receipt replay is absent, so candidate
  parsers are not admitted.

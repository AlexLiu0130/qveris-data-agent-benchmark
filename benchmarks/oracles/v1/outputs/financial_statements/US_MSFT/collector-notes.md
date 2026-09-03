# Microsoft FY2026 three-statement collection

- Scope: MSFT cases `FS-047`, `FS-051`, `FS-055`, `FS-059`, `FS-063`, and `FS-067`.
- Fiscal period: year ended 2026-06-30. SEC Form 10-K accession `0001193125-26-323660`, filed 2026-07-29.
- Basis: `as_reported`; all monetary line values are decimal strings in USD millions, except EPS (USD per share) and weighted-average shares (millions of shares). Parenthetical source values are represented with a leading minus sign. Income statement includes FY2026/FY2025/FY2024; balance sheet includes FY2026/FY2025; cash flow includes FY2026/FY2025/FY2024.
- Primary lineage: SEC 10-K -> `FilingSummary.xml` -> `R2.htm` income statement, `R4.htm` balance sheet, `R6.htm` cash-flow statement -> atomic assertion.
- IR lineage: Microsoft Investor Relations FY2026 Q4 release -> canonical table extraction at `#income-statements`, `#balance-sheets`, or `#cash-flows` -> atomic assertion.
- Fixed-download lineage: Microsoft Investor Relations SEC Filings -> `https://aka.ms/MSFT_FY26Q4_10k` -> official `MSFT_FY26Q4_10K.docx` -> OOXML statement tables -> atomic assertion. This is a distinct Microsoft delivery and an independently replayed parse, but it is the same authoritative disclosure origin as the SEC filing.

## Official DOCX second-delivery coverage

Receipt `microsoft-ir-fy2026-annual-form-10k-docx` records the official Microsoft fixed Word delivery. Its downloaded bytes hash to `55b3613c22faaa1c99377003cf1d56a0d8a65c1e25f649d82b2fd03c3bd1996a`.

The stable canonical extract uses only `word/document.xml`: select the three body-child tables below, concatenate `w:t` content in each cell, apply NFKC, collapse Unicode whitespace to one space, strip, retain empty cells and row/cell order, and serialize UTF-8 JSON with `ensure_ascii=false` and `separators=(\",\",\":\")`. The resulting 13,371-byte extract hashes to `09b1804b6270c5a5dab78286443eeef196cb56b41d8ae105ce5a3be49d861d94`.

An independent replay of the locator catalog below compared every frozen candidate's exact normalized value with the DOCX output: **213/213 matched** (`45` income statement, `66` balance sheet, `102` cash flow). No Oracle value was changed.

### Locator grammar

Each locator is `tables.<table>.rows[<row>].cells[<start>:<end>]`, where `row` is one-based in the selected OOXML table and cell indices are zero-based, half-open. The table's header establishes the period segments:

- `income_statements` = body child `590`: FY2026 `[3:7]`, FY2025 `[7:11]`, FY2024 `[11:end]`.
- `balance_sheets` = body child `600`: FY2026 `[3:7]`, FY2025 `[7:end]`.
- `cash_flows_statements` = body child `604`: FY2026 `[3:7]`, FY2025 `[7:11]`, FY2024 `[11:end]`.

For every field row listed below, apply the period segment above. Thus the catalog is a per-assertion locator: each field-row multiplied by its Oracle's named periods produces all 213 precise table-row-cell locations.

| Statement | Assertion field -> source row |
|---|---|
| Income | `revenue` 11; `cost_of_revenue` 19; `gross_margin` 22; `research_and_development` 23; `sales_and_marketing` 24; `general_and_administrative` 25; `operating_income` 28; `other_income_expense_net` 29; `income_before_income_taxes` 32; `provision_for_income_taxes` 33; `net_income` 36; `basic_earnings_per_share` 40; `diluted_earnings_per_share` 41; `basic_weighted_average_shares_outstanding` 44; `diluted_weighted_average_shares_outstanding` 45 |
| Balance sheet | `cash_and_cash_equivalents` 8; `short_term_investments` 9; `total_cash_cash_equivalents_and_short_term_investments` 12; `accounts_receivable_net` 13; `inventories` 14; `other_current_assets` 15; `total_current_assets` 18; `property_and_equipment_net` 19; `operating_lease_right_of_use_assets` 20; `equity_and_other_investments` 21; `goodwill` 22; `intangible_assets_net` 23; `other_long_term_assets` 24; `total_assets` 27; `accounts_payable` 32; `current_portion_of_long_term_debt` 33; `accrued_compensation` 34; `short_term_income_taxes` 35; `short_term_unearned_revenue` 36; `other_current_liabilities` 37; `total_current_liabilities` 40; `long_term_debt` 41; `long_term_income_taxes` 42; `long_term_unearned_revenue` 43; `deferred_income_taxes` 44; `operating_lease_liabilities` 45; `other_long_term_liabilities` 46; `total_liabilities` 49; `common_stock_and_paid_in_capital` 54; `retained_earnings` 55; `accumulated_other_comprehensive_loss` 56; `total_stockholders_equity` 59; `total_liabilities_and_stockholders_equity` 62 |
| Cash flow | `net_income` 7; `depreciation_amortization_and_other` 9; `stock_based_compensation_expense` 10; `net_recognized_losses_gains_on_investments_and_derivatives` 11; `deferred_income_taxes` 12; `accounts_receivable` 14; `inventories` 15; `other_current_assets` 16; `other_long_term_assets` 17; `accounts_payable` 18; `unearned_revenue` 19; `income_taxes` 20; `other_current_liabilities` 21; `other_long_term_liabilities` 22; `net_cash_from_operations` 25; `debt_maturities_90_days_or_less_net` 29; `proceeds_from_issuance_of_debt` 30; `repayments_of_debt` 31; `common_stock_issued` 32; `common_stock_repurchased` 33; `common_stock_cash_dividends_paid` 34; `other_financing_net` 35; `net_cash_used_in_financing` 38; `additions_to_property_and_equipment` 42; `acquisitions_and_other_assets` 43; `purchases_of_investments` 44; `maturities_of_investments` 45; `sales_of_investments` 46; `other_investing_net` 47; `net_cash_used_in_investing` 50; `effect_of_foreign_exchange_rates_on_cash` 53; `net_change_in_cash_and_cash_equivalents` 56; `cash_and_cash_equivalents_beginning_of_period` 57; `cash_and_cash_equivalents_end_of_period` 60 |

## Status and gaps

Oracle records are `frozen`; final independent review-ledger re-signatures and financial-suite manifest validation are complete. This market's normal-case assertions are part of the 27-record, 1,198-assertion scoreable financial package. SEC exposes a filing date but not a filing-time value in the submissions payload; the PIT availability time is therefore conservatively set to the following UTC day, and the source publication timestamp is date-normalized.

Microsoft's annual-reports index retrieved on 2026-09-03 listed 2025 as its newest annual report; no FY2026 annual-report entry was used. The FY2026 Q4 IR earnings release reproduces the three statement tables, but it shares Microsoft's authoritative disclosure origin with the SEC 10-K. The official FY26 Annual Form 10K Word delivery on the IR SEC Filings page now provides a fixed, independently parsed second delivery that includes the cash-flow rows absent from the dynamic Q4 release; its three tables replay to 213/213 assertions. Every receipt still records `relationship=same_authoritative_origin`; IR releases, IR DOCX, and companyfacts are `delivery_or_parse_only`, not independent factual corroboration. The IR page is dynamic, so its receipt hash is a fixed canonical extraction of the three named tables, not a page-byte hash; `retrieved_at` remains the freshness boundary.

The prior product/service revenue and cost rows were removed: they are not part of SEC `R2.htm`'s formal consolidated income-statement face. No supplemental-note locator was frozen for those rows.

No raw filings, provider responses, credentials, or reports are stored in this package. Full URLs and fetched-byte SHA-256 values are in `evidence-receipts.json`.

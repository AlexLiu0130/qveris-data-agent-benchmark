# Apple FY2025 consolidated-statement collection

- Scope: `FS-046`, `FS-058` (operations); `FS-050`, `FS-062` (balance sheets); `FS-054`, `FS-066` (cash flows).
- Filing: Apple Inc. CIK `0000320193`, Form `10-K`, accession `0000320193-25-000079`, fiscal year ended `2025-09-27`, filed `2025-10-31`. SEC submission header gives `ACCEPTANCE-DATETIME 20251031060126`; the candidate retains this as `2025-10-31T06:01:26-04:00` (Eastern daylight time).
- Presentation: direct transcription of the consolidated Item 8 tables, with every numeric row/column represented as a decimal string. Operations and cash flows include FY2025/FY2024/FY2023 comparative columns; balance sheets include 2025-09-27 and 2024-09-28. Statement section headings and blank separators have no numeric assertion. Parenthetical amounts become `-` values only; no restatement, conversion, calculation, or rounding was applied. The affected FY2024 cash-flow cell is deliberately `null`, not a numeric value; see the freeze blocker below.
- Units: statement monetary values are `USD_millions`; EPS is `USD_per_share`; shares used in EPS are `shares_thousands`. `currency: N/A` on share-count assertions is deliberate and preserves the schema-required currency field without claiming money.

## Lineage and review state

Each assertion carries its report-table path and period. Receipt `aapl-fy2025-sec-10k-primary` gives the source URL, SHA-256, accession, period, unit, Item 8 table, HTML lines, and report page for all values. `authoritative_origin_id=SEC accession 0000320193-25-000079`. The Apple IR rendering and SEC companyfacts snapshot are `corroboration_scope=delivery_or_parse_only` and `independent_authoritative_origin=false`; they are separately hashed in `evidence-receipts.json`.

Oracle records are `frozen`; final independent review-ledger re-signatures and financial-suite manifest validation are complete. This market's normal-case assertions are part of the 27-record, 1,198-assertion scoreable financial package. The Apple IR re-host and SEC companyfacts endpoint originate from the same filed financial statements; their distinct hosting/regulator chains must **not** be claimed as independent authorship. This remains a same-authoritative-origin delivery/parse audit, not a claim of independent fact authorship.

## Display-nil audit: dash-to-zero canonical conflict

Affected oracle/assertion: `financial-statements-us-aapl-fy2025-cash-flow-statement-v1` / `aapl-cf-2024-term-debt-issued` (`FY2024`, `consolidated_statements_of_cash_flows.financing_activities.proceeds_from_issuance_of_term_debt_net`).

Both independently fetched delivery documents place the cell at Item 8, Consolidated Statements of Cash Flows, page 33, HTML line 1105, row “Proceeds from issuance of term debt, net”, FY2024 column: SEC EDGAR `aapl-20250927.htm` and Apple IR's separately hosted HTML document. Separate raw-HTML parses decode `&#8212;` to U+2014 EM DASH in both cells. `confirmed_nil=false`: the strict canonical rule supplies no approved conversion from U+2014 to numeric zero. SEC companyfacts emits a derived XBRL numeric `0` for this accession and period, but it is delivery/parse-only corroboration and conflicts with the primary presentation for canonical scoring. The frozen assertion is therefore `expected: null` with `comparison: exact`; it is not a numeric zero. Its reviewed ledger and suite-manifest binding are validated.

## Self-check result

The supplied JSON is parsed and each array element is checked with a dependency-free closed-key/required-field/enum check against the applicable `oracle/v1` or `evidence-receipt/v1` contract. Cross-references from each oracle evidence pair to receipt IDs, decimal-string representation, row counts, and selected accounting identities are checked. The repository's recursive `validate_freeze.py` remains structural; it does not replace the explicit per-record audit.

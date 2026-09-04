# NVIDIA FY2026 consolidated statements collection

Status: Oracle records are `frozen`; final independent review-ledger re-signatures
and financial-suite manifest validation are complete. This market's normal-case
assertions are part of the 27-record, 1,198-assertion scoreable financial package.
No assertion value was changed by this collection pass. The primary source is NVIDIA's SEC 10-K (accession
`0001045810-26-000021`), accepted and public at `2026-02-25T21:42:19Z`; its
report date is `2026-01-25`.

## Source lineage and distinct delivery/parse channels

- Primary document: [SEC 10-K inline XBRL](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm), SHA-256 `73d81f5a111abcf72426c840871e76f5f5edc9631f436d495a86b6f87306d58b`.
- Income statement: [R3.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/R3.htm), “Consolidated Statements of Income,” SHA-256 `09b4d06ac2f8d95f74ddc8ebaa86d29772bb0074610d33fa1cf7a6b05c97f0af`.
- Balance sheet: [R5.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/R5.htm), “Consolidated Balance Sheets,” SHA-256 `304309cb19c0e19df6971cb0475161f5b90d595c5dcca8de517865ed630ea6e2`.
- Cash-flow statement: [R9.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/R9.htm), “Consolidated Statements of Cash Flows,” SHA-256 `a693ff13a72b24f6e00f0b8c64ad0404735cf995fa7537a019ab49303461ca5d`.
- Structured supplemental representation: [SEC Companyfacts CIK 0001045810](https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json), response SHA-256 `19ef503a5770f5660964b3c3aea6937579d9b359da344afe6a9adf59c63d26ff`, filtered by accession, form `10-K`, fiscal year `2026`, and fiscal period `FY`.
- Second issuer delivery: [NVIDIA FY2026 Q4/FY results-release PDF](https://nvidianews.nvidia.com/_gallery/download_pdf/699f6ab43d6332ccaa689907/), fetched-byte SHA-256 `89de999d7155649197672bf490c519607f1abbcdc96e2e4c6718bf6ecbb13564`; its [official Newsroom HTML presentation](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-fourth-quarter-and-fiscal-2026) was separately row-and-period parsed. It is NVIDIA's February 25, 2026 results release, also listed through Investor Relations and filed as 8-K exhibit 99.1 (accession `0001045810-26-000019`).

The assertions use the source's table presentation: all monetary values are
decimal strings in `USD_millions`; EPS is `USD_per_share`; weighted-average
shares are `shares_millions`. Parentheses in R3/R9 were normalized to a leading
minus. XBRL concept tags are embedded in assertion `field` values, and each
statement prefix identifies its table locator above.

## Adversarial coverage result

The result-release delivery and independent rendered-table parse together cover
all `82/82` assertions with zero value changes, misses, or conflicts:

- R3 independent table parse: `18/18` income-statement assertions.
- R5 independent table parse: `29/29` balance-sheet assertions, including
  `nvda:MarketableSecuritiesAndEquitySecuritiesFVNI`.
- R9 independent table parse: `35/35` cash-flow assertions, including
  `nvda:PaymentsForFinancedPropertyPlantAndEquipmentAndIntangibleAssetsFinancingActivities`.
- NVIDIA's results release re-displays `18/18` income, `22/29` balance-sheet,
  and `35/35` cash-flow assertions (`75/82`). Its balance sheet intentionally
  combines cash with marketable securities and does not separately display the
  five shareholders'-equity components; the seven omitted component assertions
  were resolved only from the independently parsed R5 face statement.

Companyfacts is not eligible to decide these presentation-sensitive cases: it
does not expose the two NVIDIA issuer extensions above, and its structured
debit/credit conventions cannot replace the 14 designated display-sign checks.
Those checks were compared with the parenthetical presentation in R3/R9; the
rendered SEC statement tables remain authoritative for this `as_reported`
Oracle.

Extension assertion links are deliberately narrower than the statement-level
Companyfacts link: `bs-02` pairs the 10-K authority receipt with
`SEC-NVDA-10K-R5-PARSED-0001045810-26-000021`, and `cf-28` pairs it with
`SEC-NVDA-10K-R9-PARSED-0001045810-26-000021`. Each receipt gives the exact
table, row, FY2026 column, concept, displayed value, and SHA-256 above; neither
extension is attributed to Companyfacts or the condensed Newsroom release.

## Freeze limitation

All three receipts have `authoritative_origin_id=NVIDIA-FY2026-issuer-consolidated-financial-statements`.
They are therefore `same_authoritative_origin`: the results release and
Companyfacts are valuable second delivery/parse channels, but are not an
independently authored accounting fact source. Their role is
`delivery_or_parse_only`; the record was frozen only under the approved
same-origin corroboration policy. Its final review-ledger re-signature and
suite-manifest binding are validated. No non-official or
paid source was used, and no raw report, provider response, credential, or
runtime result is retained here.

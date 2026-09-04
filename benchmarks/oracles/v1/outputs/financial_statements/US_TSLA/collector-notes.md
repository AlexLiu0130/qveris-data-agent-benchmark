# Tesla FY2025 collection notes

Status: Oracle records are `frozen`; final independent review-ledger re-signatures and financial-suite manifest validation are complete. This market's normal-case assertions are part of the 27-record, 1,198-assertion scoreable financial package. The package contains SEC iXBRL line-level assertions for the complete consolidated balance sheet, operations statement, and cash-flow statement. Values are decimal strings, in USD millions except stated EPS and share units.

Primary: SEC 10-K `0001628280-26-003952`, fiscal year ended 2025-12-31, accepted `20260128205503`; filing pages 49, 50, and 53 (iXBRL HTML tables 21, 22, and 25). Independent receipt is Tesla Investor Relations' separately hosted PDF copy of the same filing. The receipt file records both source hashes and locations; no source report or raw response is retained.

Audit note: SEC's acceptance timestamp is timezone-less in the filing header and is normalized to US Eastern time. The Tesla IR copy corroborates presentation but is not an independent calculation; it is retained as same-authoritative-origin evidence. The current recursive validator and release workflow supersede the earlier collector-era aggregation limitation.

## Dash-to-zero audit

The only `expected: "0"` assertions are `bs-025`, `cf-019`, `cf-020`, and `cf-030`. All four are confirmed values rather than nil: the SEC iXBRL facts are displayed as Unicode `U+2014` EM DASH and declare `format=ixt:fixed-zero`, `scale=6`, with no `xsi:nil` attribute. Their SEC HTML identifiers are respectively `f-112`, `f-414`, `f-417`, and `f-447`. A separate Tesla IR PDF parse found the same glyph at physical PDF p.51 line 1882 and p.55 lines 2054-2055 and 2066. The exact source locators and hashes are in `evidence-receipts.json`.

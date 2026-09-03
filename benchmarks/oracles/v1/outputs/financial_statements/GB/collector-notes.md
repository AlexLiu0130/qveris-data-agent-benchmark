# Shell plc (SHEL.L) FY2025 consolidated three-statement collection

- Scope: FS-073 and FS-076 income statement; FS-074 balance sheet; FS-075 cash-flow statement. Entity is Shell plc, listed in GB as SHEL.L, reporting period ended 2025-12-31.
- Primary: Shell's public `Consolidated Financial Statements` PDF from its 2025 Annual Report and Accounts. The three source-table locations are pages 214, 215 and 217. It reports IFRS (with no material differences from IFRS as issued by IASB), USD millions, and USD per share for EPS.
- Cross-check: SEC EDGAR Form 20-F accession `0001628280-26-017024`, accepted 2026-03-12 08:06:26 UTC. Its inline XBRL rendering contains matching values on pages 214, 215 and 217. The values in `oracles.json` are FY2025 current-column values exactly as reported; negative parenthesised report values are signed decimal strings.
- PIT: Shell publicly announced its Annual Report and Accounts on 2026-03-12. Because the issuer announcement gives no publication time, the package uses conservative `2026-03-12T23:59:59Z`; SEC's acceptance time is exact.
- Lineage: official statement row and FY2025 column -> public document SHA-256 in `evidence-receipts.json` -> assertion ID and decimal-string `expected` value. No report, raw HTML, credentials or runtime result was copied into this directory.

## Release-gate and schema audit

Oracle records are `frozen`; final independent review-ledger re-signatures and financial-suite manifest validation are complete. This market's normal-case assertions are part of the 27-record, 1,198-assertion scoreable financial package. The prior three-file collector shape did not carry those release artifacts; the retained note documents the collection-era limitation rather than a published runtime score claim.

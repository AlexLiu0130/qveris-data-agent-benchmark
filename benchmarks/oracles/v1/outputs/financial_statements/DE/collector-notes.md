# SAP.DE FY2025 consolidated three-statement collection

Snapshot: `sap-de-fy2025-2026-09-03T093219Z`; Oracle records are `frozen`, with final independent review-ledger re-signatures and financial-suite manifest validation complete. This market's normal-case assertions are part of the 27-record, 1,198-assertion scoreable financial package.

## Scope and extraction

- Cases: `FS-077`, `FS-078`, `FS-079`, and `FS-080`.
- Entity/listing: SAP SE / `SAP.DE`; fiscal year ended 2025-12-31.
- Basis: IFRS, `as_reported`; presentation currency EUR; monetary amounts are EUR millions. Income-statement EPS is EUR per share.
- Authoritative/PIT-anchor delivery: SEC EDGAR Form 20-F, accession `0001104659-26-020058`, accepted 2026-02-26T12:01:22Z. It contains all three statements; its three anchors and SHA-256 are recorded in `evidence-receipts.json`.
- Same-origin corroborating delivery: SAP Integrated Report 2025, PDF pages 211, 213, and 215. The PDF SHA-256 is recorded in `evidence-receipts.json`; it was read as a stream and was not retained. Its exact historical SAP-host upload time is unavailable, so its observed HTTP `Last-Modified` value is delivery metadata only, not a PIT claim.
- All assertion `expected` values are signed decimal strings without thousands separators. Each reports the FY2025 column only; comparative columns are not assertions for a FY2025 request.

## PIT and lineage

The accepted 20-F is the sole authority and establishes that all asserted FY2025 facts were public no later than 2026-02-26T12:01:22Z. SAP's current PDF host exposes a mutable `Last-Modified` header but not its historical upload timestamp. Both deliveries are explicitly `same_authoritative_origin`; SAP-host content corroborates the delivery/parse but never supplies PIT. Every assertion links the same pair, in authority-first order: SEC 20-F then SAP PDF.

The 2025 cash-flow presentation moves interest received to investing activities and interest paid to financing activities; SAP states that comparative periods were amended. This collection asserts only the 2025 reported column.

The statement's reported `Total assets` and `Total equity and liabilities` both equal 70,362. The displayed rounded components `Total liabilities` (25,288) plus `Total equity` (45,073) add to 70,361; this one-EUR-million presentation rounding difference is preserved rather than repaired.

Likewise, displayed cash of 9,609 plus the displayed net decrease of -1,390 gives 8,219, while reported closing cash is 8,220. The one-EUR-million presentation rounding difference is preserved; the operating/investing/financing/FX bridge to the displayed net decrease reconciles exactly.

## Historical freeze-schema audit

- The former non-recursive validator and array-wrapper limitation was resolved before promotion.
- The collector's earlier three-file layout lacked review-ledger and manifest placement; retained package artifacts now hold those release controls.
- `frozen_at` is a record-freeze timestamp, not a collector timestamp. Final ledger re-signatures and suite-manifest validation are complete; Runner and Scorer remain separate, unimplemented runtime components.

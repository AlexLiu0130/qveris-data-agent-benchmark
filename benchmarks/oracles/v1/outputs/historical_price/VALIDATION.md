# Historical-price Oracle v1 validation

Status: **frozen v1 public-traceable baseline**.

- Candidate cases: 100
- State oracles: 50 (excluded from Data Accuracy)
- Numeric contracts: 50
- Numeric records scoreable with at least one source-coherent variant: 50
- Numeric records with evidence missing: 0
- Evidence receipts: 55, normalized to `evidence-receipt/v1`; each retains its original capture metadata and declares `raw_payload_retained: false`

## V1 admission rule

A numeric case retains every complete source-coherent variant. V1 admits one exact-case publicly traceable variant; it does not claim provider authorization, redistribution rights, or official-exchange status. Conflicts remain accepted alternative variants when each is complete and source-coherent: never average, select fields across sources, or splice rows. Strict licensed dual-source reconciliation is a future release upgrade.

## Known evidence gap

- None.

## Integrity checks

- All 50 state contracts are represented exactly once.
- All 50 numeric contracts are represented exactly once.
- Every numeric variant links to one receipt and retains source URL, content hash (or explicitly qualified excerpt hash), retrieval time, currency, unit, adjustment basis and rows restricted to the requested field/date contract.
- Raw Query text is never stored or rewritten by this package; only the registry hash and pre-existing raw-query SHA-256 are referenced.
- review-ledger.json binds the frozen Oracle hash. Numeric cases have independent approved semantic/data decisions; state cases have approved semantic decisions and explicit Data Accuracy exclusion.

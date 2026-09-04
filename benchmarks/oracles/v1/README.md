# Oracle freeze v1

This directory is the evaluator-owned, versioned Oracle package for the
`historical_price`, `financial_statements`, and `realtime_quote` suites. It contains only frozen
answers and their provenance once approved. It does **not** contain provider
credentials, raw provider responses, runtime results, or model output.

## Freeze contract

One Oracle record may be reused by several equivalent cases through
`case_ids`. A frozen record must have:

1. a semantic scope that names every covered case;
2. one or more atomic assertions, each with field, expected value, comparison
   rule, currency, unit, and period where applicable;
3. an `authoritative_receipt_id`, `corroborating_receipt_ids`, and a receipt
   link on every atomic assertion;
4. point-in-time (PIT) evidence showing that the fact was public by the
   asserted availability time;
5. a separate review-ledger decision, with conflicts recorded separately from
   approvals; and
6. a review ledger whose `oracle_file_sha256` matches the oracle file, plus a
   manifest entry with SHA-256 digests for the oracle, receipt, and ledger
   files.

## Machine contract and corroboration

Financial-statement records must resolve `fact_contract_ref` in the evaluator
owned `fact-contracts.financial.v1.json` registry; the suite manifest hashes
that registry together with the Oracle package. Historical-price records may
instead use a complete inline `fact_contract` or a separate historical
registry. The inline form fixes entity, symbol, exchange, form, accession,
period end, duration/instant, consolidation scope, reported currency, unit
scale, and the complete statement schema identifier.
Frozen records additionally carry `origin`, `authoritative_origin_id`, explicit
`same_authoritative_origin`, `corroboration_scope`, `timestamp_precision`, and
`source_lineage` (origin, delivery channel, extraction method, optional chain).
The authority receipt's origin must match the record; every receipt must have
matching `authoritative_origin_id` and `source_lineage.origin_id`.

Financial statements may use one authoritative issuer/regulator **or official
exchange disclosure repository** as the fact source, provided a second delivery
of that same origin or an independent parse corroborates it and distinct
semantic and data reviewers approve it. The two reviewer IDs must differ and
neither may be the case author. Set `corroboration_scope` to one of:

- `same_authoritative_origin_multi_delivery`;
- `same_authoritative_origin_independent_parse`; or
- `independent_fact_source`.

Historical-price v1 is a deliberately narrower baseline: one exact-case, publicly
traceable, source-coherent variant may freeze a numeric case. It makes no claim
of provider authorization, redistribution rights, official-exchange status, or
independent-source reconciliation. If complete sources disagree, retain every
complete source-coherent variant; never average values or splice fields/rows.
Licensed dual-source reconciliation is a future strict-release upgrade.

### Realtime quote contract

Realtime v1 freezes what can remain stable: all 100 query hashes, request and
terminal-status contracts, plus 18 state-only Oracles. The other 82 cases are
`runtime_snapshot`: before a formal run, freeze at least one complete source
receipt with source identity, response hash, quote/capture time, session,
timezone, currency, unit, freshness and tick. One accepted variant contains
one complete source only. Multiple sources may yield alternative accepted
variants; averaging or cross-source field splicing is prohibited. Invalid or
missing capture makes only that case's Data Accuracy `not_scored`.

The current Runner does not generate dynamic receipts. A future value record
may use `live_bracketed` for formal Data Accuracy, but v1 does not require two
sources merely to freeze static contracts or status Oracles.

`replay_fixture` is a deterministic regression artifact only. Its
`evaluation_use` must be `non_formal_replay_only`; it never contributes to
formal `data_accuracy`, Case Pass, or a leaderboard. Provider tool parameters
are excluded from hard scoring in either mode. Do not create quote values,
capture timestamps, or tolerance thresholds until the live admission contract
is actually bound.

### Narrow display-nil normalization

`canonical_zero_from_display_nil` is the only permitted display-to-zero rule.
It is not general whitespace, dash, or `N/A` normalization. A frozen assertion
may use it only when `expected` is numeric `0`, `raw_display` is exactly the
standard en dash (`–`), and its two linked receipts each retain a matching
`display_nil_evidence` entry with that raw glyph, a cell locator, and
`confirmed_nil: true`. Those receipts must represent separate delivery
channels, or a delivery plus an `independent_parser` extraction. The review
ledger must additionally contain a `canonical_zero_approvals` entry from an
approved data reviewer for that assertion. Any other glyph, `N/A`, blank, or
unapproved normalization remains unscoreable and must not be converted to 0.

An as-displayed, non-numeric nil uses a different and stricter representation:
`expected: null`, `comparison: exact`, and the literal `raw_display` glyph.
Two receipt entries must retain that *same* glyph and a cell locator with
`confirmed_nil: false`, from distinct official delivery channels. It remains a
non-numeric fact; `null` may never use `numeric_tolerance` or the canonical-zero
rule.

Both legacy JSON arrays and package objects (`{"oracles": [...]}` and
`{"receipts": [...]}`) are accepted while candidates are being assembled.
The validator recursively discovers market subdirectories. `evidence_collected`
is useful progress only: it prints `INCOMPLETE` and is never scoreable or a
validator `PASS`.

A frozen suite has exactly one `outputs/<suite>/manifest.json`, with
`status: frozen`, a matching suite name, timezone-aware ISO timestamps, and
64-character lowercase SHA-256 values for every protected file. Draft,
combined, or package-held manifests are rejected. Package-level review holds
or open conflicts also block every contained record from freezing.

The default historical and financial statement treatment is
`basis: as_reported`: values are scored in the form publicly reported by the
source, not silently restated, back-adjusted, or converted. The default value
comparison is `comparison: exact_normalized`: normalize only representation
(numeric string/number, whitespace, ISO date formatting, and declared unit)
before exact comparison. A different basis or comparison must be explicitly
declared at assertion level and justified in the evidence receipt.

## State machine

```text
draft -> evidence_collected -> under_review -> frozen
                 |                  |             |
                 v                  v             v
              rejected <--------- conflict ------ superseded
```

- `draft`: no scoreable claim.
- `evidence_collected`: both receipts and PIT facts are present, but not yet
  independently reviewed.
- `under_review`: semantic and data reviewers are assessing the record.
- `conflict`: competing evidence or interpretation exists; no data score may
  be emitted.
- `frozen`: all required checks and independent review pass; the manifest hash
  is the immutable release identity.
- `rejected`: the record is invalid or cannot meet the contract.
- `superseded`: retain it for audit but score only against its replacement.

`conflict` and `review` are deliberately different artifacts. A conflict
describes disputed facts or interpretation; a review ledger records who made a
decision and why. Approval may not erase a conflict record.

## Rejection codes

| Code | Meaning |
| --- | --- |
| `MISSING_CASE_SCOPE` | No complete, unique `case_ids` list. |
| `MISSING_ATOMIC_ASSERTION` | No scoreable field-level assertion. |
| `MISSING_PRIMARY_EVIDENCE` | Primary source receipt is absent or unreadable. |
| `MISSING_INDEPENDENT_EVIDENCE` | Independent corroborating receipt is absent. |
| `NON_INDEPENDENT_SOURCES` | Receipts share a source or independence group. |
| `PIT_VIOLATION` | The asserted fact was not public by its availability time. |
| `MISSING_CURRENCY_OR_UNIT` | A monetary or quantity assertion lacks declared scale. |
| `AMBIGUOUS_PERIOD_OR_BASIS` | Reporting period, adjustment basis, or restatement basis is unclear. |
| `UNRESOLVED_CONFLICT` | A conflicting evidence record remains open. |
| `REVIEW_INCOMPLETE` | Required independent review is absent. |
| `MANIFEST_HASH_MISMATCH` | A released file differs from its declared SHA-256. |

## Layout and release rule

```text
schemas/                         JSON contracts
outputs/historical_price/          50 frozen source-coherent numeric cases (55 variants) + 50 frozen state Oracles
outputs/financial_statements/<market>/  frozen records; re-signed ledgers and validated suite manifest make normal-case data contracts scoreable
outputs/realtime_quote/            18 frozen state Oracles + 82 runtime-snapshot contracts
validate_freeze.py               dependency-free structural validator
```

Put a candidate package only in its suite output directory. Run
`python3 validate_freeze.py` before requesting review and again before release.
The validator enforces package-level invariants but does not prove a market
fact; adversarial source review does that work before the record becomes
`frozen`.

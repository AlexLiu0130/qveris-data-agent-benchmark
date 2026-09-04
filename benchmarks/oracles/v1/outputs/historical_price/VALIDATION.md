# Historical-price blocked package validation

Status: **not frozen**. Data Accuracy: **not scored**. This is a blocker package, not a value oracle.

- Candidate source: `benchmarks/candidates/v0.1/historical_price.cases.json`
- Candidate SHA-256: `9ecc5ed1eefe7bab1bd90083a885b8d27f6ef3aa029862e39957b2ee7de2a279`
- Covered Cases: 100 (80 normal, 20 boundary)
- Historical market values stored: 0
- `oracle/v1` value records admitted: 0

## Mutually exclusive primary rejection

| Primary rejection | Count |
|---|---:|
| `blocked_semantic_contract` | 30 |
| `blocked_adjustment` | 12 |
| `blocked_source_license` | 38 |
| `state_oracle_candidate` | 20 |

## Nonexclusive gate overlap

- Entity/market semantic contract missing: 24 normal Cases.
- Monthly-bucket semantic contract missing: 6 normal Cases.
- Forward-adjustment lineage missing: 17 normal Cases.
- Independent-source/license evidence missing: 80 normal Cases.
- Five Cases have both entity/market semantic ambiguity and forward-adjustment requirements; semantic is primary while adjustment remains applicable.

Every boundary Case is a structured state-oracle candidate with both calendar and suite-policy evidence marked missing. No candidate Case is `frozen`, and none is eligible for Data Accuracy scoring.

## Validator contract

`oracles.json` includes an explicit empty `oracles` array for the hardened `validate_freeze.py` discovery path. Blocked Case records are kept separately in `blocked_cases`, preventing synthetic assertions, receipts, contracts, or values from being fabricated merely to satisfy a frozen-record schema.

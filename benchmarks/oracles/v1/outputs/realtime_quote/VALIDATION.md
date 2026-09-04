# Realtime-quote blocked package validation

Status: **not frozen**. Data Accuracy: **not scored**. This is a no-value blocker inventory, not a captured quote Oracle.

- Candidate source: `benchmarks/candidates/v0.1/realtime_quote.cases.json`
- Candidate SHA-256: `2084804b053c52f7efbc001462f42ce84d0645540093bfef4c82b74fcc9035dc`
- Covered cases: 100 candidate cases
- Realtime values, provider captures, and `oracle/v1` value records: 0

## Blocker distribution

| Primary blocker | Count |
| --- | ---: |
| `blocked_reference_snapshot` | 96 |
| `blocked_semantic_status` | 4 |

All 100 cases also retain `blocked_source_license` until two independent licensed providers and permitted benchmark storage/use are documented. The four terminal-status conflicts are RTQ-039, RTQ-046, RTQ-092, and RTQ-098; they require a semantic terminal state before any future quote capture.

## Admission contract

The formal path is `live_bracketed`: before capturing values, bind either a before/after or dual-reference method; then bind staleness, cross-source skew, and tick tolerances. Each receipt must carry provider quote timestamp, evaluator capture time, session, timezone, currency, and unit. The two references must come from distinct licensed providers and independence groups.

`replay_fixture` is separate and explicitly non-formal. It may support deterministic regression but never formal `data_accuracy`, Case Pass, or leaderboard ranking. Candidate provider tool parameters are excluded from hard scoring.

## Validator contract

`oracles.json` has an explicit empty `oracles` array. `blocked_cases` carries exactly the candidate IDs and blocker state, so this package does not synthesize price assertions, quote timestamps, receipts, or thresholds before the live-reference contract is actually fulfilled.

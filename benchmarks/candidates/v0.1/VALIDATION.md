# Candidate v0.1 validation

**Status:** static packaging checks pass; this is not a formal Benchmark.

| Check | Result |
| --- | --- |
| Included cases | 200 / target 300 |
| Historical cases | 100: 80 normal, 20 boundary; CN/HK/US 29/28/28, JP/GB/DE 5/5/5 |
| Financial cases | 100: 80 normal, 20 boundary |
| Case IDs | Unique across the two included suites |
| Expected status | Every value is in the allowed public status set |
| Manifest hashes | Match the two included candidate files |
| Historical data accuracy | `not_scored`: 80 normal data Oracles are `candidate/unverified` |
| Financial data accuracy | `not_scored`: 80 normal data Oracles are `single_source` |

## Formal-use blockers

1. Add and validate 100 `realtime_quote` cases.
2. Independently freeze/review historical normal-case data Oracles, including source, captured-at timestamp, exact values, evidence hash and comparison rules.
3. Replace financial `single_source` coverage with independently validated Oracle evidence.
4. Implement Runner and `get` contracts, including an enforceable QVeris Gateway-only model-call gate.

Until all blockers are resolved, publish no data-accuracy denominator, Case Pass, aggregate accuracy, or leaderboard.

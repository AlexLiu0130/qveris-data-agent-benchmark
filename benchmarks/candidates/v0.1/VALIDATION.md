# Candidate v0.1 validation

**Status:** static packaging checks pass; this is not a formal Benchmark.

| Check | Result |
| --- | --- |
| Included cases | 300 / target 300 |
| Historical cases | 100: 80 normal, 20 boundary; CN/HK/US 29/28/28, JP/GB/DE 5/5/5 |
| Financial cases | 100: 80 normal, 20 boundary; CN/HK/US 29/28/28, JP/GB/DE 5/5/5 |
| Realtime cases | 100 candidate cases from Feishu source revision 15: A2/A3/A4/A5 = 50/20/15/15; no frozen dynamic snapshot |
| Realtime terminal states | 96 have one public GET status: `success` 77, `needs_clarification` 14, `no_data` 1, `unsupported` 4. RTQ-039/046/092/098 are `blocked_semantic_status` because their conditional success/conflict behaviour has no unique terminal score contract; original source statuses are retained only in `authoring_notes` |
| Realtime blocked Oracle package | `benchmarks/oracles/v1/outputs/realtime_quote/` covers 100/100 cases without quote values: 96 `blocked_reference_snapshot`, 4 `blocked_semantic_status`, and all 100 `blocked_source_license` |
| Replay exclusion | The Oracle release validator blocks `replay_fixture` from becoming a formal Data Accuracy Oracle. Its exclusion from Case Pass and leaderboard output is a normative contract to implement when Runner and Scorer exist; no runtime-enforcement claim is made |
| Financial semantic contracts | 80 normal cases link to 27 frozen fact-contract Oracle records; each binds entity/symbol/market, statement type, fiscal label/end, as-reported and consolidated basis, accounting standard, currency/unit scale, complete-statement schema, as-filed presentation scope, and any field-level unit exceptions |
| Financial boundary cutoff | All 20 boundary cases preserve their expected status and record an unbound `evaluation_cutoff` requirement; they cannot enter formal scoring until the evaluator pins that cutoff |
| Financial query integrity | All 80 normal cases have distinct Query wording; semantically equivalent sibling cases remain mapped to the same fact contract |
| Case IDs | Unique across all three included suites; realtime IDs are continuous RTQ-001 through RTQ-100 |
| Expected status | Every unblocked realtime value is in the public GET status union; blocked realtime cases have an explicit status conflict record instead of an invented value |
| Manifest hashes | Match the included candidate files and financial fact-contract file (`16bc1df58f3620906a6321ef268a899fb5537e60f4ec05ca118e6798bdd5fc93`) |
| Historical data accuracy | `not_scored`: 80 normal data Oracles are `candidate/unverified` |
| Financial data accuracy | 80 normal cases have frozen assertion contracts through 27 Oracle records and 1,198 assertions. The independent review ledgers were re-signed against frozen bytes and the financial suite manifest validates; these normal-case data contracts are scoreable. The 20 boundary cases are state-rule-only and excluded from the numeric data denominator |

## Formal-use blockers

1. Freeze realtime reference snapshots immediately before each formal run; every dynamic realtime case remains `not_scored` until its snapshot, source receipt, capture time and comparison rules are bound. Resolve RTQ-039/046/092/098 into one terminal status or split each case.
2. Independently freeze/review historical normal-case data Oracles, including source, captured-at timestamp, exact values, evidence hash and comparison rules.
3. Bind `evaluation_cutoff` for financial boundary scoring and make the decision rule explicit; these 20 state-rule-only cases remain outside the numeric Data Accuracy denominator.
4. Implement Runner and `get` contracts, including an enforceable QVeris Gateway-only model-call gate, then implement Scorer before emitting runtime metrics, Case Pass, or a leaderboard.

Until all blockers are resolved, publish no data-accuracy denominator, Case Pass, aggregate accuracy, or leaderboard.

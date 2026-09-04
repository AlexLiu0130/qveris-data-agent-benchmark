# Candidate v0.1 validation

**Status:** static packaging checks pass; this is not a formal Benchmark.

| Check | Result |
| --- | --- |
| Included cases | 300 / target 300 |
| Historical cases | 100: 80 normal, 20 boundary; CN/HK/US 29/28/28, JP/GB/DE 5/5/5 |
| Financial cases | 100: 80 normal, 20 boundary; CN/HK/US 29/28/28, JP/GB/DE 5/5/5 |
| Realtime cases | 100 candidate cases from Feishu source revision 15: A2/A3/A4/A5 = 50/20/15/15; static request contracts are frozen, but no dynamic quote value is stored |
| Realtime terminal states | All 100 have one public GET status: `success` 82, `needs_clarification` 14, `no_data` 1, `unsupported` 3. RTQ-039/046/092/098 use `success`: their source conditions are response-validation requirements, not a competing terminal status. RTQ-076 is a dynamic success case. RTQ-049 freezes entity-first clarification priority. |
| Realtime layered Oracle package | `benchmarks/oracles/v1/outputs/realtime_quote/` freezes 18 state-only Oracles and 100 static request contracts. The other 82 cases require a per-run source-coherent snapshot; a failed capture makes only that case's Data Accuracy `not_scored`. |
| Replay exclusion | The Oracle release validator blocks `replay_fixture` from becoming a formal Data Accuracy Oracle. Its exclusion from Case Pass and leaderboard output is a normative contract to implement when Runner and Scorer exist; no runtime-enforcement claim is made |
| Financial semantic contracts | 80 normal cases link to 27 frozen fact-contract Oracle records; each binds entity/symbol/market, statement type, fiscal label/end, as-reported and consolidated basis, accounting standard, currency/unit scale, complete-statement schema, as-filed presentation scope, and any field-level unit exceptions |
| Financial boundary cutoff | All 20 boundary cases preserve their expected status and record an unbound `evaluation_cutoff` requirement; they cannot enter formal scoring until the evaluator pins that cutoff |
| Financial query integrity | All 80 normal cases have distinct Query wording; semantically equivalent sibling cases remain mapped to the same fact contract |
| Case IDs | Unique across all three included suites; realtime IDs are continuous RTQ-001 through RTQ-100 |
| Expected status | Every unblocked realtime value is in the public GET status union; blocked realtime cases have an explicit status conflict record instead of an invented value |
| Manifest hashes | Match the included candidate files and financial fact-contract file (`16bc1df58f3620906a6321ef268a899fb5537e60f4ec05ca118e6798bdd5fc93`) |
| Historical data accuracy | 50 道数值题已冻结，覆盖 55 个公开可追溯、来源一致的完整变体；50 道状态 Oracle 不进入数据准确率分母。冲突变体保留，不平均或跨来源拼接；v1 不声称授权或官方来源 |
| Financial data accuracy | 80 normal cases have frozen assertion contracts through 27 Oracle records and 1,198 assertions. The independent review ledgers were re-signed against frozen bytes and the financial suite manifest validates; these normal-case data contracts are scoreable. The 20 boundary cases are state-rule-only and excluded from the numeric data denominator |

## Formal-use blockers

1. Before each formal run, freeze a source-coherent realtime receipt for every dynamic case. A receipt binds one complete source's value and source/time/session/currency/unit/hash/freshness/tick metadata. Multiple sources create accepted alternative variants only: never average or splice fields. Runner does not yet generate these receipts.
2. Bind `evaluation_cutoff` for financial boundary scoring and make the decision rule explicit; these 20 state-rule-only cases remain outside the numeric Data Accuracy denominator.
3. Implement Runner and `get` contracts, including an enforceable QVeris Gateway-only model-call gate, then implement Scorer before emitting runtime metrics, Case Pass, or a leaderboard.

Until all blockers are resolved, publish no data-accuracy denominator, Case Pass, aggregate accuracy, or leaderboard.

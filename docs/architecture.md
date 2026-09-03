# Architecture

```text
Benchmark case (query + frozen oracle)
                 |
                 v
Runner cell: agent_variant x get_variant x case x trial
                 |
                 | one Agent; no Search/Inspect
                 v
Public get (one execution, one structured response)
                 |
                 | internal model calls: QVeris Gateway only (required)
                 v
Response: status + resolved_request + data + meta
                 |
                 v
Scorer: semantic accuracy, data accuracy, E2E latency, token usage
```

## Responsibility boundaries

| Component | Owns | Does not own |
| --- | --- | --- |
| Benchmark | Natural-language cases, independent Oracles, scope and versioning | Agent implementation or data-provider selection |
| Runner | One-cell execution, timing and safe run records | Internal `get` traces, Oracle creation, score repair |
| `get` | One public structured response for one Query | Its own scoring or public reasoning trace |
| Gateway | Actual internal model receipt and fixed model configuration | Benchmark result calculation |
| Scorer | Deterministic metric calculation from response, run record and frozen Oracle | Missing Oracle evidence or fallback data |

Candidate v0.1 contains 300 cases: 100 each for realtime quote, historical price and financial statements. The 80 financial normal cases are covered by 27 frozen Oracle records and 1,198 assertions; their independent review ledgers were re-signed and their suite manifest validated, so their data contracts are scoreable. The 20 financial boundary cases remain state-rule-only and outside the numeric Data Accuracy denominator. Runner and Scorer are not implemented, so this is not a claim of emitted metrics, Case Pass, or ranking. The realtime candidate has a matching 100-case blocked Oracle package at `benchmarks/oracles/v1/outputs/realtime_quote/`: it contains no numeric quote values, with 96 cases blocked on reference snapshots and four on terminal-status semantics; every case also remains blocked on source licensing.

The Oracle release validator blocks `replay_fixture` from becoming a formal Data Accuracy Oracle. Its exclusion from Case Pass and leaderboard output is a normative contract to implement when Runner and Scorer exist; this repository must not claim runtime enforcement before then.

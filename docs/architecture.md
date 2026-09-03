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

Candidate v0.1 contains historical and financial cases only. Their data Oracles are not frozen, so data accuracy and formal ranking remain blocked until independent evidence is reviewed and versioned outside this repository's raw-result paths.

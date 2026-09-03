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
| Runner | One-cell execution, timing, versioned variant identity, and trusted-adapter discipline evidence | Internal `get` traces, Oracle creation, score repair |
| `get` | One public structured response for one Query | Its own scoring or public reasoning trace |
| Gateway | Actual internal model receipt and fixed model configuration | Benchmark result calculation |
| Scorer | Deterministic metric calculation from response, run record and frozen Oracle | Missing Oracle evidence or fallback data |

Candidate v0.1 contains historical and financial cases only. Their data Oracles are not frozen, so data accuracy and formal ranking remain blocked until independent evidence is reviewed and versioned outside this repository's raw-result paths.

## Implementation status

The local deterministic Runner, Scorer, and loopback read-only Arena HTTP/SSE adapter are implemented in `src/qveris_benchmark/`. They enforce the one-cell execution journal, canonical public response contract, frozen scoring bindings, versioned agent/get/model identities, and server-side score projection. Runtime evidence is only a trusted local adapter assertion: it cannot prove a real runtime, and a real Gateway/Provider still needs an independent gate. They do not implement a real GET Provider, frozen three-suite/300-case input, formal ranking operation, authentication/tenancy, or production deployment.

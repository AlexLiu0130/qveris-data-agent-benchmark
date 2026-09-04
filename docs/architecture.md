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

Candidate v0.2 is the recommended query-quality revision. It keeps 100 cases per suite while moving financial queries to 1–6 directly reported fields from one company, one statement and one period (88 success / 5 clarification / 7 no-data), resolving ordinary historical issuer names by a common listing default (82 success / 2 clarification / 6 no-data / 10 unsupported), and using 90 realtime runtime-snapshot contracts plus 10 frozen state contracts. Historical multi-market interpretations are complete, source-coherent alternative variants, never averages or field splices. `v0.1` candidates and Oracle v1 remain immutable baselines; v2 manifests bind inherited financial assertions and historical evidence hashes.

## Implementation status

The local deterministic Runner, Scorer, and loopback read-only Arena HTTP/SSE adapter are implemented in `src/qveris_benchmark/`. They enforce the one-cell execution journal, canonical public response contract, frozen scoring bindings, versioned agent/get/model identities, and server-side score projection. Runtime evidence is only a trusted local adapter assertion: it cannot prove a real runtime, and a real Gateway/Provider still needs an independent gate. They currently use an independent Run Manifest plus `oracle-bundle/v1`; no candidate/Oracle-to-Runner compilation layer exists, and no real QVeris Gateway or GET Provider is connected. Consequently, the three-suite/300-case benchmark has emitted no formal evaluation, Case Pass, or ranking. Authentication/tenancy and production deployment are also out of scope.

The benchmark canonical metric is `end_to_end_latency`; the current Runner field is `e2e_latency`. An adapter is still required, so the metric names must not be described as unified.

The Oracle release validator blocks `replay_fixture` from becoming a formal Data Accuracy Oracle. Its exclusion from Case Pass and leaderboard output is a normative contract; runtime enforcement must be verified separately.

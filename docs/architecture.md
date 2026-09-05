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
| Gateway | Actual internal model receipt and explicit per-Variant model configuration | Benchmark result calculation or model selection |
| Scorer | Deterministic metric calculation from response, run record and frozen Oracle | Missing Oracle evidence or fallback data |

Candidate v0.2 is the recommended query-quality revision. It keeps 100 cases per suite while moving financial queries to 1–6 directly reported fields from one company, one statement and one period (88 success / 5 clarification / 7 no-data), resolving ordinary historical issuer names by a common listing default (82 success / 2 clarification / 6 no-data / 10 unsupported), and using 90 realtime runtime-snapshot contracts plus 10 frozen state contracts. Historical multi-market interpretations are complete, source-coherent alternative variants, never averages or field splices. `v0.1` candidates and Oracle v1 remain immutable baselines; v2 manifests bind inherited financial assertions and historical evidence hashes.

## Implementation status

The local deterministic Runner, Scorer, and loopback read-only Arena HTTP/SSE adapter are implemented in `src/qveris_benchmark/`. They enforce the one-cell execution journal, canonical public response contract, frozen scoring bindings, versioned agent/get/model identities, and server-side score projection. `v2_compiler` compiles candidate v0.2 and Oracle v2 into a 300-case Run Manifest template plus `oracle-bundle/v2`; a supplied Variant identity and realtime reference contract turn that template into a runnable v2 Manifest. `oracle-bundle/v1` remains legacy-compatible, while public responses may be `get-response/v1` or Suite-normalized `get-response/v2`.

The static runtime catalog now contains 113 market/scenario entries; 86 are connected to fixed routes (financial 33, historical 29, realtime 24). The exact non-dispatch boundaries are maintained in [`get-route-coverage.md`](get-route-coverage.md). Model choice comes only from the explicit Variant config, so three model variants can be run without a Terra-only runtime lock. Oracle data remains parent-side and is never supplied to public `get`. Provider-direct schema and single-Tool path checks are wired; `sandbox_get_entry`/broker also passed historical and Alpha-pointer full JSONL model→Tool→host-bounded-download flow while the image remained network-free and Oracle-isolated. Six representative post-fix live requests succeeded with one model and one Tool each: three AAPL FY2024 statement fields, CN/HK daily bars, and 7203.T FY2024 revenue. The formal 600-cell evaluation has not run, so neither this catalog nor those checks establish 86-route acceptance, Case Pass, ranking, tenancy, or production readiness.

The benchmark, Runner, Scorer, and Arena use the canonical public metric `end_to_end_latency`. Legacy policy input may use `e2e_latency`, but projections and rankings only emit the canonical name.

The Oracle release validator blocks `replay_fixture` from becoming a formal Data Accuracy Oracle. Its exclusion from Case Pass and leaderboard output is a normative contract; runtime enforcement must be verified separately.

# Architecture: QVeris Data Agent Benchmark

## Purpose and boundary

This repository evaluates a deliberately narrow data-delivery path. The final benchmark target is 100 cases in each of three suites:

1. realtime quote;
2. historical price;
3. financial statement.

The present implementation is not that benchmark. It is a three-case offline replay smoke plus a bounded realtime Tool pilot. No production deployment, user-facing Kimi integration, three-domain live selection, 300-case oracle set, or real-model baseline is complete.

The only allowed request path is:

~~~text
Kimi/user input
  → one semantic-model request returning SemanticPlanReceipt
  → deterministic SemanticPlan validation
  → fake replay connector, or the separately controlled paid Execute script
  → validated structured result and score record
~~~

Search and Inspect are construction-time evidence activities. They are never run by the Agent during a benchmark request. The Agent cannot search, inspect, execute more than one Tool, create a workflow, calculate/analyse/forecast, or call a model after data retrieval.

## Runtime components

| Component | Contract |
|---|---|
| Semantic Agent | Makes one model request and returns SemanticPlanReceipt only: plan plus unmodified raw_usage. It never receives QVeris credentials or provider Tool IDs, and does not time, calculate token/cost or score. |
| External Harness validation | Strictly parses and validates receipt.plan status, semantic fields, alias and typed request against the frozen Manifest. Invalid or non-READY plans do not execute. |
| Fixed-alias connector | Resolves an allowed alias to its QVeris Tool ID. Core runner replay always uses the fake connector; QVeris Execute is only in the controlled paid script. |
| External Harness measurement/scoring | Measures agent_call_ms, connector and e2e time; derives usage/token cost policy from raw_usage; performs fixture self-check or eligible semantic/data scoring and records the outcome. |

SemanticPlanReceipt has exactly plan and raw_usage. The plan uses only READY, CLARIFY and REJECT. A READY plan names exactly one alias and its request. CLARIFY and REJECT do not reach QVeris Execute. The external validation layer is deterministic; it must not coerce a type, guess missing parameters, switch aliases, or turn a rejected plan into a call.

## QVerisGet public contract

QVerisGet is the current minimal business-facing interface, not a benchmark template. Its request contract is:

~~~text
get(query, request_id, idempotency_key)
~~~

query must be a non-empty, control-character-free string within the interface limit. request_id and idempotency_key are safe opaque identifiers. Its entire public result contract is:

~~~text
{request_id, status, tool_alias, payload, message}
~~~

status is one of SUCCESS, EMPTY, BLOCKED, FAILED, UNCERTAIN, CLARIFY, REJECT or SEMANTIC_ERROR. The public envelope intentionally excludes all metrics and execution internals: receipt/plan, usage, token, cost, latency, call count, Tool ID, idempotency key, headers, secrets and oracle data.

A READY request has one Agent call and at most one connector call. CLARIFY, REJECT and SEMANTIC_ERROR each have one Agent call and zero connector calls. The private trace_sink receives receipt, connector result, reason and call counts for the external Harness; it must not change the public result if it fails.

Agent and connector must share the exact same runtime Manifest instance. At QVerisGet construction, each response schema must be typed and closed recursively: every object has additionalProperties=false and every array has typed items. Schema keys that name or segment a secret, authorization, credential, token, cookie, header, key, Tool ID, execution ID or idempotency value are rejected. The underlying Connector LiveTransport enforces a 1 MiB maximum response; QVerisGet currently rejects LiveTransport before any request.

## QVerisGet activation boundary

An HTTPS-allowlisted live SemanticAgent may be paired with a fake replay connector for semantic integration only. This lets an external Harness observe a real model receipt without allowing a QVeris Tool call; it is not QVeris live-ready.

Real QVeris activation remains deferred until a fixed Tool, an explicitly authorized live adapter and their separate verification are available. Post-processing, transforms and Agent fallback are also deferred: the current contract returns the validated payload or controlled status only.

## Runtime Manifest versus paid approval artifact

The runtime Manifest is a frozen tool-manifest.v1 mapping from a benchmark-facing alias to a QVeris Tool ID plus its allowed domain, request schema and response expectation. The model sees the benchmark alias contract, not live credentials or arbitrary Tool methods.

Paid approval artifacts (approved runtime plan/manifest and approval digest) are a separate, non-interchangeable contract. They bind a specific external pilot case, approved parameters, budget and plan hash; they are not runtime Manifest entries and cannot be loaded as them. The current replay uses fake transports and does not issue an external POST.

The paid script defaults to dry-run. An external POST is possible only with --execute and an approval digest that is both outside the repository and a regular owner-only 0600 file whose contents match the frozen plan hash. This is the only QVeris live path described here; it is not a core runner mode.

## Replay versus live

### Replay

The bundled smoke constructs a fake model transport, a fake replay transport and frozen fixture/oracle responses. It protects the replay boundary by rejecting the ordinary live model transport and live connector. Each smoke record has outcome not_scored_oracle with a self_check value: self_check=pass means the fixture path is internally consistent, never that the data is accurate or that the case succeeded. Replay establishes only local contract behaviour: plan parsing, deterministic validation, fixed alias routing, response shape, self-check and result serialization.

The sole real-model allowance is explicit mode model_live_replay_data. It may call a model while retaining replay data and the fake connector; it cannot execute QVeris. The ordinary stdlib model transport rejects a profile unless its MODEL_API_BASE is in the explicit MODEL_API_BASE_ALLOWLIST.

### Live

Live means a real QVeris Execute request and possible paid external effects. It is only attempted by the paid script described above and requires current, per-Tool evidence before use:

- Inspect evidence for the actual Tool contract;
- explicit authorization for the intended request;
- a verified business-success response and receipt;
- observed cost; and
- as-of/provenance evidence sufficient for the intended comparison.

A static catalog entry, a Search/Inspect response, HTTP success alone, test success or replay output is not live evidence. Until all five conditions are met for a Tool and the corresponding case contract is frozen, that Tool is not eligible for benchmark live scoring.

## Scoring contract

The four benchmark metrics are fixed names with separate meanings:

| Metric | Definition | Boundary |
|---|---|---|
| semantic_exact | External Harness compares receipt.plan status, semantic slots, alias and arguments to the frozen case expectation. | A correct CLARIFY/REJECT can score semantically when it is the expected result. |
| data_accuracy | Oracle comparison of structured response fields according to the case rule, scored only by a future live runner with independent_source. | Core fake replay is always not_scored; smoke uses fixture_response_match and self_check, not data accuracy. |
| token_usage | External Harness derives prompt, completion and total tokens from receipt.raw_usage. | Missing usage is unknown; token cost is unknown unless an approved Harness pricing policy exists. |
| e2e_ms | External Harness records monotonic end-to-end elapsed time. | agent_call_ms and connector_ms are phase measurements; deterministic validation may be separately measured. Replay and live figures remain separate. |

For realtime cases, a future comparable oracle must record capture time, session, as-of fields and the predeclared comparison window/tolerance. Historical and financial cases need frozen payload/oracle, period, unit/currency and provenance rules. These are unfinished benchmark assets, not properties inferred from a Tool name.

## Tool selection rule

Tool selection is a construction-time decision, not a runtime agent action. Candidates first pass an accuracy gate: input/output schema, domain semantics, provenance, as-of handling, authorization and a comparability path must be evidenced. Only candidates that pass are compared on a Pareto frontier for measured latency and reliability; a slower but more reliable candidate, or a faster but less reliable candidate, may both remain viable.

There is no fixed threshold or declared winner in this repository. In particular, Finnhub is not called “best”; the present evidence is insufficient to rank it against alternatives.

## Current v3–v5 evidence and limitation

The v3 realtime pilot freezes alias rt_us_finnhub_quote_protocol_v3 to Tool ID finnhub_io_api.stock.quote and protocol qveris.execute.parameters.v1. The local review records one corrected-protocol valid business receipt.

This only establishes a limited schema-qualified pilot observation. The review records missing or unproven response semantics for symbol, source, session, currency and timestamp meaning, so accuracy and freshness remain blocked. One observation cannot establish reliability, latency ranking, cross-provider superiority, an oracle, or a three-domain selection.

The v4 Tiingo historical EOD pilot and v5 FMP as-reported income-statement pilot are likewise schema-qualified / accuracy-unverified. Their recorded responses do not establish independent oracle accuracy, provenance completeness, freshness or reliable ranking. The v5 Alpha Vantage income-statement response is incompatible with V1 data delivery because the allowed single Tool response does not embed the required financial-statement fields and V1 disallows an additional GET.

Primary local evidence:

- [approved v3 run plan](../benchmarks/pilot/approved-runtime-plan-v3.json);
- [approved v3 manifest](../benchmarks/pilot/approved-runtime-manifest-v3.json);
- [pilot independent review](tool-selection/pilot-plan-review.md).
- [v4 Tiingo pilot](tool-selection/historical-pilot-v4.md);
- [v5 financial pilot](tool-selection/financial-pilot-v5.md).

## Configuration and secrets

The template [.env.example](../.env.example) names QVERIS_API_KEY, MODEL_API_BASE, MODEL_API_BASE_ALLOWLIST, MODEL_API_KEY, MODEL_ID and MODEL_REASONING_EFFORT. The offline replay CLI does not automatically load it and should run without any secret. A real model profile must freeze API base, model ID and reasoning/settings in the evaluated run record; its base must be on MODEL_API_BASE_ALLOWLIST, and it must never cause the model to generate or see credentials, headers, routes or provider Tool IDs.

Never commit .env files, API keys, OAuth tokens, raw provider responses, receipts, raw result artifacts or paid approval artifacts. The repository ignores approved pilot artifacts and artifacts/ output by design. Do not run a paid Execute script or add a credential merely to reproduce the offline smoke.

## Minimal commands

~~~bash
# Offline, no external model or QVeris Tool call.
PYTHONPATH=src python3.11 -m qveris_benchmark \
  benchmarks/pilot/cases.example.jsonl \
  --results /private/tmp/qveris-benchmark-replay.jsonl

# Local contract tests.
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
~~~

## Next accepted work, not a claim of completion

1. Freeze a 30-case pilot and then the three 100-case suites, with family-separated dev/selection/holdout sets.
2. Build independent historical/financial oracles and realtime comparison contracts.
3. Apply the accuracy gate and Pareto selection process to all three domains.
4. Establish a Kimi real-model profile/token/latency baseline under a frozen benchmark version.

Each step needs evidence and explicit approval before a live or paid action. Until then, report the relevant part as blocked or degraded.

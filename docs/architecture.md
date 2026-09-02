# Architecture: QVeris Data Agent Benchmark

## Purpose and boundary

This repository evaluates a deliberately narrow data-delivery path. The final benchmark target is 100 cases in each of three suites:

1. realtime quote;
2. historical price;
3. financial statement.

The present implementation is not that benchmark. It is a three-case offline replay smoke plus a legacy three-alias QVerisGet compatibility artifact based on limited v6–v10 curation evidence. The formal design is a `market × scenario` Registry; it is not yet wired into runtime. No production deployment, user-facing Kimi integration, scenario-level live selection, 300-case oracle set, or real-model baseline is complete.

The only allowed request path is:

~~~text
Kimi/user input
  → one semantic-model request returning SemanticPlanReceipt
  → deterministic SemanticPlan validation
  → fake replay connector, or a future permit-gated legacy validation adapter
  → validated structured result and score record
~~~

Search and Inspect are construction-time evidence activities. They are never run by the Agent during a benchmark request. Historical paid-pilot execution is curation/admin work, not the get runtime path. The Agent cannot search, inspect, execute more than one Tool, create a workflow, calculate/analyse/forecast, or call a model after data retrieval.

## Runtime components

| Component | Contract |
|---|---|
| Semantic Agent | Makes one model request and returns SemanticPlanReceipt only: plan plus unmodified raw_usage. It never receives QVeris credentials or provider Tool IDs, and does not time, calculate token/cost or score. |
| External Harness validation | Current code strictly parses and validates receipt.plan status, legacy alias and typed request. The target contract validates `scenario_id` and standard parameters against the Registry. Invalid or non-READY plans do not execute. |
| Deterministic Router | Target: resolves an allowed `market × scenario_id` to a frozen Tool. Current code retains a legacy fixed-alias connector only for replay and narrow real-call validation. |
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

A READY request has one Agent call and at most one connector call. CLARIFY, REJECT and SEMANTIC_ERROR each have one Agent call and zero connector calls. The private trace_sink receives only a safe observation: plan status/domain/tool_alias summary, allowlisted numeric token usage, connector outcome/reason code and call counts. It never receives plan message/parameters, provider payload, billing, execution identifier, Tool ID or idempotency key, and it must not change the public result if it fails.

The target Agent and Router share the exact same frozen `market × scenario` Registry. Historical narrow-candidate records retain `quote.realtime.v1` (Finnhub), `price.history.v1` (Tiingo) and `statement.financial.v1` (FMP standard income statement) as compatibility and real-call evidence. Their envelope/public schema, receipt paths and fixed cost are useful call-validation controls, but they are not the current delivery contract and must not be read as one Tool per data direction.

The Connector validates the complete QVeris envelope once. On business success, QVerisGet verifies a non-empty receipt execution identifier and exact actual `$.cost`, then projects only the declared `public_data_path` fields. The public payload therefore never carries the provider envelope, billing, receipt, cost, execution identifier, headers or credentials. Any receipt, cost, path or schema failure returns FAILED after that single call.

## QVerisGet activation boundary

An HTTPS-allowlisted live SemanticAgent may be paired with a fake replay connector for semantic integration only. This lets an external Harness observe a real model receipt without allowing a QVeris Tool call; it is not QVeris live-ready.

If legacy narrow-call validation is restored, its target design requires a local permit: an owner-only, regular, non-symlink 0600 file outside normal source control whose entire content equals the corresponding frozen validation-record digest. That future adapter must reject a missing or mismatched permit before any request and carry neither a credential nor a Tool override. Formal runtime activation waits for the scenario Registry and its per-market Tool selection. Post-processing, transforms and Agent fallback remain deferred.

## Legacy narrow-candidate records versus paid approval artifact

Historical narrow-candidate records are alias-to-Tool validation artifacts with their request schema, full provider envelope, public projection, receipt paths and fixed cost. They do not select the formal runtime Tool. The future Registry, rather than the model, will map market/scenario/standard parameters to a frozen Tool; the model never sees credentials, Tool IDs, response internals or arbitrary methods.

Paid approval artifacts (approved runtime plan/manifest and approval digest) are a separate, non-interchangeable contract. They bind a specific external pilot case, approved parameters, budget and plan hash; they are not runtime Manifest entries and cannot be loaded as them. The current replay uses fake transports and does not issue an external POST.

The paid script defaults to dry-run. An external POST is possible only with --execute and an approval digest that is both outside the repository and a regular owner-only 0600 file whose contents match the frozen plan hash. It is independent Tool curation/admin work, not the get runtime path or a core runner mode.

## Replay versus live

### Replay

The bundled smoke constructs a fake model transport, a fake replay transport and frozen fixture/oracle responses. It protects the replay boundary by rejecting the ordinary live model transport and live connector. Each smoke record has outcome not_scored_oracle with a self_check value: self_check=pass means the fixture path is internally consistent, never that the data is accurate or that the case succeeded. Replay establishes only local contract behaviour: plan parsing, deterministic validation, fixed alias routing, response shape, self-check and result serialization.

The sole real-model allowance is explicit mode model_live_replay_data. It may call a model while retaining replay data and the fake connector; it cannot execute QVeris. The ordinary stdlib model transport rejects a profile unless its MODEL_API_BASE is in the explicit MODEL_API_BASE_ALLOWLIST.

### Live

Live means a real QVeris Execute request and possible paid external effects. If restored, the legacy validation adapter must be permit-gated for narrow validation; paid pilot execution remains a separate curation/admin path. Formal runtime use requires current, per-market/per-scenario evidence before a Tool is frozen:

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

## Historical v3–v5 archive (superseded, non-active)

The v3 realtime pilot freezes alias rt_us_finnhub_quote_protocol_v3 to Tool ID finnhub_io_api.stock.quote and protocol qveris.execute.parameters.v1. This is superseded historical curation evidence, not an active runtime alias or live-ready claim. The local review records one corrected-protocol valid business receipt.

This only establishes a limited schema-qualified pilot observation. The review records missing or unproven response semantics for symbol, source, session, currency and timestamp meaning, so accuracy and freshness remain blocked. One observation cannot establish reliability, latency ranking, cross-provider superiority, an oracle, or a three-domain selection.

The v4 Tiingo historical EOD pilot and v5 FMP as-reported income-statement pilot are likewise superseded historical archive, schema-qualified / accuracy-unverified. Their recorded responses do not establish independent oracle accuracy, provenance completeness, freshness or reliable ranking. The v5 Alpha Vantage income-statement response is incompatible with V1 data delivery because the allowed single Tool response does not embed the required financial-statement fields and V1 disallows an additional GET. None of these legacy aliases is a formal runtime selection.

Primary local evidence:

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

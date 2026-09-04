# Public GET response contract

`qveris_benchmark.response_contract` defines the sole scoreable public response at `PublicGetResult.public_response`; it retains the existing `get-response/v1` version label.

Every `success`/`partial` envelope contains exactly `schema_version`, `status`, `resolved_request`, `data`, `as_of`, `source`, `clarification: null`, `terminal_reason: null`, and measured `meta.usage`. `resolved_request` is exactly `{suite, accepted_variant_id}`.

- Financial: `data.kind: financial_statement`; `facts` is keyed by normalized ASCII assertion ID (`is-002` to `is_002`), and each direct field has `assertion_id`, original disclosure `field`, numeric-string `value`, `period`, `currency`, `unit`, and `nil`. The original field identifier may include dots and Unicode, but not control characters.
- Historical: `data.kind: historical_price`; `accepted_variant_id`, `instrument`, `interval`, `adjustment`, and `bars`. `data.accepted_variant_id` must equal `resolved_request.accepted_variant_id`, binding the complete market/source answer variant. Bar keys are `dYYYYMMDD`, `wSTART_END`, or `mYYYYMM`, repeated as `period_key`. Different market/source interpretations are complete alternate variants, never mixed fields.
- Realtime: `data.kind: realtime_quote`; `quote.instrument` and `quote.fields.<field> = {value, unit, as_of, nil}`. Every field timestamp equals envelope `as_of`.

State responses have `data: null`: `needs_clarification` requires `clarification`; `unsupported`, `no_data`, and `error` require `terminal_reason`. Raw provider payloads, credentials, execution IDs, arrays, non-finite pseudo-numbers, and duplicate JSON keys are rejected. `diagnostic=True` is migration-only for the old minimal envelope and is never scoreable.

The [JSON Schema](../benchmarks/schemas/public-get-response.schema.json) is an interchange reference. The stdlib Python validator is normative for normalized-ID, timestamp, bar-key, raw-field, duplicate-key, and token-total checks.

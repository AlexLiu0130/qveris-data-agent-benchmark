# Realtime-quote v1 layered freeze validation

Status: **partially frozen**. The static layer is frozen; dynamic quote values
are frozen per formal run.

- Candidate source: `benchmarks/candidates/v0.1/realtime_quote.cases.json`
- Candidate SHA-256: `6803e0cbc12a1b40b541aaadf70cbda4c0c6e646a4c185a56a1cc1d4b16577ec`
- Static registry: `benchmarks/oracles/v1/fact-contracts.realtime.v1.json`
- Static registry SHA-256: `793288d97b14d2ca51bfcbf04ec6937e57f695b1758b6b3ec61e2afa5f8a4b64`
- Covered cases: 100 / 100

## Frozen static layer

| Contract | Count | Data Accuracy treatment |
| --- | ---: | --- |
| State-only Oracle | 18 | Not applicable; semantic/status is scoreable now |
| Runtime dynamic request contract | 82 | `runtime_capture_required` |

All 100 contracts bind the untouched raw-query SHA-256 and one terminal status.
The dynamic partition is the 82 `success` cases; the state partition is 14
`needs_clarification`, 1 `no_data`, and 3 `unsupported` cases. RTQ-049 uses
entity-first clarification priority. RTQ-039/046/092/098 and RTQ-076 are
dynamic `success` cases.

## Per-run dynamic receipt contract

Every dynamic case requires one complete, source-coherent receipt before its
Data Accuracy can be scored. It records source identity and response hash,
provider quote timestamp, evaluator capture time, market session, timezone,
currency, unit, freshness and tick. One accepted variant is one source's
complete result. Several sources yield alternative accepted variants only;
never average or splice fields across sources. An invalid or absent capture
marks only that case Data Accuracy `not_scored`.

The package contains no quote values or raw provider responses. The current
Runner has no dynamic-receipt generator, so it cannot yet execute the 82
dynamic Data Accuracy paths.

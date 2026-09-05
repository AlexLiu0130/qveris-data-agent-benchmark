"""Explicit offline GET fixture for Runner wiring checks, never a benchmark model."""

from __future__ import annotations

from .run_backend import ExecutionEvidence, PublicGetResult


_VARIANT = {
    "variant_id": "fixture-get",
    "stable_display_order": 1,
    "agent_variant_id": "fixture-agent",
    "agent_version": "fixture-v1",
    "get_variant_id": "fixture-get",
    "get_version": "fixture-v1",
    "model_identifier": "fixture-model",
    "model_version": "fixture-v1",
    "model_config_digest": "f" * 64,
}


class FixtureGetClient:
    """One deterministic, provider-free public GET call per supplied query."""

    def __call__(self, _query: str, *, request_id: str, idempotency_key: str) -> PublicGetResult:
        del request_id, idempotency_key
        response = {
            "schema_version": "get-response/v1",
            "status": "error",
            "data": None,
            "clarification": None,
            "terminal_reason": "fixture_offline",
        }
        evidence = ExecutionEvidence(
            **{name: _VARIANT[name] for name in _VARIANT if name not in {"variant_id", "stable_display_order"}},
            agent_invocations=1,
            tool_executions=1,
            structured_outputs=1,
            tools_used=("get",),
        )
        return PublicGetResult(response, evidence)


def make_client() -> dict[str, object]:
    """Plugin entry point used only when ``run_benchmark.py --fixture`` is set."""
    return {"variant": dict(_VARIANT), "client": FixtureGetClient()}

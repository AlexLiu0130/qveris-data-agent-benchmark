"""Run the bundled offline replay example."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .agent import ModelProfile, SemanticAgent
from .connector import Connector, FakeReplayTransport
from .contracts import AuthMode, Domain
from .manifest import TOOL_MANIFEST_SCHEMA_VERSION, Manifest, ToolManifestEntry
from .runner import BenchmarkRunner, RunMode, load_cases, load_oracle


class ReplayModelTransport:
    """Finite fixture transport for CLI replay; it never opens a network connection."""

    def __init__(self, plans: list[Mapping[str, Any]]) -> None:
        self._plans = iter(plans)

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:  # noqa: ARG002
        return json.dumps({"choices": [{"message": {"content": json.dumps(next(self._plans), separators=(",", ":"))}}]}).encode("utf-8")


def _manifest() -> Manifest:
    schema = {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}
    return Manifest.from_entries(
        [
            ToolManifestEntry("quote", "replay.quote", schema, {"type": "object"}, Domain.REALTIME_QUOTE, AuthMode.BEARER),
            ToolManifestEntry("history", "replay.history", schema, {"type": "object"}, Domain.HISTORICAL_PRICE, AuthMode.BEARER),
            ToolManifestEntry("statement", "replay.statement", schema, {"type": "object"}, Domain.FINANCIAL_STATEMENT, AuthMode.BEARER),
        ],
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline benchmark replay")
    parser.add_argument("cases", nargs="?", default="benchmarks/pilot/cases.example.jsonl")
    parser.add_argument("--results", default="results.jsonl")
    args = parser.parse_args()
    case_path = Path(args.cases)
    cases = load_cases(case_path)
    manifest = _manifest()
    plans = []
    fixtures = {}
    for case in cases:
        semantics = dict(case.expected_semantics)
        plan = {"status": case.expected_status, **semantics}
        if case.expected_status == "READY":
            plan.update({"tool_alias": case.expected_tool_alias, "request": dict(case.expected_arguments)})
            fixtures[manifest.resolve(case.expected_tool_alias or "").tool_id] = load_oracle(case_path.parent, case.oracle_ref)["response"]
        plans.append(plan)
    agent = SemanticAgent(ModelProfile("https://replay.invalid", "gpt-5.6-terra", reasoning_effort="high"), ReplayModelTransport(plans))
    connector = Connector(manifest, FakeReplayTransport(fixtures))
    records = BenchmarkRunner(agent, connector, mode=RunMode.REPLAY_FIXTURE_SELF_CHECK).run_cases(cases, lambda case: load_oracle(case_path.parent, case.oracle_ref), args.results)
    print(json.dumps({"mode": RunMode.REPLAY_FIXTURE_SELF_CHECK.value, "cases": len(records), "chain_self_checks": [record["self_check"] for record in records]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

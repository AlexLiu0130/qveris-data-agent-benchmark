#!/usr/bin/env python3
"""Run the two-case, synthetic financial Runner smoke diagnostic.

This is intentionally an execution-only diagnostic.  It reads the frozen
financial Oracle in memory to make a deterministic fake public GET result; it
does not call a Gateway, Provider, Scorer, or network service.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from qveris_benchmark.benchmark_scorer import SCORER_DIGEST, SCORER_VERSION
from qveris_benchmark.run_backend import (
    ExecutionEvidence,
    PublicGetResult,
    RunBackendError,
    RunService,
    RunStore,
    _digest,
    _variant_contract_digest,
    _variant_identity,
)
from validate_financial_diagnostic_30 import main as validate_selection


SMOKE_CASE_IDS = ("FS-046", "FS-050")
SMOKE_RUN_ID = "financial-diagnostic-runner-smoke-v1"
SINGLE_VARIANT_SMOKE_RUN_ID = "financial-diagnostic-runner-single-variant-smoke-v1"
SELECTION = Path("benchmarks/diagnostics/financial-statements-30.v1.json")
CANDIDATES = Path("benchmarks/candidates/v0.1/financial_statements.cases.json")
REGISTRY = Path("benchmarks/oracles/v1/fact-contracts.financial.v1.json")


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("frozen Oracle path escapes repository") from error
    return path


def _primary_period(contract: dict[str, Any]) -> str:
    presentation = contract.get("presentation_scope", {}).get("primary_period", {})
    value = presentation.get("assertion_period")
    if type(value) is not str or not value:
        raise ValueError("frozen fact contract lacks its primary assertion period")
    return value


def _response_template(candidate: dict[str, Any], contract: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    period = _primary_period(contract)
    atoms = [atom for atom in oracle.get("atomic_assertions", []) if atom.get("period") == period]
    if oracle.get("status") != "frozen" or not atoms:
        raise ValueError("frozen Oracle has no primary-period assertions")
    assertions = []
    for atom in atoms:
        if type(atom.get("field")) is not str or "expected" not in atom:
            raise ValueError("frozen Oracle assertion is malformed")
        assertions.append({"field": atom["field"], "expected": atom["expected"]})
    point = oracle.get("point_in_time", {})
    as_of = point.get("as_of")
    if type(as_of) is not str or not as_of:
        raise ValueError("frozen Oracle lacks as_of")
    intent = candidate.get("expected_intent")
    if type(intent) is not dict:
        raise ValueError("candidate lacks expected intent")
    return {
        "schema_version": "get-response/v1",
        "status": "success",
        "resolved_request": copy.deepcopy(intent),
        "data": {"synthetic_frozen_oracle": {"primary_period": period, "assertions": assertions}},
        "as_of": as_of,
        "source": "synthetic-fake:frozen-oracle:%s" % oracle["oracle_id"],
    }


def _variants(count: int = 2) -> list[dict[str, Any]]:
    result = []
    for order, suffix in enumerate(("a", "b")[:count], start=1):
        identity = {
            "variant_id": "synthetic-financial-%s" % suffix,
            "stable_display_order": order,
            "agent_variant_id": "synthetic-agent-%s" % suffix,
            "agent_version": "synthetic-v1",
            "get_variant_id": "synthetic-public-get-%s" % suffix,
            "get_version": "synthetic-v1",
            "model_identifier": "synthetic-no-model-%s" % suffix,
            "model_version": "synthetic-v1",
            "model_config_digest": _digest({"synthetic": True, "variant": suffix}),
        }
        result.append(identity)
    return result


class FrozenOracleFakeClient:
    """A local adapter that exposes only synthetic public responses."""

    def __init__(self, variant: dict[str, Any], responses: dict[str, dict[str, Any]]) -> None:
        self.variant, self.responses, self.calls = variant, responses, []

    def run(self, query: str, *, request_id: str, idempotency_key: str) -> PublicGetResult:
        if query not in self.responses:
            raise RunBackendError("synthetic client received an unknown query")
        self.calls.append((query, request_id, idempotency_key))
        response = copy.deepcopy(self.responses[query])
        return PublicGetResult(
            response,
            ExecutionEvidence(
                **_variant_identity(self.variant),
                agent_invocations=1,
                tool_executions=1,
                structured_outputs=1,
                tools_used=("get",),
            ),
        )


def _compile_profile(
    root: Path,
    *,
    run_id: str,
    profile: str,
    requested_case_ids: tuple[str, ...],
    variant_count: int = 2,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compile a synthetic diagnostic from the verified selection and frozen Oracle."""
    root = root.resolve()
    validate_selection(root)
    selection = _load(root / SELECTION)
    selected_case_ids = selection.get("selected_case_ids")
    if not isinstance(selected_case_ids, list):
        raise ValueError("diagnostic selection has no selected cases")
    case_ids = requested_case_ids
    if not case_ids or not set(case_ids).issubset(selected_case_ids):
        raise ValueError("requested cases are not in the approved 30-case diagnostic")
    candidates = {item["id"]: item for item in _load(root / CANDIDATES)}
    contracts = {item["fact_contract_id"]: item for item in _load(root / REGISTRY)["contracts"]}
    cases, responses, oracle_bindings = [], {}, []
    for case_id in case_ids:
        candidate = candidates.get(case_id)
        if not isinstance(candidate, dict) or candidate.get("case_type") != "normal" or candidate.get("expected_status") != "success":
            raise ValueError("smoke case is not a frozen normal success case: %s" % case_id)
        contract = contracts.get(candidate.get("fact_contract_ref"))
        if not isinstance(contract, dict) or contract.get("review_status") != "frozen":
            raise ValueError("smoke case lacks a frozen fact contract: %s" % case_id)
        source = contract.get("source_oracle", {})
        oracle_path = _inside(root, source.get("path", ""))
        oracle = next((item for item in _load(oracle_path) if item.get("oracle_id") == source.get("oracle_id")), None)
        if not isinstance(oracle, dict) or oracle.get("status") != "frozen" or case_id not in oracle.get("case_ids", []):
            raise ValueError("smoke case lacks its frozen Oracle: %s" % case_id)
        responses[candidate["query"]] = _response_template(candidate, contract, oracle)
        cases.append({
            "case_id": case_id,
            "suite": "financial_statements",
            "query": candidate["query"],
            "score_case": {"expected_status": ["success"], "oracle_id": oracle["oracle_id"], "case_type": "normal"},
            "diagnostic_binding": {
                "candidate_sha256": _digest(candidate),
                "fact_contract_id": contract["fact_contract_id"],
                "oracle_id": oracle["oracle_id"],
                "oracle_file_sha256": _file_digest(oracle_path),
            },
        })
        oracle_bindings.append({"case_id": case_id, "oracle_id": oracle["oracle_id"], "oracle_file_sha256": _file_digest(oracle_path)})
    variants = _variants(variant_count)
    policy_binding = {"schema_version": "financial-runner-diagnostic-policy-binding/v1", "kind": "synthetic-unscored", "profile": profile}
    oracle_binding = {"schema_version": "financial-runner-diagnostic-oracle-binding/v1", "selection_sha256": _file_digest(root / SELECTION), "oracles": oracle_bindings}
    manifest = {
        "run_id": run_id,
        "mode": "diagnostic",
        "freeze_digest": _digest({"selection": _file_digest(root / SELECTION), "oracle_binding": oracle_binding}),
        "policy": {"version": "financial-runner-diagnostic/v1", "scope": "synthetic-unscored", "profile": profile},
        "timeout_ms": 1000,
        "concurrency": 1,
        "scoring_contract": {
            "policy_digest": _digest(policy_binding),
            "oracle_bundle_digest": _digest(oracle_binding),
            "scorer_version": SCORER_VERSION,
            "scorer_digest": SCORER_DIGEST,
            "variant_contract_digest": _variant_contract_digest(variants),
        },
        "variants": variants,
        "cases": cases,
    }
    return manifest, responses


def compile_smoke(root: Path = ROOT) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compile the two-case smoke subset retained for fast Runner regression checks."""
    return _compile_profile(root, run_id=SMOKE_RUN_ID, profile="smoke", requested_case_ids=SMOKE_CASE_IDS)


def compile_single_variant_smoke(root: Path = ROOT) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compile the smallest synthetic-adapter diagnostic: one case and one variant."""
    return _compile_profile(
        root,
        run_id=SINGLE_VARIANT_SMOKE_RUN_ID,
        profile="single-variant-smoke",
        requested_case_ids=("FS-046",),
        variant_count=1,
    )


def _run_profile(
    root: Path,
    output_root: Path,
    *,
    compiler: Any,
    profile: str,
) -> dict[str, Any]:
    if output_root.exists():
        raise ValueError("output directory already exists; refusing to mix diagnostic artifacts")
    manifest, responses = compiler(root)
    clients = {variant["variant_id"]: FrozenOracleFakeClient(variant, responses) for variant in manifest["variants"]}
    store = RunStore(output_root)
    service = RunService(store, clients)
    service.create_run(manifest)
    first = service.execute(manifest["run_id"])
    calls_after_first = sum(len(client.calls) for client in clients.values())
    resumed = service.execute(manifest["run_id"])
    calls_after_resume = sum(len(client.calls) for client in clients.values())
    events = store.events(manifest["run_id"])  # also validates the entire immutable hash chain
    event_counts = Counter(event["event_type"] for event in events)
    case_count, cell_count = len(manifest["cases"]), len(manifest["cases"]) * len(manifest["variants"])
    if first["internal_status"] != "execution_complete" or first["execution"] != {"total": cell_count, "completed": cell_count, "success": cell_count, "failed": 0, "incomplete": 0, "blocked": 0}:
        raise AssertionError("diagnostic execution did not complete every successful cell")
    if calls_after_first != cell_count or calls_after_resume != cell_count or event_counts != Counter({"dispatch_intent": cell_count, "terminal": cell_count, "run_started": 1, "run_finished": 1}):
        raise AssertionError("Runner did not preserve the one-call resume contract")
    if store.load_manifest(manifest["run_id"]) != manifest:
        raise AssertionError("persisted manifest does not bind to compiled inputs")
    changed = dict(manifest, timeout_ms=1001)
    try:
        store.create(changed)
    except RunBackendError:
        pass
    else:
        raise AssertionError("immutable manifest accepted a changed input")
    dispatches = [event for event in events if event["event_type"] == "dispatch_intent"]
    terminals = [event for event in events if event["event_type"] == "terminal"]
    identities = [_variant_identity(item) for item in manifest["variants"]]
    if any(event.get("variant_identity") not in identities for event in terminals):
        raise AssertionError("terminal identity does not bind to a manifest variant")
    expected_cells = {
        "cell-" + _digest([manifest["run_id"], variant["variant_id"], case["case_id"], 1])[:48]
        for variant in manifest["variants"] for case in manifest["cases"]
    }
    if {event["cell_id"] for event in dispatches} != expected_cells or {event["cell_id"] for event in terminals} != expected_cells:
        raise AssertionError("Runner did not terminalize every compiled diagnostic cell")
    if any(event.get("execution_evidence", {}).get("agent_invocations") != 1 or event["execution_evidence"].get("tool_executions") != 1 or event["execution_evidence"].get("structured_outputs") != 1 or event["execution_evidence"].get("tools_used") != ["get"] for event in terminals):
        raise AssertionError("synthetic execution evidence violates the one-agent one-get contract")
    return {
        "run_id": manifest["run_id"],
        "artifact_root": str(output_root),
        "profile": profile,
        "compiled_cases": [case["case_id"] for case in manifest["cases"]],
        "variant_ids": [variant["variant_id"] for variant in manifest["variants"]],
        "event_counts": dict(sorted(event_counts.items())),
        "client_calls": calls_after_first,
        "resume_additional_calls": calls_after_resume - calls_after_first,
        "journal_hash_chain_valid": True,
        "manifest_immutable": True,
        "usage": "unknown",
        "internal_status": resumed["internal_status"],
        "snapshot_status": resumed["status"],
        "projection_status": resumed["projection_status"],
        "synthetic_only": True,
    }


def run_smoke(root: Path, output_root: Path) -> dict[str, Any]:
    return _run_profile(root, output_root, compiler=compile_smoke, profile="smoke")


def run_single_variant_smoke(root: Path, output_root: Path) -> dict[str, Any]:
    return _run_profile(root, output_root, compiler=compile_single_variant_smoke, profile="single-variant-smoke")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", choices=("smoke", "single-variant-smoke"), default="smoke")
    args = parser.parse_args()
    if args.output:
        output = args.output
    else:
        artifact_root = args.root / "artifacts"
        artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="financial-runner-%s-" % args.profile, dir=artifact_root)) / "run-store"
    runner = run_smoke if args.profile == "smoke" else run_single_variant_smoke
    print(json.dumps(runner(args.root, output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit("FAIL: %s" % error)

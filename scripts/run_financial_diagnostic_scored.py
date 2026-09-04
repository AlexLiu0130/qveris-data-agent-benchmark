#!/usr/bin/env python3
"""Run synthetic, scored financial diagnostic checkpoints without a Provider."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qveris_benchmark.arena_http import make_server
from qveris_benchmark.benchmark_scorer import BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION
from qveris_benchmark.financial_diagnostic import compile_with_digest, digest
from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunService, RunStore, _variant_contract_digest, _variant_identity


VARIANT = {
    "variant_id": "synthetic-financial-v1",
    "stable_display_order": 1,
    "agent_variant_id": "synthetic-agent-v1",
    "agent_version": "synthetic-v1",
    "get_variant_id": "synthetic-public-get-v1",
    "get_version": "synthetic-v1",
    "model_identifier": "synthetic-no-model-v1",
    "model_version": "synthetic-v1",
    "model_config_digest": digest({"synthetic": True, "version": 1}),
}
CHECKPOINTS = (("A", ("FS-001",)), ("B", ("FS-046", "FS-050")), ("C", None))


class SyntheticFinancialClient:
    """Carries frozen assertions into the public response shape used by Scorer."""

    def __init__(self, variant: Mapping[str, Any], cases: Mapping[str, Mapping[str, Any]], oracles: Mapping[str, Mapping[str, Any]]) -> None:
        self.variant, self.calls = dict(variant), []
        self.responses = {
            case["query"]: self._response(case, oracles[case["score_case"]["oracle_id"]])
            for case in cases.values()
        }

    @staticmethod
    def _response(case: Mapping[str, Any], oracle: Mapping[str, Any]) -> dict[str, Any]:
        facts = []
        for assertion in oracle["data_assertions"]:
            fact = {name: assertion[name] for name in ("assertion_id", "currency", "unit", "period")}
            fact["value"] = copy.deepcopy(assertion["expected"])
            if "raw_display" in assertion:
                fact["raw_display"] = assertion["raw_display"]
            facts.append(fact)
        return {
            "schema_version": "get-response/v1",
            "status": "success",
            "resolved_request": copy.deepcopy(case["canonical_request"]),
            "data": {"facts": facts},
            "as_of": "2026-09-03T00:00:00Z",
            "source": "synthetic-frozen-financial-oracle",
        }

    def run(self, query: str, *, request_id: str, idempotency_key: str) -> PublicGetResult:
        if query not in self.responses:
            raise ValueError("synthetic client received an unknown query")
        self.calls.append((query, request_id, idempotency_key))
        response = copy.deepcopy(self.responses[query])
        return PublicGetResult(response, ExecutionEvidence(
            **_variant_identity(self.variant),
            agent_invocations=1,
            tool_executions=1,
            structured_outputs=1,
            tools_used=("get",),
        ))


def _profile(compiled: Mapping[str, Any], name: str, case_ids: tuple[str, ...] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    all_cases = {case["case_id"]: case for case in compiled["run_config"]["cases"]}
    ids = tuple(all_cases) if case_ids is None else case_ids
    if not ids or len(ids) != len(set(ids)) or any(case_id not in all_cases for case_id in ids):
        raise ValueError("checkpoint cases are not compiled financial diagnostic cases")
    cases = [copy.deepcopy(all_cases[case_id]) for case_id in ids]
    oracles = {case["score_case"]["oracle_id"]: copy.deepcopy(compiled["oracle_bundle"]["oracles"][case["score_case"]["oracle_id"]]) for case in cases}
    bundle = {"schema_version": compiled["oracle_bundle"]["schema_version"], "oracles": oracles}
    config = copy.deepcopy(compiled["run_config"])
    config.update({
        "run_id": "financial-diagnostic-%s-synthetic-v1" % name.lower(),
        "freeze_digest": digest({"compiled_digest": compiled["compiled_digest"], "case_ids": ids}),
        "cases": cases,
        "variants": [copy.deepcopy(VARIANT)],
    })
    config["scoring_contract"] = {
        "policy_digest": compiled["scoring_policy_digest"],
        "oracle_bundle_digest": digest(bundle),
        "scorer_version": SCORER_VERSION,
        "scorer_digest": SCORER_DIGEST,
        "variant_contract_digest": _variant_contract_digest(config["variants"]),
    }
    return config, bundle


def _http_assertions(service: RunService, run_id: str, expected_cells: int) -> None:
    server = make_server(service, heartbeat_interval=.01)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d/v1/arena/runs/%s" % (server.server_port, run_id)
    try:
        with urlopen(base + "/snapshot", timeout=2) as response:
            snapshot = json.loads(response.read())
        with urlopen(base + "/variants/" + VARIANT["variant_id"], timeout=2) as response:
            variant = json.loads(response.read())["variant"]
        cursor = snapshot["event_cursor"] - 1
        with urlopen(Request(base + "/events", headers={"Last-Event-ID": str(cursor)}), timeout=2) as response:
            projection_event = "".join(response.readline().decode() for _ in range(4))
        with urlopen(Request(base + "/events", headers={"Last-Event-ID": "999999"}), timeout=2) as response:
            resync = response.read().decode()
    finally:
        server.shutdown(); server.server_close(); thread.join()
    if snapshot["projection_status"] != "SCORED_NOT_RANKED" or snapshot["scoring"]["end_to_end_latency"] != "SCORED":
        raise AssertionError("Arena did not expose the scored diagnostic")
    if set(variant["metrics"]) != {"semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage"}:
        raise AssertionError("Arena metric contract is not canonical")
    if variant["case_pass_rate"] != {"passed": expected_cells, "denominator": expected_cells, "value": 1.0}:
        raise AssertionError("Arena case pass projection is incomplete")
    if variant["receipt_coverage"] != {"available": 0, "denominator": expected_cells, "value": 0.0} or variant["metrics"]["token_usage"] != {
        "count": 0,
        "receipt_coverage": 0.0,
        "input_mean": None,
        "input_p50": None,
        "input_p95": None,
        "output_mean": None,
        "output_p50": None,
        "output_p95": None,
        "total_mean": None,
        "total_p50": None,
        "total_p95": None,
    }:
        raise AssertionError("Arena represented unknown usage as known usage")
    exposed = json.dumps([snapshot, variant, projection_event, resync], sort_keys=True)
    if any(value in exposed for value in ("oracle_id", "receipt_id", "raw_response", "execution_evidence", "model_config_digest", "synthetic-frozen-financial-oracle")):
        raise AssertionError("Arena exposed sensitive diagnostic evidence")
    if "event: scorer_projection" not in projection_event or "event: resync_required" not in resync:
        raise AssertionError("Arena did not preserve SSE projection/resync behavior")


def run_checkpoint(root: Path, output_root: Path, name: str, case_ids: tuple[str, ...] | None) -> dict[str, Any]:
    compiled = compile_with_digest(root, variants=[VARIANT])
    manifest, bundle = _profile(compiled, name, case_ids)
    cases = {case["case_id"]: case for case in manifest["cases"]}
    client = SyntheticFinancialClient(VARIANT, cases, bundle["oracles"])
    service = RunService(RunStore(output_root), {VARIANT["variant_id"]: client})
    service.create_run(manifest)
    first = service.execute(manifest["run_id"])
    calls_before_resume = len(client.calls)
    second = service.execute(manifest["run_id"])
    projection = BenchmarkScorer(
        service.store,
        policy=compiled["scoring_policy"],
        oracle_bundle=bundle,
        approved_policy_digests={compiled["scoring_policy_digest"]},
        approved_oracle_bundle_digests={digest(bundle)},
    ).score(manifest["run_id"])
    events, score_events = service.store.events(manifest["run_id"]), service.store.score_events(manifest["run_id"])
    cells = len(cases)
    counts = Counter(event["event_type"] for event in events)
    terminals = [event for event in events if event["event_type"] == "terminal"]
    records = [event["record"] for event in score_events if event["event_type"] == "score_record"]
    variant = projection["variants"][0]
    if first["internal_status"] != "execution_complete" or second["internal_status"] != "execution_complete" or len(client.calls) != cells or calls_before_resume != cells:
        raise AssertionError("Runner did not terminalize each diagnostic cell exactly once")
    if counts != Counter({"dispatch_intent": cells, "terminal": cells, "run_started": 1, "run_finished": 1}) or len(records) != cells:
        raise AssertionError("Runner/Scorer journals have an unexpected shape")
    if any(event["usage"] != "unknown" or event["usage_source"] != "unknown" or "meta" in event["public_response"] for event in terminals):
        raise AssertionError("synthetic adapter fabricated a usage receipt")
    if any(not record["case_pass"] or record["failure_codes"] != ["USAGE_UNAVAILABLE"] or record["usage"] != "unknown" for record in records):
        raise AssertionError("synthetic frozen facts did not pass every case")
    if projection["projection_status"] != "SCORED_NOT_RANKED" or projection["ranked_results"] or variant["eligibility"] != "not_ranked":
        raise AssertionError("diagnostic was ranked")
    for coverage in (variant["semantic_oracle_coverage"], variant["oracle_coverage"]):
        if coverage != {"available": cells, "denominator": cells, "value": 1.0}:
            raise AssertionError("diagnostic coverage is incomplete")
    if variant["receipt_coverage"] != {"available": 0, "denominator": cells, "value": 0.0}:
        raise AssertionError("synthetic diagnostic fabricated receipt coverage")
    if variant["metrics"]["semantic_accuracy"]["value"] != 1.0 or variant["metrics"]["data_accuracy"]["value"] != 1.0 or any(value is not None for key, value in variant["metrics"]["token_usage"].items() if key not in {"count", "receipt_coverage"}):
        raise AssertionError("diagnostic metrics are not deterministic")
    assertion_count = sum(len(record["data_assertions"]) for record in records)
    if variant["metrics"]["data_accuracy"]["eligible_weight"] != assertion_count:
        raise AssertionError("data accuracy did not bind every fact assertion")
    _http_assertions(service, manifest["run_id"], cells)
    return {"checkpoint": name, "run_id": manifest["run_id"], "cells": cells, "assertions": assertion_count, "client_calls": calls_before_resume, "resume_additional_calls": len(client.calls) - calls_before_resume, "projection_status": projection["projection_status"], "token_usage": "unknown", "receipt_coverage": variant["receipt_coverage"]}


def run_all(root: Path = ROOT, output_root: Path | None = None) -> list[dict[str, Any]]:
    if output_root is None:
        output_root = Path(tempfile.mkdtemp(prefix="qveris-financial-diagnostic-"))
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return [run_checkpoint(root, output_root / name.lower(), name, case_ids) for name, case_ids in CHECKPOINTS]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_all(args.root, args.output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

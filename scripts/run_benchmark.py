#!/usr/bin/env python3
"""Run all frozen v2 cases through one explicit public-GET plugin.

The worker process receives only case_id, suite, and query as agent input.
The parent alone compiles and scores with the frozen Oracle bundle.  This is
process/module separation, not a claim of a sandbox against the same account.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qveris_benchmark.run_backend import RunService, RunStore, _digest, _variant_contract_digest


FIXTURE = "qveris_benchmark.fixture_get:make_client"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("JSON root must be an object")
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _factory(spec: str) -> Mapping[str, Any]:
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise ValueError("--get-client must be module:factory")
    factory = getattr(importlib.import_module(module), name, None)
    if not callable(factory):
        raise ValueError("GET plugin factory is not callable")
    value = factory()
    if not isinstance(value, Mapping) or set(value) != {"variant", "client"} or not isinstance(value["variant"], Mapping) or not callable(value["client"]):
        raise ValueError("GET plugin factory must return {'variant': ..., 'client': PublicGetClient}")
    return value


def _public_cases(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{name: case[name] for name in ("case_id", "suite", "query")} for case in manifest["cases"]]


def _runtime(args: argparse.Namespace) -> int:
    manifest, public_input = _json(Path(args.runtime_manifest)), _json(Path(args.public_input))
    public_cases = public_input.get("cases")
    if public_input.get("schema_version") != "benchmark-public-runtime-input/v1" or not isinstance(public_cases, list) or public_cases != _public_cases(manifest):
        raise ValueError("runtime input is not the exact public case projection")
    plugin = _factory(args.get_client)
    variant = plugin["variant"]
    if manifest.get("variants") != [variant]:
        raise ValueError("runtime plugin identity does not bind to the public manifest")
    service = RunService(RunStore(args.run_store), {variant["variant_id"]: plugin["client"]})
    service.create_run(manifest)
    snapshot = service.execute(manifest["run_id"])
    events = service.get_events(manifest["run_id"])
    terminals = [event for event in events if event["event_type"] == "terminal"]
    print(json.dumps({"run_id": snapshot["run_id"], "calls": len(terminals)}, sort_keys=True))
    return 0


def _run(args: argparse.Namespace) -> int:
    # Keep scorer/compiler parent-side: the worker imports only Runner + plugin.
    from qveris_benchmark.benchmark_scorer import BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION
    from qveris_benchmark.v2_compiler import compile_v2

    output = Path(args.output_dir).resolve()
    if ROOT == output or ROOT in output.parents:
        raise ValueError("--output-dir must be outside this repository")
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output-dir must be empty")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    compiled = Path(mkdtemp(prefix="compiled-", dir=output))
    result = compile_v2(ROOT / "benchmarks", compiled, run_id=args.run_id, mode="diagnostic")
    template, bundle = _json(result["run_manifest"]), _json(result["oracle_bundle"])
    policy = _json(ROOT / "benchmarks/oracles/v2/runner-score-policy.v2.json")
    plugin = _factory(FIXTURE if args.fixture else args.get_client)
    variant = dict(plugin["variant"])
    manifest = dict(template)
    manifest.update({
        "schema_version": "runner-run-manifest/v2",
        "compile_status": "diagnostic_nonranking_without_realtime_reference",
        "variants": [variant],
        "scoring_contract": {
            "policy_digest": _digest(policy),
            "oracle_bundle_digest": _digest(bundle),
            "scorer_version": SCORER_VERSION,
            "scorer_digest": SCORER_DIGEST,
            "variant_contract_digest": _variant_contract_digest([variant]),
        },
    })
    runtime_manifest = output / "runtime-manifest.v2.json"
    public_input = output / "public-runtime-input.v1.json"
    run_store = output / "run-store"
    _write(runtime_manifest, manifest)
    _write(public_input, {"schema_version": "benchmark-public-runtime-input/v1", "cases": _public_cases(manifest)})
    client_spec = FIXTURE if args.fixture else args.get_client
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_runtime", "--runtime-manifest", str(runtime_manifest), "--public-input", str(public_input), "--run-store", str(run_store), "--get-client", client_spec], check=True, capture_output=True, text=True)
    store = RunStore(run_store)
    events = store.events(args.run_id)
    dispatch = [event for event in events if event["event_type"] == "dispatch_intent"]
    terminal = [event for event in events if event["event_type"] == "terminal"]
    if len(dispatch) != 300 or len(terminal) != 300:
        raise RuntimeError("300-case runner did not dispatch and terminalize every case exactly once")
    case_suite = {case["case_id"]: case["suite"] for case in manifest["cases"]}
    suite_calls = {suite: sum(case_suite[event["case_id"]] == suite for event in dispatch) for suite in ("financial_statements", "historical_price", "realtime_quote")}
    if suite_calls != {suite: 100 for suite in suite_calls}:
        raise RuntimeError("runner did not execute exactly 100 cases in every suite")
    scorer = BenchmarkScorer(store, policy=policy, oracle_bundle=bundle, approved_policy_digests={_digest(policy)}, approved_oracle_bundle_digests={_digest(bundle)})
    projection = scorer.score(args.run_id)
    report = {
        "schema_version": "benchmark-300-run-report/v1",
        "run_id": args.run_id,
        "mode": "diagnostic_nonranking",
        "fixture": bool(args.fixture),
        "calls": len(dispatch),
        "suite_calls": suite_calls,
        "metrics": projection["variants"][0]["metrics"],
        "data_accuracy_status_by_suite": {"realtime_quote": {"value": "not_scored", "reason": "runtime_reference_contract_unavailable"}},
        "runtime_boundary": "worker receives only case_id, suite, query; Oracle bundle and scorer remain parent-side (not an OS sandbox claim)",
    }
    _write(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the fixed 3x100 public-GET benchmark.")
    parser.add_argument("--output-dir", required=False)
    parser.add_argument("--run-id", default="benchmark-300-diagnostic")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--fixture", action="store_true", help="explicit offline fixture; never a model score")
    choice.add_argument("--get-client", help="explicit plugin factory: module:factory")
    parser.add_argument("--_runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-manifest", help=argparse.SUPPRESS)
    parser.add_argument("--public-input", help=argparse.SUPPRESS)
    parser.add_argument("--run-store", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._runtime:
        if not all((args.runtime_manifest, args.public_input, args.run_store, args.get_client)):
            parser.error("runtime arguments are incomplete")
        return _runtime(args)
    if not args.output_dir or bool(args.fixture) == bool(args.get_client):
        parser.error("provide exactly one of --fixture or --get-client and an external --output-dir")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Compile the frozen v0.2 benchmark sources into runner-facing v2 artifacts.

This is deliberately an offline, deterministic transform.  It never calls a
provider and never writes raw source responses.  A runtime adapter supplies
agent variants and, for realtime cases, a separately frozen reference contract.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence


_SUITES = ("financial_statements", "historical_price", "realtime_quote")
_METRICS = ("semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompileError(ValueError):
    """The frozen source package cannot safely be compiled."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompileError("value must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompileError("invalid JSON: %s" % path) from exc


def _relative_path(root: Path, value: Any) -> Path:
    if type(value) is not str or not value or Path(value).is_absolute():
        raise CompileError("source path must be a non-empty relative path")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CompileError("source path escapes benchmark root: %s" % value) from exc
    if not candidate.is_file():
        raise CompileError("source file is missing: %s" % value)
    return candidate


def _verify_entry(root: Path, entry: Any, hashes: dict[str, str]) -> Path:
    if type(entry) is not dict or set(entry) != {"path", "sha256"}:
        raise CompileError("hash entry must contain only path and sha256")
    if type(entry["sha256"]) is not str or _SHA256.fullmatch(entry["sha256"]) is None:
        raise CompileError("hash entry has an invalid sha256: %r" % (entry.get("path"),))
    path = _relative_path(root, entry["path"])
    actual = _file_digest(path)
    if actual != entry["sha256"]:
        raise CompileError("hash mismatch: %s" % entry["path"])
    hashes[entry["path"]] = actual
    return path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise CompileError(message)


def _runner_case_id(suite: str, source_case_id: str) -> str:
    """Stable ASCII identity for Runner; source IDs can contain Chinese labels."""
    return "case-" + _digest([suite, source_case_id])[:32]


def _runner_oracle_id(suite: str, source_case_id: str) -> str:
    return "oracle-" + _digest([suite, source_case_id, "v2"])[:32]


def _assert_case_sets(cases: Sequence[Any], oracles: Sequence[Any], suite: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    _assert(type(cases) is list and len(cases) == 100, "%s requires exactly 100 candidates" % suite)
    _assert(type(oracles) is list and len(oracles) == 100, "%s requires exactly 100 oracles" % suite)
    case_map: dict[str, dict[str, Any]] = {}
    oracle_map: dict[str, dict[str, Any]] = {}
    for case in cases:
        _assert(type(case) is dict and case.get("suite") == suite, "%s candidate has wrong suite" % suite)
        case_id = case.get("case_id")
        _assert(type(case_id) is str and case_id and type(case.get("query")) is str and case["query"].strip(), "%s candidate is incomplete" % suite)
        _assert(case_id not in case_map, "%s contains duplicate candidate %s" % (suite, case_id))
        case_map[case_id] = case
    for oracle in oracles:
        _assert(type(oracle) is dict, "%s oracle is invalid" % suite)
        case_id = oracle.get("case_id")
        _assert(type(case_id) is str and case_id, "%s oracle has no case_id" % suite)
        _assert(case_id not in oracle_map, "%s contains duplicate oracle %s" % (suite, case_id))
        oracle_map[case_id] = oracle
    _assert(set(case_map) == set(oracle_map), "%s candidate/oracle case IDs differ" % suite)
    for case_id, case in case_map.items():
        _assert(case.get("expected_status") == oracle_map[case_id].get("expected_status"), "%s expected_status differs for %s" % (suite, case_id))
    return case_map, oracle_map


def _assertion(path: str, expected: Any) -> dict[str, Any]:
    return {"path": path, "operator": "exact", "expected": expected, "tolerance": None, "weight": 1, "fatal": True}


def _normalized_fact(field: Mapping[str, Any]) -> dict[str, Any]:
    """Project nullable source facts into the boolean-nil public response contract."""
    nil = field.get("nil") is True or field.get("value") is None
    return {
        "assertion_id": field.get("assertion_id"),
        "field": field.get("field"),
        "value": None if nil else field.get("value"),
        "period": field.get("period"),
        "currency": field.get("currency"),
        "unit": field.get("unit"),
        "nil": nil,
    }


def _state_oracle(case: Mapping[str, Any], oracle: Mapping[str, Any], oracle_id: str, source_ref: str) -> dict[str, Any]:
    status = oracle["expected_status"]
    answer = oracle.get("answer")
    _assert(type(answer) is dict and answer.get("status") == status, "state Oracle answer must match expected_status")
    return {
        "oracle_id": oracle_id,
        "case_id": case["case_id"],
        "suite": case["suite"],
        "expected_status": [status],
        "independence": "unavailable",
        "semantic_assertions": [],
        "data_assertions": [],
        "state_assertions": [_assertion("status", status)],
        "runtime_contract": None,
        "data_not_scored_until_receipt": False,
        "reference_evidence": None,
        "source_ref": source_ref,
        "version": "v2",
        "semantic_review_status": "not_applicable",
        "data_review_status": "not_applicable",
        "state_review_status": "approved",
    }


def _financial_oracle(case: Mapping[str, Any], oracle: Mapping[str, Any], source_ref: str) -> dict[str, Any]:
    oracle_id = "v2:%s" % case["case_id"]
    if oracle["expected_status"] != "success":
        return _state_oracle(case, oracle, oracle_id, source_ref)
    _assert(case.get("evaluation_mode") == "reported_fact_set" and oracle.get("oracle_type") == "reported_fact_set", "financial success case must be reported_fact_set")
    answer = oracle.get("answer")
    _assert(type(answer) is dict and type(answer.get("fields")) is list and answer["fields"], "financial answer requires fields")
    inherited = oracle.get("inherits_v1")
    candidate_ref = case.get("data_oracle")
    _assert(type(inherited) is dict and type(candidate_ref) is dict and candidate_ref.get("oracle_id") == inherited.get("oracle_id"), "financial candidate/v1 Oracle link differs")
    requested_ids = candidate_ref.get("assertion_ids")
    fields = answer["fields"]
    _assert(type(requested_ids) is list and requested_ids == [field.get("assertion_id") for field in fields], "financial assertion selection differs")
    facts: list[dict[str, Any]] = []
    for field in fields:
        _assert(type(field) is dict and type(field.get("assertion_id")) is str, "financial field is invalid")
        facts.append(_normalized_fact(field))
    assertions = [_assertion("data.facts.%s" % field["assertion_id"].replace("-", "_"), expected) for field, expected in zip(fields, facts)]
    return {
        "oracle_id": oracle_id,
        "case_id": case["case_id"],
        "suite": case["suite"],
        "expected_status": ["success"],
        "independence": "independent_frozen",
        "semantic_assertions": [_assertion("status", "success")],
        "data_assertions": assertions,
        "state_assertions": [],
        "alternative_assertion_sets": [{"semantic_assertions": [_assertion("status", "success")], "data_assertions": assertions, "state_assertions": []}],
        "runtime_contract": {"response_data_path": "data.facts", "key": "assertion_id", "required_assertion_ids": requested_ids},
        "data_not_scored_until_receipt": False,
        "reference_evidence": {"inherits_v1": inherited},
        "source_ref": source_ref,
        "version": "v2",
        "semantic_review_status": "approved",
        "data_review_status": "approved",
        "state_review_status": "not_applicable",
    }


def _historical_variant_rows(oracle: Mapping[str, Any], v1_numeric: Mapping[str, Mapping[str, Any]]) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    if oracle.get("oracle_type") == "inherited_v1_numeric":
        inherited = oracle.get("inherits_v1")
        _assert(type(inherited) is dict and type(inherited.get("oracle_id")) is str, "inherited historical Oracle is invalid")
        v1 = v1_numeric.get(inherited["oracle_id"])
        _assert(v1 is not None, "inherited historical v1 Oracle is missing")
        return v1.get("contract"), v1.get("accepted_variants")
    return oracle.get("contract"), oracle.get("accepted_variants")


def _bar_key(contract: Mapping[str, Any]) -> str:
    interval = contract.get("interval")
    if interval is None:
        interval = ((contract.get("date_and_aggregation") or {}).get("bar"))
    if interval is None:
        nested = contract.get("accepted_variants")
        _assert(type(nested) is list and nested, "historical contract has no bar interval")
        intervals = {((item.get("date_and_aggregation") or {}).get("bar")) for item in nested if type(item) is dict}
        _assert(len(intervals) == 1, "historical accepted variants use different bar intervals")
        interval = intervals.pop()
    normalized = {"D": "d", "daily": "d", "W": "w", "weekly": "w", "M": "m", "monthly": "m"}.get(interval)
    _assert(normalized is not None, "historical contract has unknown bar interval")
    return normalized


def _variant_contract(contract: Mapping[str, Any], variant_id: str) -> Mapping[str, Any]:
    variants = contract.get("accepted_variants")
    if type(variants) is list:
        for item in variants:
            if type(item) is dict and item.get("variant_id") == variant_id:
                return item
    return contract


def _bar_period_key(row: Mapping[str, Any], variant_contract: Mapping[str, Any], bar_key: str) -> str:
    dates = (variant_contract.get("date_and_aggregation") or {}).get("requested_dates") or variant_contract.get("date_window") or {}
    start = row.get("bucket_start") or row.get("date") or dates.get("start_date")
    end = row.get("bucket_end") or row.get("date") or dates.get("end_date") or start
    _assert(type(start) is str and type(end) is str, "historical row has no period")
    if bar_key == "d":
        return "d" + start.replace("-", "")
    if bar_key == "w":
        return "w%s_%s" % (start.replace("-", ""), end.replace("-", ""))
    return "m" + start.replace("-", "")[:6]


def _bars_expected(contract: Mapping[str, Any], variant: Mapping[str, Any], bar_key: str) -> dict[str, Any]:
    variant_contract = _variant_contract(contract, variant["variant_id"])
    instrument = variant.get("instrument") or variant_contract.get("instrument") or contract.get("instrument") or {}
    price_unit = instrument.get("price_unit") or ("%s_per_share" % (variant.get("currency") or contract.get("currency") or "currency"))
    volume_unit = instrument.get("volume_unit") or "shares"
    expected: dict[str, Any] = {}
    for row in variant["rows"]:
        _assert(type(row) is dict, "historical row is invalid")
        fields = {}
        for name, value in row.items():
            if name not in {"date", "bucket_start", "bucket_end"}:
                fields[name] = {"value": value, "unit": volume_unit if name == "volume" else price_unit, "nil": False}
        period_key = _bar_period_key(row, variant_contract, bar_key)
        _assert(period_key not in expected and fields, "historical bar key is duplicate or empty")
        expected[period_key] = {"period_key": period_key, "fields": fields}
    return expected


def _historical_oracle(case: Mapping[str, Any], oracle: Mapping[str, Any], source_ref: str, v1_numeric: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    oracle_id = "v2:%s" % case["case_id"]
    if oracle["expected_status"] != "success":
        return _state_oracle(case, oracle, oracle_id, source_ref)
    _assert(case.get("evaluation_mode") == "numeric_or_variant", "historical success case has invalid evaluation mode")
    contract, variants = _historical_variant_rows(oracle, v1_numeric)
    _assert(type(contract) is dict and type(variants) is list and variants, "historical numeric Oracle requires a contract and variants")
    bar_key = _bar_key(contract)
    alternatives: list[dict[str, list[dict[str, Any]]]] = []
    for variant in variants:
        _assert(type(variant) is dict and type(variant.get("variant_id")) is str and type(variant.get("rows")) is list, "historical variant is incomplete")
        alternative = {
            "semantic_assertions": [_assertion("status", "success"), _assertion("resolved_request.accepted_variant_id", variant["variant_id"])],
            "data_assertions": [_assertion("data.accepted_variant_id", variant["variant_id"]), _assertion("data.bars", _bars_expected(contract, variant, bar_key))],
            "state_assertions": [],
        }
        alternatives.append(alternative)
    return {
        "oracle_id": oracle_id,
        "case_id": case["case_id"],
        "suite": case["suite"],
        "expected_status": ["success"],
        "independence": "independent_frozen",
        "semantic_assertions": [],
        "data_assertions": [],
        "state_assertions": [],
        "alternative_assertion_sets": alternatives,
        "runtime_contract": {"response_data_path": "data.bars.%s" % bar_key, "bar_key": bar_key, "contract": contract, "alternative_variant_ids": [variant["variant_id"] for variant in variants], "source_coherence": "one complete alternative assertion set only; never cross-variant field splice"},
        "data_not_scored_until_receipt": False,
        "reference_evidence": {"oracle_type": oracle.get("oracle_type"), "inherits_v1": oracle.get("inherits_v1")},
        "source_ref": source_ref,
        "version": "v2",
        "semantic_review_status": "approved",
        "data_review_status": "approved",
        "state_review_status": "not_applicable",
    }


def _realtime_oracle(case: Mapping[str, Any], oracle: Mapping[str, Any], source_ref: str) -> dict[str, Any]:
    oracle_id = "v2:%s" % case["case_id"]
    if oracle["expected_status"] != "success":
        return _state_oracle(case, oracle, oracle_id, source_ref)
    _assert(case.get("evaluation_mode") == "runtime_snapshot", "realtime success case must be a runtime snapshot")
    _assert(oracle.get("runtime_capture_required") is True and type(oracle.get("runtime_receipt_contract")) is dict, "realtime success case requires a runtime receipt contract")
    answer = oracle.get("answer")
    _assert(type(answer) is dict and type(answer.get("required_fields")) is list, "realtime success answer is invalid")
    _assert(case.get("required_fields") == answer["required_fields"], "realtime candidate/oracle required_fields differ")
    response_constraints = answer.get("response_constraints")
    _assert(type(response_constraints) is dict, "realtime response constraints are missing")
    # Dynamic values are intentionally absent.  The scorer must admit data only
    # after a runtime receipt binds one source-coherent quote variant.
    return {
        "oracle_id": oracle_id,
        "case_id": case["case_id"],
        "suite": case["suite"],
        "expected_status": ["success"],
        "independence": "unavailable",
        "semantic_assertions": [_assertion("status", "success")],
        "data_assertions": [],
        "state_assertions": [],
        "alternative_assertion_sets": [{"semantic_assertions": [_assertion("status", "success")], "data_assertions": [], "state_assertions": []}],
        "runtime_contract": {"response_data_path": "data.quote.fields", "key": "field_name", "required_fields": answer["required_fields"], "response_constraints": response_constraints, "runtime_receipt_contract": oracle["runtime_receipt_contract"], "entity_resolution": answer.get("entity_resolution")},
        "data_not_scored_until_receipt": True,
        "reference_evidence": None,
        "source_ref": source_ref,
        "version": "v2",
        "semantic_review_status": "approved",
        "data_review_status": "unavailable",
        "state_review_status": "not_applicable",
    }


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _reference_contract(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"source_contract_hash", "window_rule_version"}:
        raise CompileError("reference contract must contain source_contract_hash and window_rule_version only")
    source_hash, version = value["source_contract_hash"], value["window_rule_version"]
    if type(source_hash) is not str or _SHA256.fullmatch(source_hash) is None or type(version) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", version):
        raise CompileError("reference contract is invalid")
    return {"source_contract_hash": source_hash, "window_rule_version": version}


def compile_v2(
    benchmark_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    run_id: str = "compiled-v2",
    variants: Sequence[Mapping[str, Any]] = (),
    reference_contract: Mapping[str, Any] | None = None,
    timeout_ms: int = 30000,
    mode: str = "diagnostic",
) -> dict[str, Path]:
    """Verify the frozen sources then atomically emit a bundle and run template.

    A ready manifest is emitted only when concrete variants and a separately
    supplied realtime reference contract are provided.  No placeholder identity,
    price, source hash, or receipt is invented by this compiler.
    """
    root = Path(benchmark_root).resolve()
    out = Path(output_dir).resolve()
    _assert(root.is_dir(), "benchmark root is missing")
    _assert(type(run_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", run_id) is not None, "run_id is invalid")
    _assert(type(timeout_ms) is int and not isinstance(timeout_ms, bool) and timeout_ms > 0, "timeout_ms must be positive")
    _assert(mode in {"diagnostic", "official"}, "mode must be diagnostic or official")
    hashes: dict[str, str] = {}
    candidate_manifest_path = root / "candidates/v0.2/manifest.json"
    candidate_manifest = _json(candidate_manifest_path)
    _assert(type(candidate_manifest) is dict and candidate_manifest.get("schema_version") == "candidate-manifest/v0.2", "candidate root manifest is invalid")
    _assert(tuple(candidate_manifest.get("metrics", ())) == _METRICS, "candidate metrics must be the four approved v2 metrics")
    hashes["candidates/v0.2/manifest.json"] = _file_digest(candidate_manifest_path)
    suite_specs = {item.get("suite"): item for item in candidate_manifest.get("suites", []) if type(item) is dict}
    _assert(set(suite_specs) == set(_SUITES), "candidate root manifest must declare all three suites")
    root_candidate_files = {entry.get("path"): entry for entry in candidate_manifest.get("files", []) if type(entry) is dict}
    root_suite_manifests = {entry.get("path"): entry for entry in candidate_manifest.get("suite_oracle_manifests", []) if type(entry) is dict}
    compiled: dict[str, dict[str, Any]] = {}
    source_to_runner: dict[tuple[str, str], tuple[str, str]] = {}
    expected_status_counts: dict[str, dict[str, int]] = {}
    v1_numeric_path = root / "oracles/v1/outputs/historical_price/oracles.json"
    v1_numeric_document = _json(v1_numeric_path)
    v1_numeric = {item.get("oracle_id"): item for item in v1_numeric_document.get("numeric_oracles", []) if type(item) is dict}
    for suite in _SUITES:
        candidate_name = "%s.cases.json" % suite
        candidate_rel = "candidates/v0.2/%s" % candidate_name
        suite_manifest_rel = "oracles/v2/outputs/%s/manifest.json" % suite
        _assert(candidate_name in root_candidate_files and suite_manifest_rel in root_suite_manifests, "candidate root manifest is missing %s bindings" % suite)
        candidate_path = _verify_entry(root, {"path": candidate_rel, "sha256": root_candidate_files[candidate_name]["sha256"]}, hashes)
        suite_manifest_path = _verify_entry(root, root_suite_manifests[suite_manifest_rel], hashes)
        suite_manifest = _json(suite_manifest_path)
        _assert(type(suite_manifest) is dict and suite_manifest.get("suite") == suite and suite_manifest.get("schema_version") == "benchmark-v2-manifest/v1", "%s suite manifest is invalid" % suite)
        for group in ("candidate_files", "policy_files", "v1_bindings"):
            entries = suite_manifest.get(group)
            _assert(type(entries) is list and entries, "%s manifest lacks %s" % (suite, group))
            for entry in entries:
                _verify_entry(root, entry, hashes)
        candidate_document = _json(candidate_path)
        oracle_path = root / ("oracles/v2/outputs/%s/oracles.json" % suite)
        oracle_entry = next((entry for entry in suite_manifest["candidate_files"] if entry.get("path") == "oracles/v2/outputs/%s/oracles.json" % suite), None)
        _assert(oracle_entry is not None, "%s manifest lacks its Oracle file" % suite)
        _verify_entry(root, oracle_entry, hashes)
        oracle_document = _json(oracle_path)
        _assert(type(oracle_document) is dict and oracle_document.get("suite") == suite, "%s Oracle document is invalid" % suite)
        case_map, oracle_map = _assert_case_sets(candidate_document, oracle_document.get("oracles"), suite)
        status_counts: dict[str, int] = {}
        for case in case_map.values():
            status = case["expected_status"]
            status_counts[status] = status_counts.get(status, 0) + 1
        _assert(status_counts == suite_specs[suite].get("expected_status_counts"), "%s expected status counts differ from root manifest" % suite)
        expected_status_counts[suite] = status_counts
        source_ref = "oracles/v2/outputs/%s/oracles.json" % suite
        for case_id in sorted(case_map):
            case, oracle = case_map[case_id], oracle_map[case_id]
            if suite == "financial_statements":
                record = _financial_oracle(case, oracle, source_ref)
            elif suite == "historical_price":
                record = _historical_oracle(case, oracle, source_ref, v1_numeric)
            else:
                record = _realtime_oracle(case, oracle, source_ref)
            runner_case_id, runner_oracle_id = _runner_case_id(suite, case_id), _runner_oracle_id(suite, case_id)
            _assert(runner_case_id not in {value[0] for value in source_to_runner.values()} and runner_oracle_id not in compiled, "internal ID collision")
            record["source_case_id"] = case_id
            record["source_oracle_id"] = record["oracle_id"]
            record["case_id"] = runner_case_id
            record["oracle_id"] = runner_oracle_id
            compiled[runner_oracle_id] = record
            source_to_runner[(suite, case_id)] = (runner_case_id, runner_oracle_id)
    _assert(len(compiled) == 300, "compiler must produce exactly 300 Oracle records")
    policy_path = root / "oracles/v2/query-resolution-policy.v2.json"
    policy = _json(policy_path)
    policy_digest = _digest(policy)
    compiler_digest = _file_digest(Path(__file__).resolve())
    compiled_oracle_content_digest = _digest(compiled)
    source_manifest_hashes = {
        "candidate_manifest": hashes["candidates/v0.2/manifest.json"],
        **{"%s_manifest" % suite: hashes["oracles/v2/outputs/%s/manifest.json" % suite] for suite in _SUITES},
    }
    freeze_digest = _digest({"candidate_manifest": source_manifest_hashes["candidate_manifest"], "suite_manifests": {suite: source_manifest_hashes["%s_manifest" % suite] for suite in _SUITES}, "compiler_module_sha256": compiler_digest, "compiled_oracle_content_digest": compiled_oracle_content_digest, "policy_digest": policy_digest})
    bundle = {
        "schema_version": "oracle-bundle/v2",
        "freeze_digest": freeze_digest,
        "compiler_module_sha256": compiler_digest,
        "compiled_oracle_content_digest": compiled_oracle_content_digest,
        "policy_digest": policy_digest,
        "source_manifest_hashes": source_manifest_hashes,
        "source_hashes": dict(sorted(hashes.items())),
        "expected_status_counts": expected_status_counts,
        "oracles": dict(sorted(compiled.items())),
    }
    bundle_digest = _digest(bundle)
    supplied_reference = _reference_contract(reference_contract)
    if variants and supplied_reference is None:
        raise CompileError("variants require a realtime reference contract")
    ready = bool(variants) and supplied_reference is not None
    cases: list[dict[str, Any]] = []
    for suite in _SUITES:
        candidate_document = _json(root / ("candidates/v0.2/%s.cases.json" % suite))
        for case in candidate_document:
            status = case["expected_status"]
            runner_case_id, runner_oracle_id = source_to_runner[(suite, case["case_id"])]
            entry: dict[str, Any] = {
                "case_id": runner_case_id,
                "source_case_id": case["case_id"],
                "suite": suite,
                "query": case["query"],
                "score_case": {"expected_status": [status], "oracle_id": runner_oracle_id, "case_type": "normal" if status == "success" else "boundary"},
            }
            if suite == "realtime_quote":
                if supplied_reference is None:
                    entry["reference_contract_status"] = "blocked_not_scored_until_runtime_reference_contract"
                else:
                    entry["reference_contract"] = supplied_reference
            cases.append(entry)
    run_manifest = {
        "schema_version": "runner-run-manifest/v2" if ready else "runner-run-manifest-template/v2",
        "compile_status": "ready" if ready else "blocked_until_variants_and_realtime_reference_contract",
        "run_id": run_id,
        "mode": mode,
        "freeze_digest": freeze_digest,
        "policy": {"schema_version": "runner-score-policy/v2", "metrics": list(_METRICS), "policy_digest": policy_digest},
        "timeout_ms": timeout_ms,
        "concurrency": 1,
        "variants": list(variants),
        "oracle_bundle_digest": bundle_digest,
        "source_manifest_hashes": source_manifest_hashes,
        "expected_status_counts": expected_status_counts,
        "cases": cases,
    }
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / "oracle-bundle.v2.json"
    manifest_path = out / ("run-manifest.v2.json" if ready else "run-manifest-template.v2.json")
    _atomic_json(bundle_path, bundle)
    _atomic_json(manifest_path, run_manifest)
    return {"oracle_bundle": bundle_path, "run_manifest": manifest_path}


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile verified v0.2 candidates and v2 Oracles for the benchmark runner.")
    parser.add_argument("--benchmark-root", default="benchmarks", help="directory containing candidates/ and oracles/")
    parser.add_argument("--output-dir", required=True, help="empty or existing directory for compiled JSON")
    parser.add_argument("--run-id", default="compiled-v2")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--mode", choices=("diagnostic", "official"), default="diagnostic")
    parser.add_argument("--variant-json", action="append", default=[], help="path to one concrete runner variant identity JSON object; repeat for each variant")
    parser.add_argument("--reference-contract-json", help="path to runtime reference {source_contract_hash,window_rule_version}; required to emit a ready realtime manifest")
    args = parser.parse_args(argv)
    variants = [_json(Path(path)) for path in args.variant_json]
    reference = _json(Path(args.reference_contract_json)) if args.reference_contract_json else None
    try:
        result = compile_v2(args.benchmark_root, args.output_dir, run_id=args.run_id, variants=variants, reference_contract=reference, timeout_ms=args.timeout_ms, mode=args.mode)
    except CompileError as exc:
        parser.error(str(exc))
    print(json.dumps({key: str(value) for key, value in result.items()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the module CLI
    raise SystemExit(_main())

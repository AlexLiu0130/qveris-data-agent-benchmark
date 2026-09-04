#!/usr/bin/env python3
"""Dependency-free integrity gate for the query-quality v2 benchmark files."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "benchmarks"
V1_BINDINGS = (
    "oracles/v1/outputs/financial_statements/manifest.json",
    "oracles/v1/outputs/historical_price/oracles.json",
    "oracles/v1/outputs/realtime_quote/oracles.json",
)
EXPECTED = {
    "financial_statements": {"success": 88, "needs_clarification": 5, "no_data": 7},
    "historical_price": {"success": 82, "needs_clarification": 2, "no_data": 6, "unsupported": 10},
    "realtime_quote": {"success": 90, "needs_clarification": 6, "no_data": 2, "unsupported": 2},
}


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def is_sha(value: object) -> bool:
    value = str(value).removeprefix("sha256:")
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def normalized_sha(value: str) -> str:
    return value.removeprefix("sha256:")


def status_counts(cases: list[dict]) -> Counter:
    return Counter(case["expected_status"] for case in cases)


def index_by_case(items: list[dict], errors: list[str], label: str) -> dict[str, dict]:
    indexed = {item.get("case_id"): item for item in items}
    if None in indexed or len(indexed) != len(items):
        fail(errors, f"{label}: duplicate or missing case_id")
    return indexed


def check_hash_items(errors: list[str], label: str, items: list[dict]) -> None:
    for item in items:
        path = BASE / item.get("path", "")
        if not path.is_file() or digest(path) != item.get("sha256"):
            fail(errors, f"{label}: manifest hash mismatch {item.get('path')}")


def check_candidate_manifest(errors: list[str], candidates: dict[str, list[dict]]) -> None:
    manifest = load(BASE / "candidates/v0.2/manifest.json")
    if manifest.get("schema_version") != "candidate-manifest/v0.2":
        fail(errors, "candidate manifest: schema version")
    if manifest.get("metrics") != ["semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage"]:
        fail(errors, "candidate manifest: exact four benchmark metrics")
    suites = {item.get("suite"): item for item in manifest.get("suites", [])}
    if set(suites) != set(EXPECTED):
        fail(errors, "candidate manifest: suite set")
    for suite, cases in candidates.items():
        entry = suites.get(suite, {})
        if entry.get("cases") != len(cases) or entry.get("expected_status_counts") != EXPECTED[suite]:
            fail(errors, f"candidate manifest: {suite} counts")
    for item in manifest.get("files", []):
        path = BASE / "candidates/v0.2" / item.get("path", "")
        if not path.is_file() or digest(path) != item.get("sha256"):
            fail(errors, f"candidate manifest: file hash mismatch {item.get('path')}")
    suite_manifests = manifest.get("suite_oracle_manifests", [])
    expected_paths = {f"oracles/v2/outputs/{suite}/manifest.json" for suite in EXPECTED}
    if {item.get("path") for item in suite_manifests} != expected_paths:
        fail(errors, "candidate manifest: suite Oracle manifest bindings")
    check_hash_items(errors, "candidate manifest", suite_manifests)


def check_suite_manifest(errors: list[str], suite: str) -> None:
    manifest = load(BASE / f"oracles/v2/outputs/{suite}/manifest.json")
    if manifest.get("suite") != suite or manifest.get("schema_version") != "benchmark-v2-manifest/v1":
        fail(errors, f"{suite}: suite manifest identity")
    check_hash_items(errors, suite, manifest.get("candidate_files", []))
    check_hash_items(errors, suite, manifest.get("policy_files", []))
    bindings = {item.get("path"): item.get("sha256") for item in manifest.get("v1_bindings", [])}
    if set(bindings) != set(V1_BINDINGS):
        fail(errors, f"{suite}: v1 binding set")
    for path in V1_BINDINGS:
        if bindings.get(path) != digest(BASE / path):
            fail(errors, f"{suite}: v1 binding hash {path}")


def check_case_alignment(errors: list[str], suite: str, candidates: list[dict], oracles: list[dict]) -> None:
    candidate_by_case = index_by_case(candidates, errors, f"{suite} candidates")
    oracle_by_case = index_by_case(oracles, errors, f"{suite} Oracles")
    if set(candidate_by_case) != set(oracle_by_case):
        fail(errors, f"{suite}: candidate/Oracle case_id set")
        return
    for case_id, candidate in candidate_by_case.items():
        if candidate.get("expected_status") != oracle_by_case[case_id].get("expected_status"):
            fail(errors, f"{suite} {case_id}: candidate/Oracle status")


def check_financial(errors: list[str], candidates: list[dict]) -> None:
    financial = load(BASE / "oracles/v2/outputs/financial_statements/oracles.json")["oracles"]
    check_case_alignment(errors, "financial_statements", candidates, financial)
    v1_root = BASE / "oracles/v1"
    v1_manifest = v1_root / "outputs/financial_statements/manifest.json"
    assertions, source_oracles = {}, {}
    for item in load(v1_manifest).get("files", []):
        if item["path"].endswith("/oracles.json"):
            for source_oracle in load(v1_root / item["path"]):
                source_oracles[source_oracle["oracle_id"]] = source_oracle
                for assertion in source_oracle["atomic_assertions"]:
                    assertions[(source_oracle["oracle_id"], assertion["assertion_id"])] = assertion
    contracts = {item["fact_contract_id"]: item for item in load(v1_root / "fact-contracts.financial.v1.json")["contracts"]}
    candidate_by_case = {item["case_id"]: item for item in candidates}
    for oracle in financial:
        case_id, status = oracle["case_id"], oracle["expected_status"]
        if status != "success":
            if oracle.get("oracle_type") != "state_oracle" or oracle.get("answer", {}).get("status") != status:
                fail(errors, f"financial {case_id}: state Oracle")
            continue
        inherited, answer = oracle.get("inherits_v1", {}), oracle.get("answer", {})
        contract = contracts.get(inherited.get("fact_contract_id"))
        fields = answer.get("fields", [])
        if inherited.get("manifest_path") != "oracles/v1/outputs/financial_statements/manifest.json" or inherited.get("manifest_sha256") != digest(v1_manifest):
            fail(errors, f"financial {case_id}: v1 manifest binding")
        if oracle.get("oracle_type") != "reported_fact_set" or not contract or not 1 <= len(fields) <= 6:
            fail(errors, f"financial {case_id}: 1-6 reported fields")
            continue
        if inherited.get("oracle_id") != contract.get("source_oracle", {}).get("oracle_id"):
            fail(errors, f"financial {case_id}: contract/source binding")
        source = source_oracles.get(inherited.get("oracle_id"))
        if not source or source.get("status") != "frozen":
            fail(errors, f"financial {case_id}: missing frozen v1 source")
        if answer.get("entity") != contract.get("entity") or answer.get("statement_schema_id") != contract.get("complete_statement_schema_id"):
            fail(errors, f"financial {case_id}: entity or statement schema")
        if any(answer.get(key) != contract.get(source_key) for key, source_key in (("statement_type", "statement_type"), ("consolidation_scope", "consolidation_scope"), ("currency", "currency"))):
            fail(errors, f"financial {case_id}: cross-statement or cross-currency projection")
        allowed_units = {contract.get("unit_scale")}
        allowed_units.update(item.get("unit_scale") for item in contract.get("field_unit_exceptions", []))
        if answer.get("unit") not in allowed_units or answer.get("period_end") != contract.get("period", {}).get("period_end"):
            fail(errors, f"financial {case_id}: cross-period or cross-unit projection")
        candidate_oracle = candidate_by_case[case_id].get("data_oracle", {})
        if candidate_oracle.get("oracle_id") != inherited.get("oracle_id") or candidate_oracle.get("assertion_ids") != [field.get("assertion_id") for field in fields]:
            fail(errors, f"financial {case_id}: candidate assertion projection")
        for field in fields:
            assertion = assertions.get((inherited.get("oracle_id"), field.get("assertion_id")))
            exact = ("field", "value", "period", "currency", "unit", "comparison", "receipt_ids")
            if not assertion or any(field.get(key) != assertion.get("expected" if key == "value" else key) for key in exact):
                fail(errors, f"financial {case_id}: field differs from v1 assertion")
            if any(field.get(key) != answer.get(key) for key in ("period", "currency", "unit")):
                fail(errors, f"financial {case_id}: mixed field period/currency/unit")
    forbidden = ("同比", "环比", "增长", "比率", "利润率", "差额", "完整", "计算")
    if any(any(word in case["query"] for word in forbidden) for case in candidates if case["expected_status"] == "success"):
        fail(errors, "financial: success query contains a derived/complete-statement request")


def period_bounds(value: dict) -> tuple[str | None, str | None]:
    return value.get("start") or value.get("start_date"), value.get("end") or value.get("end_date")


def unit_matches(instrument: dict, variant: dict, fields: list[str]) -> bool:
    unit = variant.get("unit", "").replace(" ", "").replace("_", "").lower()
    price_unit = instrument.get("price_unit", "").replace(" ", "").replace("_", "").lower()
    generic_price = f"price={instrument.get('currency', '').lower()}pershare" in unit or "price=currencypershare" in unit
    if price_unit not in unit and not generic_price:
        return False
    return "volume" not in fields or "volume=shares" in unit


def check_historical(errors: list[str], candidates: list[dict]) -> None:
    path = BASE / "oracles/v2/outputs/historical_price"
    historical = load(path / "oracles.json")["oracles"]
    receipts = load(path / "evidence-receipts.json")["receipts"]
    ledger = load(path / "review-ledger.json")
    policy = BASE / "oracles/v2/query-resolution-policy.v2.json"
    check_case_alignment(errors, "historical_price", candidates, historical)
    bindings = ledger.get("artifact_bindings", {})
    if bindings.get("candidate_sha256") != digest(BASE / "candidates/v0.2/historical_price.cases.json") or bindings.get("oracle_sha256") != digest(path / "oracles.json") or bindings.get("evidence_receipts_sha256") != digest(path / "evidence-receipts.json") or bindings.get("query_resolution_policy_sha256") != digest(policy):
        fail(errors, "historical: ledger artifact/query-policy bindings")
    receipts_by_variant, receipt_by_id = defaultdict(list), {}
    for receipt in receipts:
        if not is_sha(receipt.get("content_sha256")) or not receipt.get("url") or not receipt.get("retrieved_at"):
            fail(errors, f"historical receipt {receipt.get('receipt_id')}: traceability")
        receipt_by_id[receipt.get("receipt_id")] = receipt
        receipts_by_variant[(receipt.get("case_id"), receipt.get("variant_id"))].append(receipt)
    v1 = load(BASE / "oracles/v1/outputs/historical_price/oracles.json")
    v1_numeric = {item["oracle_id"]: item for item in v1["numeric_oracles"]}
    for oracle in historical:
        case_id, status = oracle["case_id"], oracle["expected_status"]
        if status != "success":
            if oracle.get("oracle_type") != "state_oracle" or oracle.get("answer", {}).get("status") != status:
                fail(errors, f"historical {case_id}: state Oracle")
            continue
        if oracle.get("oracle_type") == "inherited_v1_numeric":
            inherited = oracle.get("inherits_v1", {})
            source = v1_numeric.get(inherited.get("oracle_id"))
            if not source or inherited.get("oracles_sha256") != digest(BASE / "oracles/v1/outputs/historical_price/oracles.json") or inherited.get("receipt_sha256") != digest(BASE / "oracles/v1/outputs/historical_price/evidence-receipts.json"):
                fail(errors, f"historical {case_id}: inherited v1 binding")
                continue
            if source.get("case_id") != case_id or source.get("contract") != oracle.get("contract"):
                fail(errors, f"historical {case_id}: inherited contract drift")
            source_variants = {item["variant_id"] for item in source.get("accepted_variants", [])}
            if {item.get("variant_id") for item in oracle.get("accepted_variants", [])} != source_variants:
                fail(errors, f"historical {case_id}: inherited variant set")
            check_bar_shape(errors, case_id, source.get("contract", {}), source.get("accepted_variants", []))
            continue
        if oracle.get("oracle_type") != "v2_numeric":
            fail(errors, f"historical {case_id}: numeric Oracle type")
            continue
        contract = oracle.get("contract", {})
        variants = oracle.get("accepted_variants", [])
        if not variants:
            fail(errors, f"historical {case_id}: contract or variants")
            continue
        contracts = {item.get("variant_id"): item for item in contract.get("accepted_variants", [])}
        for variant in variants:
            variant_contract = contracts.get(variant.get("variant_id"), contract)
            instrument = variant_contract.get("instrument")
            fields = variant_contract.get("fields", contract.get("fields", []))
            expected_instrument = variant.get("instrument", {})
            if not instrument or any(expected_instrument.get(key) != instrument.get(key) for key in ("symbol", "mic", "currency", "price_unit", "volume_unit")):
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: contract/variant instrument")
            if variant.get("currency") != instrument.get("currency") or not unit_matches(instrument, variant, fields):
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: contract/variant currency or unit")
            rows = variant.get("rows", [])
            if not rows or any(any(field not in row for field in fields) for row in rows):
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: required output fields")
            source = variant.get("source", {})
            variant_receipts = receipts_by_variant[(case_id, variant.get("variant_id"))]
            if not variant_receipts:
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: missing receipt")
            elif not variant.get("daily_source_coverage") and not any(receipt.get("url") == source.get("url") and normalized_sha(receipt.get("content_sha256", "")) == normalized_sha(source.get("content_sha256", "")) for receipt in variant_receipts):
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: source/receipt mismatch")
            check_receipt_date_coverage(errors, case_id, variant, variant_receipts, receipt_by_id, fields)
            check_bar_shape(errors, case_id, variant_contract, [variant])
        check_bar_shape(errors, case_id, contract, variants)


def check_receipt_date_coverage(errors: list[str], case_id: str, variant: dict, receipts: list[dict], receipt_by_id: dict[str, dict], fields: list[str]) -> None:
    rows = variant.get("rows", [])
    daily = variant.get("daily_source_coverage")
    if daily:
        if len(daily) != len(rows) or {entry.get("date") for entry in daily} != {row.get("date") for row in rows}:
            fail(errors, f"historical {case_id}/{variant.get('variant_id')}: daily receipt date coverage")
        for entry in daily:
            receipt = receipt_by_id.get(entry.get("receipt_id"))
            if not receipt or receipt.get("observed_date") != entry.get("date") or entry.get("source_field") not in fields:
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: daily receipt/date link")
        return
    source_dates = [row.get("date") for row in rows if row.get("date")]
    for receipt in receipts:
        coverage = receipt.get("coverage", {})
        if isinstance(coverage, dict) and (coverage.get("start") or coverage.get("start_date")):
            start, end = period_bounds(coverage)
            if not start or not end or any(not start <= date <= end for date in source_dates):
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: receipt date coverage")


def check_bar_shape(errors: list[str], case_id: str, contract: dict, variants: list[dict]) -> None:
    aggregation = contract.get("date_and_aggregation", {})
    resolution = aggregation.get("resolution", "")
    monthly = aggregation.get("bar") == "monthly" or "month_k_priority" in resolution
    weekly = contract.get("interval") == "W" or aggregation.get("bar") == "weekly"
    for variant in variants:
        rows = variant.get("rows", [])
        if monthly:
            start, end = period_bounds(variant.get("resolved_period", {}))
            expected_start = aggregation.get("resolved_month")
            expected_end = aggregation.get("resolved_month_end")
            if len(rows) != 1 or not start or not end or start != expected_start or end != expected_end or rows[0].get("date") != start:
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: monthly final one-row/period")
        if weekly:
            window = contract.get("date_window", {})
            if len(rows) != 1 or rows[0].get("bucket_start") != window.get("start_date") or rows[0].get("bucket_end") != window.get("end_date"):
                fail(errors, f"historical {case_id}/{variant.get('variant_id')}: weekly final one-row/period")


def contains_static_payload(value: object) -> bool:
    if isinstance(value, dict):
        if {"name", "value", "unit", "as_of"}.issubset(value):
            return True
        forbidden_keys = {"accepted_variants", "rows", "quote", "quote_value", "last_price_value", "static_price", "snapshot", "market_data"}
        return any(key in forbidden_keys or contains_static_payload(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_static_payload(item) for item in value)
    return False


def check_realtime(errors: list[str], candidates: list[dict]) -> None:
    realtime = load(BASE / "oracles/v2/outputs/realtime_quote/oracles.json")["oracles"]
    schema = load(BASE / "oracles/v2/schemas/realtime-runtime-receipt.v1.schema.json")
    check_case_alignment(errors, "realtime_quote", candidates, realtime)
    candidate_by_case = {item["case_id"]: item for item in candidates}
    schema_required = {"case_id", "get_execution", "accepted_variant_id", "source", "captured_at", "fields"}
    source_required = {"name", "url", "quote_timestamp", "as_of"}
    field_required = {"name", "value", "unit", "as_of"}
    if set(schema.get("required", [])) != schema_required or set(schema.get("properties", {}).get("source", {}).get("required", [])) != source_required or set(schema.get("properties", {}).get("fields", {}).get("items", {}).get("required", [])) != field_required:
        fail(errors, "realtime: runtime receipt schema")
    for oracle in realtime:
        case_id, status = oracle["case_id"], oracle["expected_status"]
        if status != "success":
            if oracle.get("oracle_state") != "frozen" or oracle.get("answer", {}).get("status") != status:
                fail(errors, f"realtime {case_id}: frozen state Oracle")
            continue
        answer, runtime = oracle.get("answer", {}), oracle.get("runtime_receipt_contract", {})
        candidate_fields = candidate_by_case[case_id].get("required_fields", [])
        resolution = answer.get("entity_resolution", {})
        response_fields = answer.get("response_constraints", {}).get("required_fields", [])
        if oracle.get("oracle_state") != "contract_frozen" or oracle.get("data_accuracy") != "runtime_capture_required" or oracle.get("runtime_capture_required") is not True:
            fail(errors, f"realtime {case_id}: runtime-only state")
        if not resolution.get("acceptable_market_variants") or not answer.get("required_fields") or answer.get("required_fields") != candidate_fields or [item.get("name") for item in response_fields] != candidate_fields:
            fail(errors, f"realtime {case_id}: entity resolution/required fields")
        for variant in resolution.get("acceptable_market_variants", []):
            if not variant.get("instruments") or any(any(not instrument.get(key) for key in ("symbol", "mic", "currency", "instrument_type")) for instrument in variant["instruments"]):
                fail(errors, f"realtime {case_id}: resolved instrument")
        if runtime.get("schema_ref") != "oracles/v2/schemas/realtime-runtime-receipt.v1.schema.json" or runtime.get("get_execution_count") != 1 or runtime.get("accepted_variant_rule") != "one_complete_traceable_source_coherent_variant" or runtime.get("capture_failure") != {"data_accuracy": "not_scored", "scope": "this_case_only"}:
            fail(errors, f"realtime {case_id}: runtime receipt/capture failure rule")
        if contains_static_payload(oracle):
            fail(errors, f"realtime {case_id}: static quote payload")


def main() -> int:
    errors: list[str] = []
    v1 = subprocess.run([sys.executable, str(BASE / "oracles/v1/validate_freeze.py")], cwd=ROOT, capture_output=True, text=True)
    if v1.returncode:
        fail(errors, "v1 validator failed: " + v1.stdout + v1.stderr)
    immutable = subprocess.run(["git", "diff", "--quiet", "46b11bd2872245cf561487df063fcd6609f53d55", "--", "benchmarks/candidates/v0.1", "benchmarks/oracles/v1"], cwd=ROOT)
    if immutable.returncode:
        fail(errors, "v0.1 candidates or Oracle v1 bytes changed")
    candidates = {}
    for suite, counts in EXPECTED.items():
        cases = load(BASE / f"candidates/v0.2/{suite}.cases.json")
        candidates[suite] = cases
        if len(cases) != 100 or status_counts(cases) != counts:
            fail(errors, f"{suite}: expected 100 cases and {counts}, got {len(cases)} / {status_counts(cases)}")
        if any(not case.get("query", "").strip() for case in cases):
            fail(errors, f"{suite}: blank query")
        check_suite_manifest(errors, suite)
    check_candidate_manifest(errors, candidates)
    check_financial(errors, candidates["financial_statements"])
    check_historical(errors, candidates["historical_price"])
    check_realtime(errors, candidates["realtime_quote"])
    if errors:
        print("FAIL")
        print("\n".join(errors))
        return 1
    print("PASS: v1 immutable; v2 candidate/Oracle/manifests and financial, historical, realtime contracts are coherent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

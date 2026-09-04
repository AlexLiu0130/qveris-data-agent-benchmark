"""Compile the frozen 30-case financial diagnostic without executing it.

The candidate suite is useful for prompts and semantic intent only.  Every
score-bearing datum is derived from the frozen fact-contract release.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


SELECTION_PATH = Path("benchmarks/diagnostics/financial-statements-30.v1.json")
CANDIDATE_PATH = Path("benchmarks/candidates/v0.1/financial_statements.cases.json")
REGISTRY_PATH = Path("benchmarks/oracles/v1/fact-contracts.financial.v1.json")
FREEZE_ROOT = Path("benchmarks/oracles/v1")
OPERATORS = ("exact", "within_abs", "exact_normalized", "canonical_zero_from_display_nil")
METRICS = ("semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage")


class FinancialDiagnosticError(ValueError):
    """The diagnostic source release cannot safely be compiled."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FinancialDiagnosticError(message)


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise FinancialDiagnosticError("diagnostic value is not canonical JSON") from error


def digest(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise FinancialDiagnosticError("cannot read frozen source: %s" % path) from error


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise FinancialDiagnosticError("cannot read JSON source: %s" % path) from error


def _inside(root: Path, relative: str) -> Path:
    _require(type(relative) is str and relative, "frozen source path is missing")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise FinancialDiagnosticError("frozen source path escapes diagnostic root") from error
    return path


def _review_decisions(ledger: Mapping[str, Any], oracle_id: str, oracle_file_sha256: str) -> None:
    _require(ledger.get("oracle_id") == oracle_id and ledger.get("oracle_file_sha256") == oracle_file_sha256, "review ledger does not bind the frozen Oracle")
    reviews = ledger.get("reviews")
    _require(type(reviews) is list, "review ledger reviews are invalid")
    approved = {(review.get("role"), review.get("decision")) for review in reviews if type(review) is dict}
    _require({("data_reviewer", "approved"), ("semantic_reviewer", "approved")} <= approved, "frozen Oracle lacks required reviewer approvals")


def _semantic_assertions(intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project the candidate intent into stable leaf assertions without coercion."""
    assertions: list[dict[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if type(value) is dict:
            for key in sorted(value):
                _require(type(key) is str and key, "expected intent has an invalid path")
                visit(value[key], path + "." + key)
        else:
            assertions.append({"path": path, "operator": "exact", "expected": deepcopy(value), "tolerance": None, "weight": 1, "fatal": True})

    visit(intent, "resolved_request")
    _require(assertions, "candidate expected intent has no semantic assertions")
    return assertions


def _derived_data_assertions(source_atoms: Any, case_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _require(type(source_atoms) is list and source_atoms, "frozen Oracle has no atomic assertions")
    projected, provenance = [], []
    for source in source_atoms:
        _require(type(source) is dict, "frozen Oracle assertion is invalid")
        scoped_cases = source.get("case_ids")
        _require(scoped_cases is None or (type(scoped_cases) is list and case_id in scoped_cases), "frozen assertion does not cover its case")
        operator, field = source.get("comparison"), source.get("field")
        _require(operator in OPERATORS and type(field) is str and field, "frozen assertion has an unsupported operator or field")
        _require("expected" in source and type(source.get("assertion_id")) is str and source["assertion_id"], "frozen assertion is missing identity or expected value")
        if operator == "exact_normalized":
            _require(type(source.get("unit")) is str and source["unit"], "exact_normalized assertion must declare a unit")
        if operator == "canonical_zero_from_display_nil":
            _require(source.get("expected") == 0 and source.get("raw_display") == "–", "display-nil assertion must freeze en-dash zero")
        # This is the strict scorer adapter.  Values are carried in
        # `data.facts[]` and selected by assertion_id, never by parsing the
        # frozen field (which may contain punctuation such as `|` or `/`).
        item = {
            "response_root": "data",
            "assertion_id": source["assertion_id"],
            "field": field,
            "operator": operator,
            "expected": deepcopy(source["expected"]),
            "tolerance": None,
            # Diagnostic-only equal weighting/fatality.  It is not an
            # official scoring-weight decision.
            "weight": 1,
            "fatal": True,
            "currency": source["currency"],
            "unit": source["unit"],
            "period": source["period"],
        }
        if "raw_display" in source:
            item["raw_display"] = source["raw_display"]
        projected.append(item)
        provenance_item = deepcopy(source)
        if operator == "canonical_zero_from_display_nil":
            provenance_item["display_nil_evidence"] = True
        provenance.append(provenance_item)
    _require(projected, "derived Oracle has no assertions for its case")
    return projected, provenance


def _scoring_policy() -> dict[str, Any]:
    contracts = {
        "success": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]},
        "partial": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]},
        "needs_clarification": {"required_non_null_paths": ["clarification"], "required_null_paths": ["data", "terminal_reason"]},
        "unsupported": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
        "no_data": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
        "error": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
    }
    return {
        "schema_version": "financial-diagnostic-score-policy/v1",
        "metric_names": list(METRICS),
        "percentile_method": "nearest_rank",
        "assertion_operators": list(OPERATORS),
        "operator_registry": list(OPERATORS),
        "case_pass_gate": ["schema_valid", "status_correct", "semantic_pass", "data_pass", "execution_complete"],
        "completeness": {},
        "response_schema_version": "get-response/v1",
        "response_status_contracts": contracts,
        "max_reference_window_seconds": 0,
        "error": "disabled",
        "timeout_latency_treatment": "observed",
        "usage_receipt_required_fields": ["receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"],
        "trusted_receipt_issuers": ["qveris-gateway"],
        "eligibility": None,
        "ranking": None,
    }


def compile_financial_diagnostic(root: Path | str = Path("."), *, variants: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Return deterministic Runner input plus per-case frozen diagnostic inputs.

    This function is intentionally pure: it never writes artifacts and never
    executes a Runner, scorer, gateway, or provider.
    """
    _require(type(variants) is list and variants, "diagnostic requires explicit variant identities")
    root = Path(root).resolve()
    selection_path = _inside(root, str(SELECTION_PATH))
    candidate_path = _inside(root, str(CANDIDATE_PATH))
    registry_path = _inside(root, str(REGISTRY_PATH))
    selection = _load_json(selection_path)
    _require(type(selection) is dict and selection.get("status") == "diagnostic_non_formal_non_ranking", "selection is not explicitly diagnostic and non-ranking")
    selected = selection.get("selected_case_ids")
    _require(type(selected) is list and len(selected) == 30 and len(selected) == len(set(selected)), "selection must contain exactly 30 unique cases")
    _require(selection.get("candidate_suite_size") == 100, "selection must bind the 100-case suite")

    release = selection.get("frozen_financial_release")
    _require(type(release) is dict and type(release.get("release_id")) is str and release["release_id"], "selection lacks a frozen release binding")
    manifest_path = _inside(root, release.get("manifest_path", ""))
    release_registry_path = _inside(root, release.get("fact_contract_registry_path", ""))
    _require(_sha256_file(manifest_path) == release.get("manifest_sha256"), "frozen release manifest hash mismatch")
    _require(_sha256_file(release_registry_path) == release.get("fact_contract_registry_sha256"), "frozen fact-contract registry hash mismatch")
    _require(release_registry_path == registry_path, "selection registry path does not match compiler registry")
    manifest, registry = _load_json(manifest_path), _load_json(registry_path)
    _require(type(manifest) is dict and manifest.get("status") == "frozen" and manifest.get("release_id") == release["release_id"], "frozen release identity mismatch")
    _require(type(registry) is dict and registry.get("status") == "frozen" and registry.get("contract_count") == 27 and type(registry.get("contracts")) is list, "fact-contract registry is not frozen")

    manifest_files: dict[str, str] = {}
    for entry in manifest.get("files", []):
        _require(type(entry) is dict and type(entry.get("path")) is str and type(entry.get("sha256")) is str, "frozen manifest entry is invalid")
        path = _inside(root / FREEZE_ROOT, entry["path"])
        _require(_sha256_file(path) == entry["sha256"], "frozen manifest hash mismatch: %s" % entry["path"])
        manifest_files[entry["path"]] = entry["sha256"]

    candidates = _load_json(candidate_path)
    _require(type(candidates) is list and len(candidates) == 100, "financial candidate suite must contain 100 cases")
    candidates_by_id = {candidate.get("id"): candidate for candidate in candidates if type(candidate) is dict}
    _require(len(candidates_by_id) == 100 and all(case_id in candidates_by_id for case_id in selected), "selected candidate case is missing")
    contracts = {contract.get("fact_contract_id"): contract for contract in registry["contracts"] if type(contract) is dict}
    _require(len(contracts) == 27, "fact-contract registry has duplicate or missing contracts")
    frozen_normal_ids = {
        case_id
        for contract in contracts.values()
        if contract.get("review_status") == "frozen"
        for case_id in contract.get("case_ids", [])
    }
    _require(len(frozen_normal_ids) == 80 and all(candidates_by_id[case_id].get("case_type") == "normal" for case_id in frozen_normal_ids), "frozen release must cover 80 normal cases")

    oracle_records: dict[str, tuple[dict[str, Any], Path]] = {}
    review_ledgers: dict[str, tuple[dict[str, Any], Path]] = {}
    for path_text in manifest_files:
        path = _inside(root / FREEZE_ROOT, path_text)
        if path_text.endswith("/oracles.json"):
            for oracle in _load_json(path):
                _require(type(oracle) is dict and type(oracle.get("oracle_id")) is str and oracle["oracle_id"] not in oracle_records, "frozen Oracle identity is invalid")
                oracle_records[oracle["oracle_id"]] = (oracle, path)
        elif path_text.endswith("/review-ledger.json"):
            ledgers = _load_json(path)
            _require(type(ledgers) is dict and type(ledgers.get("review_ledgers")) is list, "review ledger file is invalid")
            for ledger in ledgers["review_ledgers"]:
                _require(type(ledger) is dict and type(ledger.get("oracle_id")) is str and ledger["oracle_id"] not in review_ledgers, "review ledger identity is invalid")
                review_ledgers[ledger["oracle_id"]] = (ledger, path)

    compiled_cases: list[dict[str, Any]] = []
    derived_oracles: dict[str, dict[str, Any]] = {}
    raw_assertion_provenance: dict[str, list[dict[str, Any]]] = {}
    source_bindings: list[dict[str, Any]] = []
    assertion_count, selected_contracts = 0, set()
    for case_id in selected:
        candidate = candidates_by_id[case_id]
        _require(candidate.get("case_type") == "normal" and candidate.get("expected_status") == "success", "diagnostic case is not a normal success case: %s" % case_id)
        _require(type(candidate.get("query")) is str and candidate["query"].strip() and type(candidate.get("expected_intent")) is dict, "candidate lacks canonical query or intent")
        contract = contracts.get(candidate.get("fact_contract_ref"))
        _require(type(contract) is dict and contract.get("review_status") == "frozen" and case_id in contract.get("case_ids", []), "case does not bind a frozen fact contract: %s" % case_id)
        source = contract.get("source_oracle")
        _require(type(source) is dict and source.get("status") == "frozen", "fact contract source is not frozen")
        oracle_id, source_path_text = source.get("oracle_id"), source.get("path")
        _require(type(oracle_id) is str and type(source_path_text) is str, "fact contract source binding is invalid")
        oracle, oracle_path = oracle_records.get(oracle_id, (None, None))
        _require(type(oracle) is dict and oracle_path is not None and oracle.get("status") == "frozen" and oracle.get("fact_contract_ref") == contract["fact_contract_id"] and case_id in oracle.get("case_ids", []), "frozen Oracle binding mismatch: %s" % case_id)
        _require(oracle_path == _inside(root, source_path_text), "fact contract Oracle path is not frozen-manifest path")
        ledger, ledger_path = review_ledgers.get(oracle_id, (None, None))
        _require(type(ledger) is dict and ledger_path is not None, "frozen Oracle has no review ledger")
        _review_decisions(ledger, oracle_id, _sha256_file(oracle_path))
        data_assertions, source_assertions = _derived_data_assertions(oracle.get("atomic_assertions"), case_id)
        derived_id = "financial-diagnostic-" + case_id.lower()
        _require(derived_id not in derived_oracles, "derived Oracle identity is duplicated")
        source_binding = {
            "release_id": release["release_id"],
            "selection_sha256": _sha256_file(selection_path),
            "candidate_sha256": digest(candidate),
            "fact_contract_id": contract["fact_contract_id"],
            "fact_contract_registry_sha256": _sha256_file(registry_path),
            "source_oracle_id": oracle_id,
            "source_oracle_path": source_path_text,
            "source_oracle_sha256": _sha256_file(oracle_path),
            "review_ledger_path": str(ledger_path.relative_to(root)),
            "review_ledger_sha256": _sha256_file(ledger_path),
            "review_status": "frozen",
        }
        derived_oracles[derived_id] = {
            "oracle_id": derived_id,
            "case_id": case_id,
            "independence": "independent_frozen",
            "semantic_review_status": "approved",
            "data_review_status": "approved",
            "state_review_status": "not_applicable",
            "semantic_assertions": _semantic_assertions(candidate["expected_intent"]),
            "data_assertions": data_assertions,
            "state_assertions": [],
            "reference_evidence": None,
            "source_ref": oracle_id,
            "version": release["release_id"],
        }
        raw_assertion_provenance[derived_id] = source_assertions
        compiled_cases.append({
            "case_id": case_id,
            "suite": "financial_statements",
            "query": candidate["query"],
            "score_case": {"expected_status": ["success"], "oracle_id": derived_id, "case_type": "normal"},
            "canonical_request": deepcopy(candidate["expected_intent"]),
            "diagnostic_binding": source_binding,
        })
        source_bindings.append({"case_id": case_id, "oracle_id": derived_id, "source_oracle_id": oracle_id, "source_oracle_sha256": source_binding["source_oracle_sha256"]})
        assertion_count += len(data_assertions)
        selected_contracts.add(contract["fact_contract_id"])

    _require(len(selected_contracts) == 27, "selected diagnostic must span 27 frozen contracts")
    _require(assertion_count == 1347, "selected diagnostic must project 1347 frozen data assertions")
    oracle_bundle = {
        "schema_version": "financial-diagnostic-oracle-bundle/v1",
        "oracles": derived_oracles,
    }
    scoring_policy = _scoring_policy()
    policy_digest, oracle_bundle_digest = digest(scoring_policy), digest(oracle_bundle)
    freeze_binding = {
        "selection_sha256": _sha256_file(selection_path),
        "release_id": release["release_id"],
        "release_manifest_sha256": _sha256_file(manifest_path),
        "fact_contract_registry_sha256": _sha256_file(registry_path),
        "sources": source_bindings,
    }
    run_config = {
        "run_id": "financial-statements-30-diagnostic-v1",
        "mode": "diagnostic",
        "freeze_digest": digest(freeze_binding),
        "policy": {"version": scoring_policy["schema_version"], "scope": "diagnostic_non_formal_non_ranking"},
        "timeout_ms": 30000,
        "concurrency": 1,
        "variants": deepcopy(variants),
        "cases": compiled_cases,
    }
    from .benchmark_scorer import SCORER_DIGEST, SCORER_VERSION
    from .run_backend import _variant_contract_digest
    run_config["scoring_contract"] = {
        "policy_digest": policy_digest,
        "oracle_bundle_digest": oracle_bundle_digest,
        "scorer_version": SCORER_VERSION,
        "scorer_digest": SCORER_DIGEST,
        "variant_contract_digest": _variant_contract_digest(run_config["variants"]),
    }
    return {
        "schema_version": "financial-diagnostic-compiled/v1",
        "diagnostic_id": selection.get("diagnostic_id"),
        "status": "diagnostic_non_formal_non_ranking",
        "source_summary": {"candidate_cases": 100, "frozen_normal_cases": 80, "selected_cases": 30, "selected_contracts": 27, "scoring_assertions": 1347, "release_id": release["release_id"]},
        "scoring_policy": scoring_policy,
        "scoring_policy_digest": policy_digest,
        "oracle_bundle": oracle_bundle,
        "oracle_bundle_digest": oracle_bundle_digest,
        "frozen_assertion_provenance": raw_assertion_provenance,
        "diagnostic_assumptions": {
            "assertion_weight": 1,
            "assertion_fatal": True,
            "scope": "diagnostic_only_not_an_official_weight_or_threshold_decision",
            "data_carrier": "response.data.facts matched by assertion_id",
        },
        "run_config": run_config,
        "compiled_digest": "",  # Filled below so the digest does not hash itself.
    }


def compile_with_digest(root: Path | str = Path("."), *, variants: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Compile and attach a digest over every serializable input except itself."""
    compiled = compile_financial_diagnostic(root, variants=variants)
    compiled["compiled_digest"] = digest({key: value for key, value in compiled.items() if key != "compiled_digest"})
    return compiled

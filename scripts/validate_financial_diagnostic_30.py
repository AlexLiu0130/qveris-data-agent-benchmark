#!/usr/bin/env python3
"""Read-only integrity gate for the non-formal 30-case financial diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from stat import S_ISREG


SELECTION = Path("benchmarks/diagnostics/financial-statements-30.v1.json")
CANDIDATES = Path("benchmarks/candidates/v0.1/financial_statements.cases.json")
FREEZE = Path("benchmarks/oracles/v1")


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_file(root: Path, relative: object, label: str) -> Path:
    """Resolve a declared source only when it remains a real local file."""
    require(type(relative) is str and relative, "%s path is missing" % label)
    relative_path = Path(relative)
    require(not relative_path.is_absolute() and ".." not in relative_path.parts, "%s path must be a contained relative path" % label)
    root = root.resolve()
    candidate = root.joinpath(*relative_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError("%s path escapes its root" % label) from error
    current = root
    for part in relative_path.parts:
        current = current / part
        require(not current.is_symlink(), "%s path may not use symlinks" % label)
    try:
        require(S_ISREG(resolved.stat().st_mode), "%s path must name a regular file" % label)
    except OSError as error:
        raise ValueError("%s path cannot be read" % label) from error
    return resolved


def frozen_file(root: Path, relative: object, label: str) -> Path:
    require(type(relative) is str and relative, "%s path is missing" % label)
    return safe_file(root, str(FREEZE / Path(relative)), label)


def main(root: Path) -> dict[str, int | str]:
    root = root.resolve()
    selection_path = safe_file(root, str(SELECTION), "selection")
    candidate_path = safe_file(root, str(CANDIDATES), "candidate")
    selection = load(selection_path)
    selected = selection.get("selected_case_ids")
    require(selection.get("status") == "diagnostic_non_formal_non_ranking", "selection is not explicitly non-formal/non-ranking")
    require(isinstance(selected, list) and len(selected) == 30 and len(selected) == len(set(selected)), "selection must have exactly 30 unique ids")
    require(selection.get("candidate_suite_size") == 100, "selection must declare the 100-case candidate suite")
    release = selection.get("frozen_financial_release")
    require(isinstance(release, dict), "selection has no frozen release")
    freeze_manifest_path = safe_file(root, release.get("manifest_path"), "frozen manifest")
    registry_path = safe_file(root, release.get("fact_contract_registry_path"), "fact-contract registry")
    require(digest(freeze_manifest_path) == release["manifest_sha256"], "frozen manifest hash mismatch")
    require(digest(registry_path) == release["fact_contract_registry_sha256"], "fact-contract registry hash mismatch")
    freeze_manifest, registry = load(freeze_manifest_path), load(registry_path)
    require(freeze_manifest.get("status") == "frozen" and freeze_manifest.get("release_id") == release["release_id"], "frozen release mismatch")
    require(registry.get("status") == "frozen" and registry.get("contract_count") == 27, "registry must be 27 frozen contracts")
    for item in freeze_manifest.get("files", []):
        require(type(item) is dict and type(item.get("path")) is str and type(item.get("sha256")) is str, "freeze manifest entry is invalid")
        path = frozen_file(root, item["path"], "freeze manifest entry")
        require(digest(path) == item["sha256"], "freeze manifest hash mismatch: %s" % item["path"])
    candidates = load(candidate_path)
    require(isinstance(candidates, list) and len(candidates) == 100, "financial candidate file must contain 100 cases")
    by_case = {item["id"]: item for item in candidates}
    require(len(by_case) == 100 and all(case_id in by_case for case_id in selected), "selected candidate case missing")
    contracts = {item["fact_contract_id"]: item for item in registry["contracts"]}
    frozen_normal = {case_id for contract in contracts.values() if contract.get("review_status") == "frozen" for case_id in contract.get("case_ids", [])}
    require(len(contracts) == 27 and len(frozen_normal) == 80 and all(by_case[case_id].get("case_type") == "normal" for case_id in frozen_normal), "registry must cover 80 frozen normal cases")
    records, reviews = {}, {}
    for item in freeze_manifest["files"]:
        if item["path"].endswith("/oracles.json"):
            path = frozen_file(root, item["path"], "freeze manifest entry")
            for oracle in load(path): records[oracle["oracle_id"]] = (oracle, path)
        if item["path"].endswith("/review-ledger.json"):
            path = frozen_file(root, item["path"], "freeze manifest entry")
            for ledger in load(path)["review_ledgers"]: reviews[ledger["oracle_id"]] = ledger
    assertion_count, selected_contracts = 0, set()
    for case_id in selected:
        case = by_case[case_id]
        require(case.get("case_type") == "normal", "diagnostic includes non-normal case %s" % case_id)
        contract = contracts.get(case.get("fact_contract_ref"))
        require(contract is not None and contract.get("review_status") == "frozen" and case_id in contract["case_ids"], "case is not bound to frozen contract %s" % case_id)
        selected_contracts.add(contract["fact_contract_id"])
        source = contract["source_oracle"]
        oracle, path = records.get(source["oracle_id"], (None, None))
        source_path = safe_file(root, source.get("path"), "fact-contract source Oracle")
        require(oracle is not None and path == source_path and oracle.get("status") == "frozen" and oracle.get("fact_contract_ref") == contract["fact_contract_id"] and case_id in oracle["case_ids"], "frozen oracle binding mismatch %s" % case_id)
        ledger = reviews.get(oracle["oracle_id"])
        decisions = {(review.get("role"), review.get("decision")) for review in ledger.get("reviews", [])} if isinstance(ledger, dict) else set()
        require(isinstance(ledger, dict) and ledger.get("oracle_file_sha256") == digest(path) and {("data_reviewer", "approved"), ("semantic_reviewer", "approved")} <= decisions, "review gate failed %s" % case_id)
        atoms = oracle.get("atomic_assertions")
        require(isinstance(atoms, list) and atoms, "oracle has no assertions %s" % case_id)
        for atom in atoms:
            require(atom.get("comparison") in {"exact", "exact_normalized", "canonical_zero_from_display_nil"}, "unsupported frozen assertion operator")
            require(atom.get("case_ids") is None or case_id in atom["case_ids"], "assertion does not cover selected case")
            if atom.get("comparison") == "canonical_zero_from_display_nil":
                require(atom.get("expected") == 0 and atom.get("raw_display") == "–", "display nil must be frozen en-dash zero")
        assertion_count += len(atoms)
    require(len(selected_contracts) == 27, "selected 30 cases must span 27 frozen contracts")
    require(assertion_count == 1347, "selected 30 cases must project 1347 frozen scoring assertions")
    return {"candidate_cases": len(candidates), "frozen_normal_cases": len(frozen_normal), "selected_cases": len(selected), "selected_contracts": len(selected_contracts), "scoring_assertions": assertion_count, "release_id": release["release_id"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    try:
        print(json.dumps(main(parser.parse_args().root), sort_keys=True))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        raise SystemExit("FAIL: %s" % error)

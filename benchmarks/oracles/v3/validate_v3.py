#!/usr/bin/env python3
"""Small integrity gate for the final v0.3/v3 package.

v2 remains the immutable baseline; this gate only verifies the new layer and
its manifest hash chain.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "benchmarks"
SUITES = ("financial_statements", "historical_price", "realtime_quote")
METRICS = ["semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage"]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized_query(query: str) -> str:
    return "".join(char.casefold() for char in unicodedata.normalize("NFKC", query) if unicodedata.category(char)[0] in {"L", "N"})


def hash_entries(errors: list[str], label: str, entries: object, directory: Path = BASE) -> None:
    if type(entries) is not list or not entries:
        errors.append(f"{label}: missing hash entries")
        return
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"path", "sha256"} or type(entry["path"]) is not str or not SHA256.fullmatch(str(entry["sha256"])):
            errors.append(f"{label}: invalid hash entry")
            continue
        path = directory / entry["path"]
        if not path.is_file() or digest(path) != entry["sha256"]:
            errors.append(f"{label}: hash mismatch {entry['path']}")


def duplicate(errors: list[str], label: str, values: list[tuple[str, str]]) -> None:
    seen: dict[str, str] = {}
    for key, case_id in values:
        previous = seen.setdefault(key, case_id)
        if previous != case_id:
            errors.append(f"{label}: duplicate {previous}/{case_id}")


def main() -> int:
    errors: list[str] = []
    baseline = subprocess.run([sys.executable, str(BASE / "oracles/v2/validate_v2.py")], cwd=ROOT, capture_output=True, text=True)
    if baseline.returncode:
        errors.append("v2 baseline validator failed: " + (baseline.stdout + baseline.stderr).strip())
    manifest_path = BASE / "candidates/v0.3/manifest.json"
    if not manifest_path.is_file():
        errors.append("v0.3 candidate manifest is missing")
    else:
        manifest = load(manifest_path)
        if manifest.get("schema_version") != "candidate-manifest/v0.3" or manifest.get("metrics") != METRICS:
            errors.append("v0.3 candidate manifest identity or metrics")
        hash_entries(errors, "v0.3 candidate files", manifest.get("files"))
        hash_entries(errors, "v0.3 suite manifests", manifest.get("suite_oracle_manifests"))
        spec = {item.get("suite"): item for item in manifest.get("suites", []) if type(item) is dict}
        if set(spec) != set(SUITES):
            errors.append("v0.3 candidate manifest suite set")
        all_queries: list[tuple[str, str]] = []
        financial_combinations: list[tuple[str, str]] = []
        historical_combinations: list[tuple[str, str]] = []
        for suite in SUITES:
            candidate_path = BASE / f"candidates/v0.3/{suite}.cases.json"
            oracle_path = BASE / f"oracles/v3/outputs/{suite}/oracles.json"
            suite_manifest_path = BASE / f"oracles/v3/outputs/{suite}/manifest.json"
            if not candidate_path.is_file() or not oracle_path.is_file() or not suite_manifest_path.is_file():
                errors.append(f"{suite}: required v3 file is missing")
                continue
            cases, oracle_document, suite_manifest = load(candidate_path), load(oracle_path), load(suite_manifest_path)
            oracles = oracle_document.get("oracles") if type(oracle_document) is dict else None
            if type(cases) is not list or type(oracles) is not list or len(cases) != 100 or len(oracles) != 100:
                errors.append(f"{suite}: requires exactly 100 candidates and Oracles")
                continue
            if suite_manifest.get("schema_version") != "benchmark-v3-manifest/v1" or suite_manifest.get("suite") != suite:
                errors.append(f"{suite}: manifest identity")
            for group in ("candidate_files", "policy_files"):
                hash_entries(errors, f"{suite} {group}", suite_manifest.get(group))
            for name, entries in suite_manifest.items():
                if name.endswith("_bindings"):
                    hash_entries(errors, f"{suite} {name}", entries)
            case_map = {case.get("case_id"): case for case in cases if type(case) is dict}
            oracle_map = {oracle.get("case_id"): oracle for oracle in oracles if type(oracle) is dict}
            if None in case_map or None in oracle_map or len(case_map) != 100 or len(oracle_map) != 100 or set(case_map) != set(oracle_map):
                errors.append(f"{suite}: case_id alignment")
                continue
            if spec.get(suite, {}).get("cases") != 100 or spec.get(suite, {}).get("expected_status_counts") != dict(Counter(case.get("expected_status") for case in cases)):
                errors.append(f"{suite}: manifest counts")
            for case_id, case in case_map.items():
                oracle = oracle_map[case_id]
                if case.get("suite") != suite or not isinstance(case.get("query"), str) or not case["query"].strip() or case.get("expected_status") != oracle.get("expected_status"):
                    errors.append(f"{suite} {case_id}: shape or expected_status")
                all_queries.append((normalized_query(case.get("query", "")), f"v0.3:{case_id}"))
                if suite == "financial_statements" and case.get("expected_status") == "success":
                    data = case.get("data_oracle", {})
                    if type(data) is not dict or type(data.get("oracle_id")) is not str or type(data.get("assertion_ids")) is not list:
                        errors.append(f"financial {case_id}: reported fact combination")
                    else:
                        financial_combinations.append((canonical([data["oracle_id"], sorted(data["assertion_ids"])]), case_id))
                if suite == "historical_price" and case.get("expected_status") == "success":
                    contract = oracle.get("contract")
                    if type(contract) is not dict:
                        errors.append(f"historical {case_id}: missing contract")
                    else:
                        historical_combinations.append((canonical(contract), case_id))
        for suite in SUITES:
            old = BASE / f"candidates/v0.2/{suite}.cases.json"
            if old.is_file():
                all_queries.extend((normalized_query(case.get("query", "")), f"v0.2:{case.get('case_id')}") for case in load(old) if type(case) is dict)
        duplicate(errors, "normalized query across v0.2/v0.3", all_queries)
        duplicate(errors, "financial reported fact combination", financial_combinations)
        duplicate(errors, "historical contract combination", historical_combinations)
    if errors:
        print("FAIL")
        print("\n".join(errors))
        return 1
    print("PASS: v2 baseline and v0.3/v3 candidate, Oracle, manifest and uniqueness contracts are coherent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

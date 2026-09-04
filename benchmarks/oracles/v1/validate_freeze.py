#!/usr/bin/env python3
"""Dependency-free gate for an Oracle freeze v1 package.

This checks evidence contracts, not the underlying market facts.  A package
with evidence-collected records is reported as INCOMPLETE, never as PASS.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
SCHEMAS = ROOT / "schemas"
OUTPUTS = ROOT / "outputs"
REQUIRED_SCHEMAS = {
    "oracle.schema.json": "oracle/v1",
    "evidence-receipt.schema.json": "evidence-receipt/v1",
    "review-ledger.schema.json": "review-ledger/v1",
    "manifest.schema.json": "oracle-manifest/v1",
    "financial-fact-contract-registry.schema.json": "financial-statement-fact-contract/v1",
}
SUITES = {"historical_price", "financial_statements", "realtime_quote"}
STATUSES = {"draft", "evidence_collected", "under_review", "conflict", "frozen", "rejected", "superseded"}
SHA256_LENGTH = 64
FINANCIAL_AUTHORITY_SOURCE_CLASSES = {"issuer_or_regulator", "exchange"}


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.gaps: list[str] = []
        self.frozen = 0
        self.incomplete = 0

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def gap(self, message: str) -> None:
        self.gaps.append(message)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == SHA256_LENGTH and all(char in "0123456789abcdef" for char in value)


def iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def strings(record: dict[str, Any], fields: Iterable[str]) -> list[str]:
    return [field for field in fields if not isinstance(record.get(field), str) or not record[field].strip()]


def records(value: Any, key: str, path: Path, report: Report) -> list[dict[str, Any]]:
    """Read both legacy arrays and named arrays (or a single record)."""
    if isinstance(value, list):
        value = value
    elif isinstance(value, dict) and isinstance(value.get(key), list):
        value = value[key]
    elif isinstance(value, dict) and {
        "oracles": "oracle/v1", "receipts": "evidence-receipt/v1", "review_ledgers": "review-ledger/v1",
    }.get(key) == value.get("schema_version"):
        value = [value]
    elif isinstance(value, dict) and key == "review_ledgers":
        # Keep legacy reviewer notes visible as gaps; they simply cannot approve
        # a frozen record until upgraded to review-ledger/v1.
        value = [value]
    else:
        report.fail(f"{path}: expected array or object containing {key}")
        return []
    if not all(isinstance(item, dict) for item in value):
        report.fail(f"{path}: {key} must contain objects")
        return []
    return value


def fact_contract_present(record: dict[str, Any]) -> bool:
    if isinstance(record.get("fact_contract_ref"), str) and record["fact_contract_ref"].strip():
        return True
    contract = record.get("fact_contract")
    return isinstance(contract, dict) and not strings(contract, (
        "entity", "symbol", "exchange", "form", "accession", "period_end",
        "duration_or_instant", "consolidation_scope", "reported_currency",
        "accounting_standard", "unit_scale", "complete_statement_schema_id",
    ))


def normalized_registry_receipts(source: Any) -> tuple[str, list[str]] | None:
    """Return the registry's unambiguous primary/corroborator receipt IDs."""
    if not isinstance(source, dict):
        return None
    primary = source.get("primary_receipt_id")
    singular = source.get("corroboration_receipt_id")
    plural = source.get("corroboration_receipt_ids")
    if not isinstance(primary, str) or not primary.strip() or (singular is not None and plural is not None):
        return None
    if singular is not None:
        values = [singular]
    elif plural is not None:
        values = plural
    else:
        return None
    if not isinstance(values, list) or not values or not all(isinstance(value, str) and value.strip() for value in values):
        return None
    normalized = sorted(value.strip() for value in values)
    primary = primary.strip()
    if len(normalized) != len(set(normalized)) or primary in normalized:
        return None
    return primary, normalized


def financial_registry_contract_valid(item: dict[str, Any]) -> bool:
    """Guard every field projected into a financial Oracle contract."""
    required = (
        "fact_contract_id", "market", "statement_type", "reporting_basis", "exchange", "form", "accession",
        "duration_or_instant", "consolidation_scope", "accounting_standard", "currency", "reported_currency",
        "unit_scale", "complete_statement_schema_id",
    )
    if strings(item, required):
        return False
    case_ids = item.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids or not all(isinstance(case_id, str) and case_id.strip() for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
        return False
    entity, period, pit, presentation = item.get("entity"), item.get("period"), item.get("point_in_time"), item.get("presentation_scope")
    if not isinstance(entity, dict) or strings(entity, ("name", "symbol")):
        return False
    if not isinstance(period, dict) or strings(period, ("label", "period_end")):
        return False
    if not isinstance(pit, dict) or strings(pit, ("information_available_at", "source_published_at")):
        return False
    if not isinstance(presentation, dict):
        return False
    primary_period, comparative_periods = presentation.get("primary_period"), presentation.get("comparative_periods")
    if not isinstance(primary_period, dict) or strings(primary_period, ("assertion_period", "label", "period_end")):
        return False
    if not isinstance(comparative_periods, list) or not all(isinstance(period_id, str) and period_id.strip() for period_id in comparative_periods) or len(comparative_periods) != len(set(comparative_periods)):
        return False
    if not isinstance(presentation.get("required_as_filed"), bool):
        return False
    if "field_unit_exceptions" in item and not isinstance(item["field_unit_exceptions"], list):
        return False
    return True


def collect_financial_contracts(report: Report, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read the evaluator-owned fact registry used by financial Oracle refs."""
    path = path or ROOT / "fact-contracts.financial.v1.json"
    try:
        value = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        report.fail(f"{path}: unreadable financial fact contracts: {error}")
        return {}
    contracts = value.get("contracts") if isinstance(value, dict) else None
    if not isinstance(contracts, list):
        report.fail(f"{path}: contracts must be an array")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in contracts:
        contract_id = item.get("fact_contract_id") if isinstance(item, dict) else None
        if not isinstance(contract_id, str) or not contract_id or contract_id in result:
            report.fail(f"{path}: fact_contract_id must be unique and non-empty")
            continue
        source = item.get("source_oracle")
        if not financial_registry_contract_valid(item):
            report.fail(f"{path}:{contract_id}: registry_projection_contract_invalid")
            continue
        if not isinstance(source, dict) or strings(source, ("oracle_id", "path")) or normalized_registry_receipts(source) is None:
            report.fail(f"{path}:{contract_id}: registry_corroborator_invalid")
            continue
        result[contract_id] = item
    return result


def collect_historical_contracts(report: Report) -> dict[str, dict[str, Any]]:
    """Historical registries are optional because a complete inline contract is valid."""
    result: dict[str, dict[str, Any]] = {}
    candidates = [ROOT / "fact-contracts.historical.v1.json", *ROOT.glob("fact-contracts.historical.*.json")]
    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError) as error:
            report.fail(f"{path}: unreadable historical fact registry: {error}")
            continue
        items = value.get("contracts") if isinstance(value, dict) else None
        if not isinstance(items, list):
            report.fail(f"{path}: historical registry contracts must be an array")
            continue
        for item in items:
            contract_id = item.get("fact_contract_id") if isinstance(item, dict) else None
            if not isinstance(contract_id, str) or not contract_id or contract_id in result:
                report.fail(f"{path}: historical fact_contract_id must be unique and non-empty")
            else:
                result[contract_id] = item
    return result


def schema_check(report: Report) -> None:
    for name, version in REQUIRED_SCHEMAS.items():
        try:
            schema = load_json(SCHEMAS / name)
        except (OSError, json.JSONDecodeError) as error:
            report.fail(f"{name}: unreadable schema: {error}")
            continue
        if not isinstance(schema, dict) or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            report.fail(f"{name}: must declare JSON Schema draft 2020-12")
        if schema.get("properties", {}).get("schema_version", {}).get("const") != version:
            report.fail(f"{name}: wrong schema_version contract")


def json_paths(suite_dir: Path, names: set[str]) -> list[Path]:
    return sorted(path for path in suite_dir.rglob("*.json") if path.name in names)


def package_review_blocks(value: dict[str, Any]) -> bool:
    package_review = value.get("package_review")
    decision = package_review.get("counts", {}).get("package_release_decision") if isinstance(package_review, dict) else None
    package_conflicts = package_review.get("conflicts", []) if isinstance(package_review, dict) else []
    root_conflicts = value.get("conflicts", [])
    conflicts = (package_conflicts if isinstance(package_conflicts, list) else []) + (root_conflicts if isinstance(root_conflicts, list) else [])
    return decision not in {None, "approved"} or any(isinstance(conflict, dict) and conflict.get("status") == "open" for conflict in conflicts)


def collect_receipts(suite_dir: Path, report: Report) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    receipt_by_id: dict[str, dict[str, Any]] = {}
    path_by_id: dict[str, Path] = {}
    for path in json_paths(suite_dir, {"evidence-receipts.json", "receipts.json"}):
        try:
            items = records(load_json(path), "receipts", path, report)
        except (OSError, json.JSONDecodeError) as error:
            report.fail(f"{path}: unreadable receipts: {error}")
            continue
        for item in items:
            receipt_id = item.get("receipt_id")
            if item.get("schema_version") != "evidence-receipt/v1" or not isinstance(receipt_id, str) or not receipt_id:
                report.fail(f"{path}: invalid evidence receipt")
                continue
            if receipt_id in receipt_by_id:
                report.fail(f"{path}: duplicate receipt_id {receipt_id}")
                continue
            receipt_by_id[receipt_id], path_by_id[receipt_id] = item, path
            missing = strings(item, (
                "source_id", "source_class", "independence_group", "published_at", "retrieved_at", "content_sha256",
                "evidence_locator", "authoritative_origin_id", "timestamp_precision",
            ))
            lineage = item.get("source_lineage")
            if not isinstance(lineage, dict) or strings(lineage, ("origin_id", "delivery_channel", "extraction_method")):
                missing.append("source_lineage")
            if not isinstance(item.get("same_authoritative_origin"), bool):
                missing.append("same_authoritative_origin")
            if missing:
                report.gap(f"{path}:{receipt_id}: missing {', '.join(missing)}")
    return receipt_by_id, path_by_id


def collect_ledgers(suite_dir: Path, report: Report) -> tuple[dict[str, dict[str, Any]], dict[str, Path], set[Path]]:
    ledger_by_oracle: dict[str, dict[str, Any]] = {}
    path_by_oracle: dict[str, Path] = {}
    package_blocked: set[Path] = set()
    candidates = sorted(path for path in suite_dir.rglob("*.json") if "review-ledger" in path.name or path.name == "review.json")
    for path in candidates:
        try:
            value = load_json(path)
            items = records(value, "review_ledgers", path, report)
        except (OSError, json.JSONDecodeError) as error:
            report.fail(f"{path}: unreadable review ledger: {error}")
            continue
        if isinstance(value, dict):
            if package_review_blocks(value):
                package_blocked.add(path)
        for item in items:
            oracle_id = item.get("oracle_id")
            if item.get("schema_version") != "review-ledger/v1" or not isinstance(oracle_id, str) or not oracle_id:
                # Candidate review notes are allowed, but cannot satisfy a frozen gate.
                report.gap(f"{path}: legacy/incomplete review ledger; not eligible for frozen approval")
            elif oracle_id in ledger_by_oracle:
                report.fail(f"{path}: duplicate review ledger for {oracle_id}")
            else:
                ledger_by_oracle[oracle_id], path_by_oracle[oracle_id] = item, path
    return ledger_by_oracle, path_by_oracle, package_blocked


def collect_manifest(suite_dir: Path, report: Report) -> tuple[dict[str, str], list[tuple[Path, dict[str, Any]]]]:
    entries: dict[str, str] = {}
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(suite_dir.rglob("manifest.json")):
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError) as error:
            report.fail(f"{path}: unreadable manifest: {error}")
            continue
        if not isinstance(value, dict) or value.get("schema_version") != "oracle-manifest/v1" or not isinstance(value.get("files"), list):
            report.fail(f"{path}: invalid manifest")
            continue
        manifests.append((path, value))
        if not isinstance(value.get("release_id"), str) or not value["release_id"].strip() or iso_datetime(value.get("created_at")) is None:
            report.fail(f"{path}: release_id and timezone-aware created_at are required")
        for entry in value["files"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not is_sha256(entry.get("sha256")):
                report.fail(f"{path}: invalid manifest entry")
                continue
            target = ROOT / entry["path"]
            if not target.is_file() or digest(target) != entry["sha256"]:
                report.fail(f"{path}: manifest hash mismatch for {entry['path']}")
            entries[entry["path"]] = entry["sha256"]
    return entries, manifests


def is_canonical_zero(assertion: dict[str, Any]) -> bool:
    value = assertion.get("expected")
    return assertion.get("comparison") == "canonical_zero_from_display_nil" and not isinstance(value, bool) and isinstance(value, (int, float)) and value == 0


def receipt_confirms_display(receipt: dict[str, Any], assertion_id: str, glyph: str, confirmed_nil: bool) -> bool:
    evidence = receipt.get("display_nil_evidence")
    return isinstance(evidence, list) and any(
        isinstance(item, dict)
        and item.get("assertion_id") == assertion_id
        and item.get("raw_display") == glyph
        and item.get("confirmed_nil") is confirmed_nil
        and isinstance(item.get("evidence_locator"), str)
        and item["evidence_locator"].strip()
        for item in evidence
    )


def receipt_confirms_display_nil(receipt: dict[str, Any], assertion_id: str) -> bool:
    return receipt_confirms_display(receipt, assertion_id, "–", True)


def canonical_zero_gate(
    assertion: dict[str, Any], prefix: str, receipt_by_id: dict[str, dict[str, Any]],
    ledger: dict[str, Any] | None, approved_data_reviewers: set[Any], report: Report,
) -> None:
    """Allow one auditable display-nil normalization, and nothing broader."""
    assertion_id = str(assertion.get("assertion_id", "?"))
    if not is_canonical_zero(assertion):
        report.fail(f"{prefix}:{assertion_id}: canonical display nil must have numeric expected=0")
    if assertion.get("raw_display") != "–":
        report.fail(f"{prefix}:{assertion_id}: only standard en-dash display nil may canonicalize to zero")
    links = assertion.get("receipt_ids")
    if not isinstance(links, list) or len(links) < 2 or not all(isinstance(item, str) and item in receipt_by_id for item in links):
        report.fail(f"{prefix}:{assertion_id}: canonical zero needs two known receipt links")
        return
    confirmed = [receipt_by_id[item] for item in links if receipt_confirms_display_nil(receipt_by_id[item], assertion_id)]
    if len(confirmed) < 2:
        report.fail(f"{prefix}:{assertion_id}: each of two receipt links must retain raw glyph, locator, and nil confirmation")
    delivery_channels = {item.get("source_lineage", {}).get("delivery_channel") for item in confirmed}
    extraction_methods = {item.get("source_lineage", {}).get("extraction_method") for item in confirmed}
    if len(delivery_channels) < 2 and not ("independent_parser" in extraction_methods and len(extraction_methods) >= 2):
        report.fail(f"{prefix}:{assertion_id}: canonical zero needs independent delivery or independent-parser evidence")
    approvals = ledger.get("canonical_zero_approvals", []) if isinstance(ledger, dict) else []
    if not any(
        isinstance(item, dict)
        and item.get("assertion_id") == assertion_id
        and item.get("comparison") == "canonical_zero_from_display_nil"
        and item.get("decision") == "approved"
        and item.get("reviewer_id") in approved_data_reviewers
        for item in approvals
    ):
        report.fail(f"{prefix}:{assertion_id}: review ledger lacks explicit canonical-zero approval")


def exact_display_nil_gate(assertion: dict[str, Any], prefix: str, receipt_by_id: dict[str, dict[str, Any]], report: Report) -> None:
    """Preserve an explicitly displayed non-numeric nil; never turn it into zero."""
    assertion_id, glyph = str(assertion.get("assertion_id", "?")), assertion.get("raw_display")
    if assertion.get("expected") is not None or assertion.get("comparison") != "exact" or not isinstance(glyph, str) or not glyph:
        report.fail(f"{prefix}:{assertion_id}: display nil must be expected=null with comparison=exact and raw glyph")
        return
    links = assertion.get("receipt_ids")
    if not isinstance(links, list) or len(links) < 2 or not all(isinstance(item, str) and item in receipt_by_id for item in links):
        report.fail(f"{prefix}:{assertion_id}: exact display nil needs two known receipt links")
        return
    confirmed = [receipt_by_id[item] for item in links if receipt_confirms_display(receipt_by_id[item], assertion_id, glyph, False)]
    channels = {item.get("source_lineage", {}).get("delivery_channel") for item in confirmed}
    if len(confirmed) < 2 or len(channels) < 2:
        report.fail(f"{prefix}:{assertion_id}: expected=null display nil needs two same-glyph, non-numeric receipt locators from distinct deliveries")


def frozen_time_and_receipt_gate(record: dict[str, Any], receipts: list[dict[str, Any]], prefix: str, report: Report) -> None:
    frozen_at = iso_datetime(record.get("frozen_at"))
    point_in_time = record.get("point_in_time")
    if frozen_at is None or not isinstance(point_in_time, dict):
        report.fail(f"{prefix}: frozen_at and point_in_time must be timezone-aware ISO datetimes")
        return
    published = iso_datetime(point_in_time.get("source_published_at"))
    available = iso_datetime(point_in_time.get("information_available_at"))
    retrieved = iso_datetime(point_in_time.get("retrieved_at"))
    if published is None or available is None or published > available or available > frozen_at or (retrieved is not None and retrieved > frozen_at):
        report.fail(f"{prefix}: invalid PIT order (published <= available <= frozen_at; retrieved <= frozen_at)")
    for receipt in receipts:
        receipt_id = receipt.get("receipt_id", "?")
        receipt_published, receipt_retrieved = iso_datetime(receipt.get("published_at")), iso_datetime(receipt.get("retrieved_at"))
        if not is_sha256(receipt.get("content_sha256")):
            report.fail(f"{prefix}:{receipt_id}: content_sha256 must be 64 lowercase hex")
        if receipt_published is None or receipt_retrieved is None or receipt_published > receipt_retrieved or receipt_retrieved > frozen_at:
            report.fail(f"{prefix}:{receipt_id}: invalid receipt ISO/PIT order")


def origin_gate(record: dict[str, Any], authority: dict[str, Any], corroborating: list[dict[str, Any]], prefix: str, report: Report) -> None:
    origin = record.get("origin")
    if not isinstance(origin, str) or not origin or record.get("authoritative_origin_id") != origin:
        report.fail(f"{prefix}: record.origin must equal authoritative_origin_id")
        return
    record_lineage = record.get("source_lineage")
    if not isinstance(record_lineage, dict) or record_lineage.get("origin_id") != origin:
        report.fail(f"{prefix}: record source_lineage.origin_id must equal record.origin")
    receipts = [authority, *corroborating]
    for receipt in receipts:
        if receipt.get("source_lineage", {}).get("origin_id") != receipt.get("authoritative_origin_id"):
            report.fail(f"{prefix}:{receipt.get('receipt_id', '?')}: receipt lineage.origin_id must equal authoritative_origin_id")
    if authority.get("authoritative_origin_id") != origin:
        report.fail(f"{prefix}: authority authoritative_origin_id must equal record.origin")
    same_origin = record.get("same_authoritative_origin")
    if not isinstance(same_origin, bool):
        report.fail(f"{prefix}: same_authoritative_origin must be explicit boolean")
        return
    scope = record.get("corroboration_scope")
    if scope in {"same_authoritative_origin_multi_delivery", "same_authoritative_origin_independent_parse"}:
        if same_origin is not True or any(item.get("authoritative_origin_id") != origin or item.get("same_authoritative_origin") is not True for item in corroborating):
            report.fail(f"{prefix}: same-origin scope requires explicit same_authoritative_origin=true and one common origin")
    elif scope == "independent_fact_source":
        if same_origin is not False or not corroborating or not all(
            item.get("authoritative_origin_id") != origin
            and item.get("independence_group") != authority.get("independence_group")
            and item.get("same_authoritative_origin") is False
            for item in corroborating
        ):
            report.fail(f"{prefix}: independent scope requires different origin, independence_group, and explicit same_authoritative_origin=false")


def reviewers_are_independent(approved: list[dict[str, Any]]) -> tuple[bool, set[Any]]:
    authors = {item.get("reviewer_id") for item in approved if item.get("role") == "case_author"}
    semantic = {item.get("reviewer_id") for item in approved if item.get("role") == "semantic_reviewer"}
    data = {item.get("reviewer_id") for item in approved if item.get("role") == "data_reviewer"}
    return bool(semantic and data and not authors & semantic and not authors & data and not semantic & data), data


def suite_manifest_is_frozen(suite: str, manifests: list[tuple[Path, dict[str, Any]]]) -> bool:
    expected = OUTPUTS / suite / "manifest.json"
    return len(manifests) == 1 and manifests[0][0] == expected and manifests[0][1].get("status") == "frozen" and manifests[0][1].get("suite") == suite


def has_allowed_fact_contract(record: dict[str, Any], financial_contracts: dict[str, dict[str, Any]], historical_contracts: dict[str, dict[str, Any]]) -> bool:
    reference = record.get("fact_contract_ref")
    if record.get("suite") == "financial_statements":
        return isinstance(reference, str) and reference in financial_contracts
    return fact_contract_present(record) or (isinstance(reference, str) and reference in historical_contracts)


def financial_inline_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Project the evaluator registry into the Oracle v1 inline-contract shape."""
    inline = {
        "entity": contract["entity"]["name"],
        "symbol": contract["entity"]["symbol"],
        "exchange": contract["exchange"],
        "form": contract["form"],
        "accession": contract["accession"],
        "period_end": contract["period"]["period_end"],
        "duration_or_instant": contract["duration_or_instant"],
        "consolidation_scope": contract["consolidation_scope"],
        "accounting_standard": contract["accounting_standard"],
        "reported_currency": contract["reported_currency"],
        "unit_scale": contract["unit_scale"],
        "complete_statement_schema_id": contract["complete_statement_schema_id"],
        "presentation_scope": contract["presentation_scope"],
    }
    if "field_unit_exceptions" in contract:
        inline["field_unit_exceptions"] = contract["field_unit_exceptions"]
    return inline


def financial_registry_receipts_match(source: Any, evidence: Any) -> bool:
    expected = normalized_registry_receipts(source)
    if expected is None or not isinstance(evidence, dict):
        return False
    authority, corroborators = evidence.get("authoritative_receipt_id"), evidence.get("corroborating_receipt_ids")
    if not isinstance(authority, str) or not isinstance(corroborators, list) or not all(isinstance(receipt_id, str) and receipt_id.strip() for receipt_id in corroborators):
        return False
    return expected == (authority.strip(), sorted(receipt_id.strip() for receipt_id in corroborators))


def historical_sources_are_independent_licensed(sources: list[dict[str, Any]]) -> bool:
    return len({item.get("source_id") for item in sources}) >= 2 and len({item.get("independence_group") for item in sources}) >= 2 and len({item.get("authoritative_origin_id") for item in sources}) >= 2 and all(
        item.get("source_class") in {"independent_market_data", "licensed_market_data"}
        and isinstance(item.get("license_id"), str) and item["license_id"].strip()
        and item.get("same_authoritative_origin") is False
        and item.get("source_lineage", {}).get("origin_id") == item.get("authoritative_origin_id")
        for item in sources
    )


QUOTE_CAPTURE_FIELDS = {"provider_quote_timestamp", "captured_at", "market_session", "timezone", "currency", "unit"}


def quote_contract_gate(record: dict[str, Any], prefix: str, frozen: bool, report: Report) -> bool:
    """Validate realtime's live/replay boundary without creating a second ledger system."""
    contract = record.get("quote_contract")
    if not isinstance(contract, dict):
        report.fail(f"{prefix}: realtime_quote requires quote_contract")
        return False
    missing = strings(contract, ("snapshot_mode", "evaluation_use", "status_expectation", "reference_timing"))
    metadata = contract.get("capture_metadata")
    tolerances = contract.get("tolerances")
    exclusions = contract.get("scoring_exclusions")
    blocks = contract.get("block_reasons")
    if missing or not isinstance(metadata, list) or not QUOTE_CAPTURE_FIELDS.issubset(set(metadata)):
        report.fail(f"{prefix}: quote_contract lacks required capture metadata")
        return False
    if exclusions != ["provider_tool_parameters"]:
        report.fail(f"{prefix}: provider tool parameters must be excluded from hard scoring")
    if not isinstance(tolerances, dict) or set(tolerances) != {"staleness", "cross_source_skew", "tick"}:
        report.fail(f"{prefix}: quote_contract needs staleness, cross_source_skew, and tick policies")
        return False
    for name, policy in tolerances.items():
        if not isinstance(policy, dict) or policy.get("state") not in {"unbound_must_bind_before_capture", "bound"}:
            report.fail(f"{prefix}: {name} policy must be explicit")
        elif frozen and (policy.get("state") != "bound" or not isinstance(policy.get("value"), (int, float)) or isinstance(policy.get("value"), bool) or policy["value"] < 0 or not isinstance(policy.get("unit"), str) or not policy["unit"].strip()):
            report.fail(f"{prefix}: frozen realtime policy {name} requires a pre-bound non-negative value and unit")
    if not isinstance(blocks, list) or not all(isinstance(item, str) for item in blocks):
        report.fail(f"{prefix}: quote_contract block_reasons must be a string list")
        return False
    if contract.get("snapshot_mode") == "replay_fixture":
        if contract.get("evaluation_use") != "non_formal_replay_only":
            report.fail(f"{prefix}: replay_fixture may never enter formal data_accuracy")
    elif contract.get("snapshot_mode") != "live_bracketed":
        report.fail(f"{prefix}: unknown realtime snapshot_mode")
    if frozen:
        if contract.get("snapshot_mode") != "live_bracketed" or contract.get("evaluation_use") != "formal_data_accuracy" or blocks:
            report.fail(f"{prefix}: frozen realtime Oracle must be live_bracketed, formal, and unblocked")
    elif record.get("atomic_assertions"):
        report.fail(f"{prefix}: blocked realtime inventory must not contain numeric assertions")
    return not report.failures


def realtime_sources_are_independent_licensed(sources: list[dict[str, Any]], timing: str) -> bool:
    if not historical_sources_are_independent_licensed(sources):
        return False
    captures = [item.get("quote_capture") for item in sources]
    if not all(isinstance(capture, dict) and QUOTE_CAPTURE_FIELDS.issubset(capture) for capture in captures):
        return False
    roles = {capture.get("reference_role") for capture in captures if isinstance(capture, dict)}
    return (timing in {"dual_reference", "before_after_or_dual_reference"} and "dual_reference" in roles) or (timing in {"before_after", "before_after_or_dual_reference"} and {"before", "after"}.issubset(roles))


def realtime_capture_time_gate(sources: list[dict[str, Any]], frozen_at: datetime | None, prefix: str, report: Report) -> None:
    for receipt in sources:
        capture = receipt.get("quote_capture")
        receipt_id = receipt.get("receipt_id", "?")
        if not isinstance(capture, dict):
            report.fail(f"{prefix}:{receipt_id}: missing quote_capture")
            continue
        quoted, captured = iso_datetime(capture.get("provider_quote_timestamp")), iso_datetime(capture.get("captured_at"))
        if quoted is None or captured is None or quoted > captured or (frozen_at is not None and captured > frozen_at):
            report.fail(f"{prefix}:{receipt_id}: quote capture timestamps must satisfy quote <= captured_at <= frozen_at")


def validate_realtime_blocked_inventory(path: Path, value: Any, report: Report) -> bool:
    """Accept a complete, value-free blocker inventory before quote captures exist."""
    if not isinstance(value, dict) or value.get("package_schema_version") != "realtime-quote-blocked-package/v1":
        return False
    prefix = str(path)
    if value.get("suite") != "realtime_quote" or value.get("status") != "not_frozen" or value.get("data_accuracy") != "not_scored":
        report.fail(f"{prefix}: invalid realtime blocked-package state")
    if value.get("oracles") != []:
        report.fail(f"{prefix}: blocked realtime package must contain no value Oracle records")
    contract_report = Report()
    quote_contract_gate({"quote_contract": value.get("quote_freeze_contract"), "atomic_assertions": []}, prefix, False, contract_report)
    report.failures.extend(contract_report.failures)
    source = value.get("source_candidate")
    root = ROOT.parents[2]
    if not isinstance(source, dict) or not isinstance(source.get("path"), str) or not is_sha256(source.get("sha256")):
        report.fail(f"{prefix}: source_candidate path and hash are required")
        return True
    candidate_path = root / source["path"]
    try:
        candidates = load_json(candidate_path)
    except (OSError, json.JSONDecodeError) as error:
        report.fail(f"{prefix}: unreadable realtime candidate source: {error}")
        return True
    if digest(candidate_path) != source["sha256"] or not isinstance(candidates, list) or source.get("case_count") != len(candidates):
        report.fail(f"{prefix}: source_candidate hash/count mismatch")
    candidate_status = {item.get("case_id"): item.get("expected_status") for item in candidates if isinstance(item, dict)}
    blocked = value.get("blocked_cases")
    if not isinstance(blocked, list) or len(blocked) != len(candidate_status):
        report.fail(f"{prefix}: blocked_cases must cover each candidate exactly once")
        return True
    seen: set[str] = set()
    forbidden = {"expected", "value", "price", "quote", "open", "high", "low", "bid", "ask"}
    for item in blocked:
        case_id = item.get("case_id") if isinstance(item, dict) else None
        if not isinstance(case_id, str) or case_id in seen or case_id not in candidate_status:
            report.fail(f"{prefix}: invalid or duplicate blocked case id")
            continue
        seen.add(case_id)
        if item.get("oracle_state") != "not_frozen" or item.get("data_accuracy") != "not_scored":
            report.fail(f"{prefix}:{case_id}: blocked case must be not_frozen/not_scored")
        if candidate_status[case_id] is None:
            if "expected_status" in item:
                report.fail(f"{prefix}:{case_id}: status-conflict candidate must omit expected_status")
        elif item.get("expected_status") != candidate_status[case_id]:
            report.fail(f"{prefix}:{case_id}: expected status does not match candidate")
        reasons = item.get("applicable_rejection_codes")
        if not isinstance(reasons, list) or not reasons or any(reason not in {"blocked_reference_snapshot", "blocked_source_license", "blocked_semantic_status"} for reason in reasons):
            report.fail(f"{prefix}:{case_id}: invalid realtime rejection code")
        if candidate_status[case_id] is None and "blocked_semantic_status" not in reasons:
            report.fail(f"{prefix}:{case_id}: unresolved candidate status requires blocked_semantic_status")
        if candidate_status[case_id] is not None and not {"blocked_reference_snapshot", "blocked_source_license"}.issubset(set(reasons)):
            report.fail(f"{prefix}:{case_id}: quote candidate requires snapshot and source-license blockers")
        if any(key in forbidden for key in item):
            report.fail(f"{prefix}:{case_id}: blocked inventory may not store market values")
    if set(candidate_status) != seen:
        report.fail(f"{prefix}: blocked_cases does not exactly match candidate IDs")
    report.incomplete += len(blocked)
    report.gap(f"{prefix}: {len(blocked)} realtime cases are an explicit no-value blocker inventory; none is scoreable")
    return True


def frozen_gate(
    record: dict[str, Any], oracle_path: Path, receipt_by_id: dict[str, dict[str, Any]], receipt_paths: dict[str, Path],
    ledger_by_oracle: dict[str, dict[str, Any]], ledger_paths: dict[str, Path], package_blocked: set[Path], manifest: dict[str, str], manifests: list[tuple[Path, dict[str, Any]]], report: Report,
) -> None:
    oracle_id = str(record.get("oracle_id", "?"))
    prefix = f"{oracle_path}:{oracle_id}"
    suite = record.get("suite")
    required = strings(record, ("authoritative_origin_id", "origin", "corroboration_scope", "timestamp_precision"))
    if required or (suite != "realtime_quote" and not fact_contract_present(record)):
        report.fail(f"{prefix}: frozen record lacks machine contract ({', '.join(required) or 'fact_contract'})")
    if suite == "realtime_quote":
        quote_contract_gate(record, prefix, True, report)
    lineage = record.get("source_lineage")
    if not isinstance(lineage, dict) or strings(lineage, ("origin_id", "delivery_channel", "extraction_method")):
        report.fail(f"{prefix}: frozen record lacks source_lineage")
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    authority_id, corroborators = evidence.get("authoritative_receipt_id"), evidence.get("corroborating_receipt_ids")
    if not isinstance(authority_id, str) or not isinstance(corroborators, list) or not corroborators:
        report.fail(f"{prefix}: frozen record needs authoritative_receipt_id and corroborating_receipt_ids")
        return
    if authority_id not in receipt_by_id or not all(isinstance(item, str) and item in receipt_by_id for item in corroborators):
        report.fail(f"{prefix}: evidence references unknown receipt")
        return
    all_ids = {authority_id, *corroborators}
    assertion_ids: set[str] = set()
    canonical_assertions: list[dict[str, Any]] = []
    exact_display_nil_assertions: list[dict[str, Any]] = []
    for assertion in record.get("atomic_assertions", []):
        assertion_id = assertion.get("assertion_id") if isinstance(assertion, dict) else None
        if not isinstance(assertion_id, str) or assertion_id in assertion_ids:
            report.fail(f"{prefix}: assertion IDs must be unique")
        if isinstance(assertion_id, str):
            assertion_ids.add(assertion_id)
        links = assertion.get("receipt_ids") if isinstance(assertion, dict) else None
        if not isinstance(links, list) or authority_id not in links or not any(item in corroborators for item in links):
            report.fail(f"{prefix}:{assertion_id or '?'}: requires authoritative and corroborating receipt links")
        elif not all(isinstance(item, str) and item in receipt_by_id for item in links):
            report.fail(f"{prefix}:{assertion_id or '?'}: references unknown receipt")
        else:
            all_ids.update(links)
        if isinstance(assertion, dict) and assertion.get("comparison") == "canonical_zero_from_display_nil":
            canonical_assertions.append(assertion)
        if isinstance(assertion, dict) and assertion.get("expected") is None:
            exact_display_nil_assertions.append(assertion)
    authority = receipt_by_id[authority_id]
    corroborating = [receipt_by_id[item] for item in corroborators]
    frozen_time_and_receipt_gate(record, [authority, *corroborating], prefix, report)
    origin_gate(record, authority, corroborating, prefix, report)
    if suite == "historical_price":
        sources = [authority, *corroborating]
        if record.get("same_authoritative_origin") is not False:
            report.fail(f"{prefix}: historical_price requires explicit same_authoritative_origin=false")
        if not historical_sources_are_independent_licensed(sources):
            report.fail(f"{prefix}: historical_price needs two independent licensed market-data sources with consistent lineage")
    elif suite == "financial_statements":
        if authority.get("source_class") not in FINANCIAL_AUTHORITY_SOURCE_CLASSES:
            report.fail(f"{prefix}: financial_statements authority must be issuer/regulator or official exchange disclosure repository")
        scope = record.get("corroboration_scope")
        valid = {"same_authoritative_origin_multi_delivery", "same_authoritative_origin_independent_parse", "independent_fact_source"}
        if scope not in valid:
            report.fail(f"{prefix}: invalid financial corroboration_scope")
        elif scope != "independent_fact_source":
            if any(item.get("authoritative_origin_id") != authority.get("authoritative_origin_id") for item in corroborating):
                report.fail(f"{prefix}: same-origin corroboration must share authoritative_origin_id")
            if scope == "same_authoritative_origin_multi_delivery" and not any(
                item.get("source_lineage", {}).get("delivery_channel") != authority.get("source_lineage", {}).get("delivery_channel") for item in corroborating
            ):
                report.fail(f"{prefix}: multi-delivery corroboration needs another delivery channel")
            if scope == "same_authoritative_origin_independent_parse" and not any(
                item.get("source_lineage", {}).get("extraction_method") == "independent_parser" for item in corroborating
            ):
                report.fail(f"{prefix}: independent-parse corroboration needs an independent_parser receipt")
    else:
        timing = record.get("quote_contract", {}).get("reference_timing") if isinstance(record.get("quote_contract"), dict) else ""
        if record.get("same_authoritative_origin") is not False or not realtime_sources_are_independent_licensed([authority, *corroborating], timing):
            report.fail(f"{prefix}: realtime_quote needs two independent licensed providers with quote capture metadata and declared timing")
        realtime_capture_time_gate([authority, *corroborating], iso_datetime(record.get("frozen_at")), prefix, report)
    ledger = ledger_by_oracle.get(oracle_id)
    ledger_path = ledger_paths.get(oracle_id)
    approved_data_reviewers: set[Any] = set()
    if not ledger or not ledger_path:
        report.fail(f"{prefix}: frozen record lacks review ledger")
    else:
        if ledger_path in package_blocked:
            report.fail(f"{prefix}: package review has an open conflict or hold decision")
        if ledger.get("oracle_file_sha256") != digest(oracle_path):
            report.fail(f"{prefix}: review ledger oracle_file_sha256 mismatch")
        approved = [item for item in ledger.get("reviews", []) if isinstance(item, dict) and item.get("decision") == "approved"]
        roles = {item.get("role") for item in approved}
        independent_review, approved_data_reviewers = reviewers_are_independent(approved)
        if not {"semantic_reviewer", "data_reviewer"}.issubset(roles) or not independent_review:
            report.fail(f"{prefix}: frozen record needs distinct semantic/data reviewers, both independent of case author")
        if any(item.get("status") == "open" for item in ledger.get("conflicts", []) if isinstance(item, dict)):
            report.fail(f"{prefix}: frozen record has open conflict")
    for assertion in canonical_assertions:
        canonical_zero_gate(assertion, prefix, receipt_by_id, ledger, approved_data_reviewers, report)
    for assertion in exact_display_nil_assertions:
        exact_display_nil_gate(assertion, prefix, receipt_by_id, report)
    if not suite_manifest_is_frozen(record["suite"], manifests):
        report.fail(f"{prefix}: frozen record requires one status=frozen suite manifest (draft/combined manifests rejected)")
    else:
        required_paths = {oracle_path, *(receipt_paths[item] for item in all_ids if item in receipt_paths)}
        if ledger_path:
            required_paths.add(ledger_path)
        if suite == "financial_statements":
            required_paths.add(ROOT / "fact-contracts.financial.v1.json")
        for path in required_paths:
            relative = path.relative_to(ROOT).as_posix()
            if manifest.get(relative) != digest(path):
                report.fail(f"{prefix}: manifest missing/mismatches {relative}")


def validate_oracle(
    record: dict[str, Any], oracle_path: Path, receipt_by_id: dict[str, dict[str, Any]], receipt_paths: dict[str, Path],
    ledger_by_oracle: dict[str, dict[str, Any]], ledger_paths: dict[str, Path], package_blocked: set[Path], manifest: dict[str, str], manifests: list[tuple[Path, dict[str, Any]]],
    fact_contracts: dict[str, dict[str, Any]], historical_contracts: dict[str, dict[str, Any]], report: Report,
) -> None:
    prefix = f"{oracle_path}:{record.get('oracle_id', '?')}"
    if record.get("schema_version") != "oracle/v1" or record.get("suite") not in SUITES or record.get("status") not in STATUSES:
        report.fail(f"{prefix}: invalid oracle schema_version or suite")
        return
    case_ids = record.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids or len(case_ids) != len(set(case_ids)):
        report.fail(f"{prefix}: case_ids must be non-empty and unique")
    assertions = record.get("atomic_assertions")
    if not isinstance(assertions, list):
        report.fail(f"{prefix}: at least one atomic assertion is required")
    if record["suite"] == "realtime_quote":
        frozen = record.get("status") == "frozen"
        quote_contract_gate(record, prefix, frozen, report)
        if not frozen:
            report.incomplete += 1
            report.gap(f"{prefix}: status={record.get('status', 'missing')}; realtime reference capture is not frozen/scoreable")
            return
    elif not assertions:
        report.fail(f"{prefix}: at least one atomic assertion is required")
    contract_ref = record.get("fact_contract_ref")
    required = strings(record, ("authoritative_origin_id", "origin", "corroboration_scope", "timestamp_precision"))
    if required:
        target = report.fail if record.get("status") == "frozen" else report.gap
        target(f"{prefix}: missing machine-contract fields ({', '.join(required)})")
    if record["suite"] == "financial_statements":
        contract = fact_contracts.get(contract_ref) if isinstance(contract_ref, str) else None
        if not has_allowed_fact_contract(record, fact_contracts, historical_contracts):
            report.fail(f"{prefix}: financial_statements requires a resolvable financial fact_contract_ref")
        else:
            source = contract["source_oracle"]
            if source["oracle_id"] != record.get("oracle_id") or set(contract.get("case_ids", [])) != set(record.get("case_ids", [])):
                report.fail(f"{prefix}: fact_contract_ref points to a different Oracle scope")
            elif record.get("fact_contract") != financial_inline_contract(contract):
                report.fail(f"{prefix}: inline fact_contract differs from registry contract")
            if not financial_registry_receipts_match(source, record.get("evidence")):
                report.fail(f"{prefix}: registry_evidence_receipt_mismatch")
    elif record["suite"] == "historical_price" and not has_allowed_fact_contract(record, fact_contracts, historical_contracts):
        report.fail(f"{prefix}: historical_price requires a complete inline fact_contract or resolvable independent registry")
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    authority_id, corroborators = evidence.get("authoritative_receipt_id"), evidence.get("corroborating_receipt_ids")
    if not isinstance(authority_id, str) or not isinstance(corroborators, list) or not corroborators:
        report.fail(f"{prefix}: authoritative and corroborating evidence links are required")
    elif authority_id not in receipt_by_id or not all(isinstance(item, str) and item in receipt_by_id for item in corroborators):
        report.fail(f"{prefix}: evidence references an unknown receipt")
    for assertion in assertions if isinstance(assertions, list) else []:
        if not isinstance(assertion, dict) or strings(assertion, ("assertion_id", "field", "currency", "unit")):
            report.fail(f"{prefix}: assertion needs assertion_id, field, currency, unit")
        elif assertion.get("comparison") == "numeric_tolerance" and "tolerance" not in assertion:
            report.fail(f"{prefix}:{assertion.get('assertion_id')}: numeric_tolerance requires tolerance")
        elif assertion.get("expected") is None and assertion.get("comparison") == "numeric_tolerance":
            report.fail(f"{prefix}:{assertion.get('assertion_id')}: expected=null cannot use numeric_tolerance")
        else:
            links = assertion.get("receipt_ids")
            if not isinstance(links, list) or authority_id not in links or not any(item in corroborators for item in links):
                report.fail(f"{prefix}:{assertion.get('assertion_id')}: requires authoritative and corroborating receipt links")
            elif not all(isinstance(item, str) and item in receipt_by_id for item in links):
                report.fail(f"{prefix}:{assertion.get('assertion_id')}: references unknown receipt")
    if record.get("status") == "frozen":
        report.frozen += 1
        frozen_gate(record, oracle_path, receipt_by_id, receipt_paths, ledger_by_oracle, ledger_paths, package_blocked, manifest, manifests, report)
    else:
        report.incomplete += 1
        gaps = [field for field in ("authoritative_origin_id", "corroboration_scope", "timestamp_precision") if field not in record]
        if record["suite"] != "realtime_quote" and not fact_contract_present(record):
            gaps.append("fact_contract_ref|fact_contract")
        report.gap(f"{prefix}: status={record.get('status', 'missing')}; not scoreable/frozen ({', '.join(gaps) or 'review + manifest gates pending'})")


def validate_package() -> Report:
    report = Report()
    schema_check(report)
    financial_contracts = collect_financial_contracts(report)
    historical_contracts = collect_historical_contracts(report)
    for suite in sorted(SUITES):
        suite_dir = OUTPUTS / suite
        if not suite_dir.is_dir():
            report.fail(f"missing output directory: {suite}")
            continue
        receipt_by_id, receipt_paths = collect_receipts(suite_dir, report)
        ledger_by_oracle, ledger_paths, package_blocked = collect_ledgers(suite_dir, report)
        manifest, manifests = collect_manifest(suite_dir, report)
        for path in json_paths(suite_dir, {"oracles.json", "oracle.json"}):
            try:
                value = load_json(path)
            except (OSError, json.JSONDecodeError) as error:
                report.fail(f"{path}: unreadable oracles: {error}")
                continue
            if suite == "realtime_quote" and validate_realtime_blocked_inventory(path, value, report):
                continue
            items = records(value, "oracles", path, report)
            for item in items:
                validate_oracle(item, path, receipt_by_id, receipt_paths, ledger_by_oracle, ledger_paths, package_blocked, manifest, manifests, financial_contracts, historical_contracts, report)
    return report


def self_test() -> int:
    """Narrow frozen-gate fixtures for registry, provenance, reviewers, manifest, PIT, and hashes."""
    report = Report()
    record = {"schema_version": "oracle/v1", "oracle_id": "negative", "suite": "historical_price", "case_ids": ["H-1"], "status": "frozen", "atomic_assertions": [{"assertion_id": "a", "field": "close", "currency": "USD", "unit": "USD"}]}
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "oracles.json"
        path.write_text(json.dumps([record]), encoding="utf-8")
        validate_oracle(record, path, {}, {}, {}, {}, set(), {}, [], {}, {}, report)
    assert report.failures and any("machine contract" in item or "authoritative_receipt_id" in item for item in report.failures)
    dash_report = Report()
    canonical_zero_gate(
        {"assertion_id": "dash", "comparison": "canonical_zero_from_display_nil", "expected": 0, "raw_display": "N/A", "receipt_ids": []},
        "self-test", {}, None, set(), dash_report,
    )
    assert any("only standard en-dash" in item for item in dash_report.failures)
    inline_contract = {"entity": "x", "symbol": "X", "exchange": "X", "form": "annual", "accession": "a", "period_end": "2025-12-31", "duration_or_instant": "duration", "consolidation_scope": "consolidated", "accounting_standard": "GAAP", "reported_currency": "USD", "unit_scale": "1", "complete_statement_schema_id": "s/v1"}
    assert has_allowed_fact_contract({"suite": "historical_price", "fact_contract": inline_contract}, {}, {})
    assert not has_allowed_fact_contract({"suite": "financial_statements", "fact_contract": inline_contract}, {}, {})
    assert has_allowed_fact_contract({"suite": "financial_statements", "fact_contract_ref": "f"}, {"f": {}}, {})
    assert reviewers_are_independent([{"role": "case_author", "reviewer_id": "author"}, {"role": "semantic_reviewer", "reviewer_id": "semantic"}, {"role": "data_reviewer", "reviewer_id": "data"}])[0]
    assert not reviewers_are_independent([{"role": "semantic_reviewer", "reviewer_id": "same"}, {"role": "data_reviewer", "reviewer_id": "same"}])[0]
    manifest = [(OUTPUTS / "historical_price" / "manifest.json", {"suite": "historical_price", "status": "frozen"})]
    assert suite_manifest_is_frozen("historical_price", manifest)
    assert not suite_manifest_is_frozen("historical_price", [(manifest[0][0], {"suite": "historical_price", "status": "draft"})])
    assert package_review_blocks({"package_review": {"counts": {"package_release_decision": "hold"}}})
    assert not package_review_blocks({"package_review": {"counts": {"package_release_decision": "approved"}, "conflicts": []}})
    authority = {"receipt_id": "a", "source_id": "source-a", "source_class": "licensed_market_data", "authoritative_origin_id": "origin-a", "independence_group": "a", "same_authoritative_origin": False, "source_lineage": {"origin_id": "origin-a", "delivery_channel": "official-a", "extraction_method": "manual"}, "published_at": "2025-01-01T00:00:00Z", "retrieved_at": "2025-01-02T00:00:00Z", "content_sha256": "a" * 64, "license_id": "license-a"}
    corroborator = {"receipt_id": "b", "source_id": "source-b", "source_class": "licensed_market_data", "authoritative_origin_id": "origin-b", "independence_group": "b", "same_authoritative_origin": False, "source_lineage": {"origin_id": "origin-b", "delivery_channel": "official-b", "extraction_method": "manual"}, "published_at": "2025-01-01T00:00:00Z", "retrieved_at": "2025-01-02T00:00:00Z", "content_sha256": "b" * 64, "license_id": "license-b"}
    origin_report = Report()
    origin_gate({"origin": "origin-a", "authoritative_origin_id": "origin-a", "same_authoritative_origin": False, "corroboration_scope": "independent_fact_source", "source_lineage": {"origin_id": "origin-a", "delivery_channel": "record", "extraction_method": "manual"}}, authority, [corroborator], "self-test", origin_report)
    frozen_time_and_receipt_gate({"frozen_at": "2025-01-03T00:00:00Z", "point_in_time": {"source_published_at": "2025-01-01T00:00:00Z", "information_available_at": "2025-01-02T00:00:00Z", "retrieved_at": "2025-01-02T00:00:00Z"}}, [authority, corroborator], "self-test", origin_report)
    assert not origin_report.failures
    assert historical_sources_are_independent_licensed([authority, corroborator])
    assert not historical_sources_are_independent_licensed([{**authority, "license_id": ""}, corroborator])
    quote = {
        "schema_version": "oracle/v1", "oracle_id": "rtq-negative", "suite": "realtime_quote", "case_ids": ["RTQ-1"], "status": "draft", "atomic_assertions": [],
        "quote_contract": {"snapshot_mode": "live_bracketed", "evaluation_use": "formal_data_accuracy", "status_expectation": "success", "reference_timing": "dual_reference", "capture_metadata": sorted(QUOTE_CAPTURE_FIELDS), "tolerances": {name: {"state": "unbound_must_bind_before_capture"} for name in ("staleness", "cross_source_skew", "tick")}, "scoring_exclusions": ["provider_tool_parameters"], "block_reasons": ["blocked_reference_snapshot", "blocked_source_license"]},
    }
    quote_report = Report()
    validate_oracle(quote, Path("realtime-oracles.json"), {}, {}, {}, {}, set(), {}, [], {}, {}, quote_report)
    assert not quote_report.failures and quote_report.incomplete == 1
    quote["quote_contract"]["snapshot_mode"] = "replay_fixture"
    quote["quote_contract"]["evaluation_use"] = "formal_data_accuracy"
    replay_report = Report()
    quote_contract_gate(quote, "self-test", False, replay_report)
    assert any("never enter formal" in item for item in replay_report.failures)
    display_receipts = {
        "a": {"source_lineage": {"delivery_channel": "sec"}, "display_nil_evidence": [{"assertion_id": "nil", "raw_display": "—", "evidence_locator": "sec#cell", "confirmed_nil": False}]},
        "b": {"source_lineage": {"delivery_channel": "issuer_ir"}, "display_nil_evidence": [{"assertion_id": "nil", "raw_display": "—", "evidence_locator": "ir#cell", "confirmed_nil": False}]},
    }
    display_report = Report()
    exact_display_nil_gate({"assertion_id": "nil", "expected": None, "comparison": "exact", "raw_display": "—", "receipt_ids": ["a", "b"]}, "self-test", display_receipts, display_report)
    assert not display_report.failures
    exact_display_nil_gate({"assertion_id": "nil", "expected": 0, "comparison": "exact", "raw_display": "—", "receipt_ids": ["a", "b"]}, "self-test", display_receipts, display_report)
    assert any("expected=null" in item for item in display_report.failures)
    bad_origin = Report()
    origin_gate({"origin": "origin-a", "authoritative_origin_id": "origin-a", "same_authoritative_origin": True, "corroboration_scope": "same_authoritative_origin_multi_delivery", "source_lineage": {"origin_id": "origin-a", "delivery_channel": "record", "extraction_method": "manual"}}, authority, [corroborator], "self-test", bad_origin)
    assert bad_origin.failures and "same-origin" in bad_origin.failures[-1]
    registry_contract = {
        "fact_contract_id": "financial-test", "case_ids": ["FS-1"], "market": "US", "statement_type": "income_statement",
        "reporting_basis": "as_reported", "entity": {"name": "Example", "symbol": "EX"},
        "period": {"label": "FY2025", "period_end": "2025-12-31"}, "consolidation_scope": "consolidated",
        "accounting_standard": "GAAP", "currency": "USD", "reported_currency": "USD", "unit_scale": "1",
        "complete_statement_schema_id": "financial_statements/complete_income_statement/v1", "exchange": "NYSE",
        "form": "annual_report", "accession": "example", "duration_or_instant": "duration",
        "point_in_time": {"information_available_at": "2026-01-01T00:00:00Z", "source_published_at": "2026-01-01T00:00:00Z"},
        "presentation_scope": {"primary_period": {"assertion_period": "FY2025", "label": "FY2025", "period_end": "2025-12-31"}, "comparative_periods": [], "required_as_filed": True},
        "source_oracle": {"oracle_id": "financial-test", "path": "outputs/financial_statements/test/oracles.json", "primary_receipt_id": "a", "corroboration_receipt_ids": ["b", "c"]},
    }
    assert financial_registry_contract_valid(registry_contract)
    assert normalized_registry_receipts(registry_contract["source_oracle"]) == ("a", ["b", "c"])
    assert normalized_registry_receipts({"primary_receipt_id": "a", "corroboration_receipt_ids": ["b", "b"]}) is None
    assert normalized_registry_receipts({"primary_receipt_id": "a", "corroboration_receipt_id": "a"}) is None
    assert financial_registry_receipts_match(registry_contract["source_oracle"], {"authoritative_receipt_id": "a", "corroborating_receipt_ids": ["c", "b"]})
    assert not financial_registry_receipts_match(registry_contract["source_oracle"], {"authoritative_receipt_id": "a", "corroborating_receipt_ids": ["b"]})
    missing_projection = dict(registry_contract)
    missing_projection.pop("presentation_scope")
    assert not financial_registry_contract_valid(missing_projection)
    with tempfile.TemporaryDirectory() as temporary:
        registry_path = Path(temporary) / "registry.json"
        registry_path.write_text(json.dumps({"contracts": [registry_contract]}), encoding="utf-8")
        registry_report = Report()
        assert list(collect_financial_contracts(registry_report, registry_path)) == ["financial-test"] and not registry_report.failures
        registry_path.write_text(json.dumps({"contracts": [missing_projection]}), encoding="utf-8")
        malformed_registry_report = Report()
        assert not collect_financial_contracts(malformed_registry_report, registry_path)
        assert any("registry_projection_contract_invalid" in failure for failure in malformed_registry_report.failures)
    assert "exchange" in FINANCIAL_AUTHORITY_SOURCE_CLASSES and "other" not in FINANCIAL_AUTHORITY_SOURCE_CLASSES
    print("OK: self-test covered registry receipt normalization/projection guards, reviewer separation, origin/license, frozen manifest, ISO/PIT/hash, authority class, display-nil, and realtime live/replay gates")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    report = validate_package()
    for message in report.failures:
        print(f"FAIL: {message}", file=sys.stderr)
    for message in report.gaps:
        print(f"INCOMPLETE: {message}")
    if report.failures:
        return 1
    if report.frozen:
        print(f"PASS: {report.frozen} frozen Oracle record(s) passed structural gates")
    else:
        print(f"INCOMPLETE: no frozen Oracle records; {report.incomplete} record(s) remain non-scoreable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

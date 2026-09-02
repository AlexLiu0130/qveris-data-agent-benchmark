#!/usr/bin/env python3
"""Dry-run one frozen pilot case by default; --execute permits one approved call."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


BASE_URL = "https://qveris.ai/api/v1"
DEFAULT_LEDGER = Path("artifacts/tool-audit/pilot-ledger.jsonl")
DEFAULT_PRIVATE_RESULT_DIR = Path("artifacts/private")
SYSTEM_CA_FILE = Path("/etc/ssl/cert.pem")
MAX_RESPONSE_BYTES = 1_000_000
SENSITIVE_NAMES = ("authorization", "api_key", "apikey", "token", "secret", "password", "cookie", "header")
ERROR_FIELDS = ("error_code", "status", "code", "error_message", "message")
LEDGER_STATES = frozenset({"planned", "dispatched", "settled", "uncertain"})
CONNECTOR_PROTOCOL_VERSION = "qveris.execute.parameters.v1"
QUALITY_SPEC_KEYS = frozenset({"data_path", "required_keys", "finite_numeric_fields", "finite_decimal_fields", "nonempty", "identity", "date", "period", "ohlc", "timestamp_fields", "financial_fields"})
FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class PilotError(RuntimeError):
    """A fail-closed policy or audit-boundary error."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def manifest_hash(manifest: Mapping[str, Any]) -> str:
    """Hash immutable manifest content, excluding its plan-hash attestation to avoid a cycle."""
    if type(manifest) is not dict:
        raise PilotError("manifest must be an object")
    frozen = json.loads(json.dumps(manifest))
    policy = frozen.get("execution_policy")
    if type(policy) is dict:
        policy.pop("approved_plan_hash", None)
    return canonical_hash(frozen)


def build_ssl_context(environ: Mapping[str, str] | None = None) -> ssl.SSLContext:
    environ = os.environ if environ is None else environ
    cafile = environ.get("SSL_CERT_FILE") or (str(SYSTEM_CA_FILE) if SYSTEM_CA_FILE.is_file() else None)
    try:
        context = ssl.create_default_context(cafile=cafile)
    except (OSError, ssl.SSLError) as error:
        raise PilotError("verifying_ca_unavailable") from error
    if not context.cert_store_stats().get("x509_ca", 0):
        raise PilotError("verifying_ca_unavailable")
    return context


def load_key(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PilotError("cannot read credential file") from error
    for line in lines:
        if line.startswith("QVERIS_API_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    raise PilotError("QVERIS_API_KEY is absent from credential file")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PilotError("invalid JSON input: %s" % path) from error


def _candidates(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    domains = manifest.get("domains")
    if type(domains) is not dict:
        raise PilotError("manifest has no domains")
    found: dict[str, Mapping[str, Any]] = {}
    for detail in domains.values():
        rows = detail.get("primary_candidates") if type(detail) is dict else None
        if type(rows) is not list:
            continue
        for row in rows:
            alias = row.get("alias") if type(row) is dict else None
            if type(alias) is not str or not alias or alias in found:
                raise PilotError("manifest has invalid or duplicate aliases")
            found[alias] = row
    if not found:
        raise PilotError("manifest has no primary candidates")
    return found


def _has_blocked(value: Any) -> bool:
    if type(value) is dict:
        return value.get("status") == "blocked" or value.get("blocked") is True or any(_has_blocked(item) for item in value.values())
    return type(value) is list and any(_has_blocked(item) for item in value)


def _fixed_cost(value: Any) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise PilotError("unknown or unbounded expected cost is not approved for live execution")
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PilotError("unknown or unbounded expected cost is not approved for live execution") from None
    if not cost.is_finite() or cost < 0:
        raise PilotError("unknown or unbounded expected cost is not approved for live execution")
    try:
        fixed = float(cost)
    except OverflowError:
        raise PilotError("unknown or unbounded expected cost is not approved for live execution") from None
    if not math.isfinite(fixed):
        raise PilotError("unknown or unbounded expected cost is not approved for live execution")
    return fixed


def _frozen_idempotency_key(value: Any) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise PilotError("plan idempotency_key is invalid")
    return value


def _plan_case(plan: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    if set(plan) != {"approval_id", "manifest_hash", "connector_protocol_version", "cases"} or type(plan.get("approval_id")) is not str or not plan["approval_id"] or plan.get("connector_protocol_version") != CONNECTOR_PROTOCOL_VERSION:
        raise PilotError("plan schema is invalid")
    rows = plan.get("cases")
    if type(rows) is not list:
        raise PilotError("plan cases are invalid")
    case_ids: set[str] = set()
    idempotency_keys: set[str] = set()
    for candidate in rows:
        if type(candidate) is not dict or set(candidate) != {"case_id", "alias", "arguments", "expected_cost", "approval_id", "idempotency_key"}:
            raise PilotError("plan case must contain exact execution fields")
        if any(type(candidate[name]) is not str or not candidate[name] for name in ("case_id", "alias", "approval_id")) or type(candidate["arguments"]) is not dict:
            raise PilotError("plan case types are invalid")
        frozen_key = _frozen_idempotency_key(candidate["idempotency_key"])
        if candidate["case_id"] in case_ids or frozen_key in idempotency_keys:
            raise PilotError("plan case ids and idempotency keys must be unique")
        case_ids.add(candidate["case_id"])
        idempotency_keys.add(frozen_key)
    matches = [row for row in rows if type(row) is dict and row.get("case_id") == case_id]
    if len(matches) != 1:
        raise PilotError("case is absent or duplicated in approved plan")
    row = matches[0]
    _fixed_cost(row["expected_cost"])
    if row["approval_id"] != plan["approval_id"] or _has_blocked(row["arguments"]) or _has_full_content_url(row["arguments"]):
        raise PilotError("plan case is not executable")
    return row


def resolve_approved_case(manifest_path: Path, plan_path: Path, case_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str, str]:
    manifest, plan = _load_json(manifest_path), _load_json(plan_path)
    if type(manifest) is not dict or type(plan) is not dict:
        raise PilotError("manifest and plan must be objects")
    policy = manifest.get("execution_policy")
    if type(policy) is not dict or policy.get("live_status") != "approved_for_pilot":
        raise PilotError("manifest is not approved for pilot")
    if "connector_protocol_version" in manifest or policy.get("connector_protocol_version") != CONNECTOR_PROTOCOL_VERSION or plan.get("connector_protocol_version") != CONNECTOR_PROTOCOL_VERSION:
        raise PilotError("manifest and plan connector protocols must match the approved version")
    approval_id = policy.get("approval_id")
    total_budget = policy.get("total_budget_credits")
    if type(approval_id) is not str or not approval_id:
        raise PilotError("manifest approval is invalid")
    _fixed_cost(total_budget)
    actual_manifest_hash = manifest_hash(manifest)
    plan_hash = canonical_hash(plan)
    if plan.get("manifest_hash") != actual_manifest_hash or policy.get("approved_plan_hash") != plan_hash:
        raise PilotError("approved plan and manifest hashes do not bind")
    if plan.get("approval_id") != approval_id:
        raise PilotError("plan approval does not match manifest approval")
    planned = _plan_case(plan, case_id)
    frozen_order = manifest.get("frozen_case_order")
    if frozen_order is not None and (type(frozen_order) is not list or frozen_order != [row["case_id"] for row in plan["cases"]]):
        raise PilotError("manifest frozen case order does not match approved plan")
    candidate = _candidates(manifest).get(planned["alias"])
    if candidate is None or candidate.get("live_status") != "approved_for_pilot" or _has_blocked(candidate):
        raise PilotError("candidate is not approved for pilot")
    if type(candidate.get("tool_id")) is not str or not candidate["tool_id"]:
        raise PilotError("candidate tool_id is invalid")
    if type(candidate.get("call_parameters")) is not dict or canonical_hash(candidate["call_parameters"]) != canonical_hash(planned["arguments"]):
        raise PilotError("plan arguments do not exactly match manifest")
    if _fixed_cost(candidate.get("catalog_expected_credits")) != _fixed_cost(planned["expected_cost"]):
        raise PilotError("plan expected cost does not exactly match manifest")
    _quality_spec(candidate)
    return manifest, plan, planned, actual_manifest_hash, plan_hash


def _verify_provenance_artifacts(manifest: Mapping[str, Any]) -> None:
    artifacts = manifest.get("provenance_artifacts")
    if artifacts is None:
        return
    if type(artifacts) is not list:
        raise PilotError("provenance artifacts are invalid")
    repo_root = Path(__file__).resolve().parents[1]
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != {"path", "sha256"} or type(artifact["path"]) is not str or not artifact["path"] or type(artifact["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
            raise PilotError("provenance artifact is invalid")
        requested = Path(artifact["path"])
        candidate = requested if requested.is_absolute() else repo_root / requested
        lexical = Path(os.path.abspath(candidate))
        in_tmp = lexical == Path("/tmp") or Path("/tmp") in lexical.parents
        try:
            relative = lexical.relative_to(repo_root)
        except ValueError:
            relative = None
        if not in_tmp and (relative is None or subprocess.run(["git", "-C", str(repo_root), "check-ignore", "-q", "--", str(relative)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode != 0):
            raise PilotError("provenance artifact path is not an ignored repository file or /tmp file")
        try:
            info = os.lstat(lexical)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise PilotError("provenance artifact must be an owner regular file")
            digest = hashlib.sha256(lexical.read_bytes()).hexdigest()
        except OSError as error:
            raise PilotError("provenance artifact is unavailable") from error
        if digest != artifact["sha256"]:
            raise PilotError("provenance artifact digest does not match approved manifest")


def _plan_storage_paths(ledger_base: Path, private_base: Path, plan_hash: str) -> tuple[Path, Path]:
    return ledger_base.parent / plan_hash / ledger_base.name, private_base / plan_hash


def _has_full_content_url(value: Any) -> bool:
    if type(value) is dict:
        return "full_content_file_url" in value or any(_has_full_content_url(item) for item in value.values())
    return type(value) is list and any(_has_full_content_url(item) for item in value)


def _safe_case_id(case_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", case_id) or ".." in case_id:
        raise PilotError("case_id is unsafe for a private result filename")
    return case_id


def _verify_approval_digest(path: Path | None, plan_hash: str) -> None:
    """Require external attestation so repository edits cannot self-authorize execution."""
    if path is None:
        raise PilotError("--execute requires --approval-digest-file")
    try:
        info = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PilotError("approval digest file is unavailable") from error
    repo_root = Path(__file__).resolve().parents[1]
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 128:
        raise PilotError("approval digest file must be an owner-only regular file")
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise PilotError("approval digest file must be external to the repository")
    try:
        value = resolved.read_text(encoding="utf-8")
    except OSError as error:
        raise PilotError("approval digest file cannot be read") from error
    if value.strip() != plan_hash:
        raise PilotError("approval digest does not match the frozen plan")


def _write_private_result(directory: Path, case_id: str, response_sha256: str, payload: Any) -> tuple[str | None, str]:
    """Persist only a parsed HTTP JSON response, never request metadata or headers."""
    if type(response_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", response_sha256):
        return None, "not_json"
    basename = "%s-%s.json" % (_safe_case_id(case_id), response_sha256)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        target = directory / basename
        descriptor, temporary = tempfile.mkstemp(prefix=".result.", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
    except OSError:
        return basename, "write_failed"
    return basename, "saved"


class LockedLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.records: list[Mapping[str, Any]] = []

    def __enter__(self) -> "LockedLedger":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        os.lseek(self.fd, 0, os.SEEK_SET)
        self.records = _parse_ledger(os.read(self.fd, os.fstat(self.fd).st_size))
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def append(self, record: Mapping[str, Any]) -> None:
        if self.fd is None:
            raise RuntimeError("ledger lock is not held")
        append_ledger(self.fd, record)
        self.records.append(record)


def _parse_ledger(raw: bytes) -> list[Mapping[str, Any]]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise PilotError("pilot ledger is truncated")
    records: list[Mapping[str, Any]] = []
    planned: set[tuple[str, str]] = set()
    finalized: set[tuple[str, str]] = set()
    for line in raw.splitlines():
        if not line:
            raise PilotError("pilot ledger contains a blank record")
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PilotError("pilot ledger is malformed") from error
        if type(record) is not dict or record.get("record_type") not in LEDGER_STATES:
            raise PilotError("pilot ledger has an unknown state")
        required = ("case_id", "alias", "tool_id", "arguments_hash", "idempotency_key")
        if any(type(record.get(name)) is not str or not record[name] for name in required):
            raise PilotError("pilot ledger record is incomplete")
        if type(record.get("expected_credits")) not in (int, float) or isinstance(record["expected_credits"], bool):
            raise PilotError("pilot ledger expected cost is invalid")
        binding = ("manifest_hash", "plan_hash", "approval_id")
        is_v2 = all(type(record.get(name)) is str and record[name] for name in binding)
        if not is_v2:
            if any(name in record for name in binding) or record["record_type"] != "planned" or type(record.get("variable_cost")) is not bool:
                raise PilotError("pilot ledger record is incomplete")
            records.append(record)
            continue
        protocol = record.get("connector_protocol_version")
        if protocol is not None and protocol != CONNECTOR_PROTOCOL_VERSION:
            raise PilotError("pilot ledger connector protocol is unknown")
        identity = (record["case_id"], record["plan_hash"])
        if record["record_type"] == "planned":
            if identity in planned:
                raise PilotError("pilot ledger contains duplicate planned case")
            planned.add(identity)
        elif identity not in planned or (record["record_type"] in {"settled", "uncertain"} and identity in finalized):
            raise PilotError("pilot ledger state transition is invalid")
        elif record["record_type"] in {"settled", "uncertain"}:
            finalized.add(identity)
        records.append(record)
    return records


def append_ledger(fd: int, record: Mapping[str, Any]) -> None:
    encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    os.lseek(fd, 0, os.SEEK_END)
    written = os.write(fd, encoded)
    if written != len(encoded):
        raise OSError("short pilot ledger write")
    os.fsync(fd)


def _ensure_not_replayed(records: list[Mapping[str, Any]], case_id: str, idempotency_key: str) -> None:
    for record in records:
        if record["case_id"] == case_id or record["idempotency_key"] == idempotency_key:
            raise PilotError("case or idempotency key was already planned; do not resend")


def _prior_actual_credits(records: list[Mapping[str, Any]], plan: Mapping[str, Any], plan_hash: str, case_id: str) -> float:
    case_ids = [row["case_id"] for row in plan["cases"]]
    total = 0.0
    for prior_case in case_ids[:case_ids.index(case_id)]:
        terminal = [record for record in records if record.get("plan_hash") == plan_hash and record["case_id"] == prior_case and record["record_type"] in {"settled", "uncertain"}]
        if len(terminal) != 1:
            raise PilotError("prior plan case lacks a unique terminal receipt")
        receipt = terminal[0]
        actual = receipt.get("actual_credits")
        if receipt["record_type"] != "settled" or receipt.get("outcome") != "success" or receipt.get("receipt_status") != "reported" or type(actual) not in (int, float) or isinstance(actual, bool) or not math.isfinite(actual) or actual < 0:
            raise PilotError("prior plan case did not complete with a valid successful receipt")
        total += float(actual)
    return total


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return normalized != "full_content_file_url" and not any(term in normalized for term in SENSITIVE_NAMES)


def _result_shape(payload: Any) -> str:
    if payload is None:
        return "null"
    if type(payload) is dict:
        return "empty_object" if not payload else "object"
    if type(payload) is list:
        return "empty_array" if not payload else "array"
    return "scalar"


def _result_top_level_keys(payload: Any) -> list[str]:
    return sorted(str(key) for key in payload if _safe_key(key))[:30] if type(payload) is dict else []


def _sanitize_text(value: str) -> str:
    value = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(r"(?i)\b(api[_-]?key|apikey|token|secret|password|authorization|cookie)\b\s*[:=]\s*\S+", r"\1=[redacted]", value)
    value = re.sub(r"(?i)([?&](?:api[_-]?key|apikey|token|secret|password)=)[^&\s]+", r"\1[redacted]", value)
    return value[:300]


def _sanitized_error(payload: Any) -> dict[str, str | int | float | bool | None] | None:
    if type(payload) is not dict:
        return None
    error: dict[str, str | int | float | bool | None] = {}
    for name in ERROR_FIELDS:
        value = payload.get(name)
        if type(value) is str:
            error[name] = _sanitize_text(value)
        elif type(value) in (int, float, bool) or value is None and name in payload:
            error[name] = value
    return error or None


def _business_success_raw(payload: Any) -> bool | str:
    return payload["success"] if type(payload) is dict and type(payload.get("success")) is bool else "missing"


def _field(value: Any, name: Any) -> str:
    if type(value) is not str or not FIELD_NAME.fullmatch(value):
        raise PilotError("quality validator field names are invalid")
    return value


def _field_list(value: Any, label: str) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise PilotError("quality validator %s is invalid" % label)
    return tuple(_field(item, label) for item in value)


def _match_rule(value: Any, label: str) -> Mapping[str, str]:
    if type(value) is not dict or set(value) != {"field", "argument", "mode"}:
        raise PilotError("quality validator %s rule is invalid" % label)
    field, argument, mode = _field(value.get("field"), label), _field(value.get("argument"), label), value.get("mode")
    if mode not in {"exact", "request_bound", "iso_date_exact"}:
        raise PilotError("quality validator %s mode is invalid" % label)
    return {"field": field, "argument": argument, "mode": mode}


def _quality_spec(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    spec = candidate.get("quality_validator_spec")
    if spec is None:
        return None
    if type(spec) is not dict or not spec or set(spec) - QUALITY_SPEC_KEYS:
        raise PilotError("quality validator spec is invalid")
    path = spec.get("data_path")
    if type(path) is not str or not path or any(not FIELD_NAME.fullmatch(part) for part in path.split(".")):
        raise PilotError("quality validator data_path is invalid")
    for label in ("required_keys", "finite_numeric_fields", "finite_decimal_fields", "timestamp_fields", "financial_fields"):
        if label in spec:
            _field_list(spec[label], label)
    if "nonempty" in spec and type(spec["nonempty"]) is not bool:
        raise PilotError("quality validator nonempty is invalid")
    for label in ("identity", "date", "period"):
        if label in spec:
            _match_rule(spec[label], label)
    if "ohlc" in spec:
        ohlc = spec["ohlc"]
        if ohlc is True:
            pass
        elif type(ohlc) is dict and set(ohlc) == {"open", "high", "low", "close", "volume"}:
            for value in ohlc.values():
                _field(value, "ohlc")
        else:
            raise PilotError("quality validator ohlc is invalid")
    return spec


def _data_at(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if type(current) is not dict or part not in current:
            return None
        current = current[part]
    return current


def _quality_rows(data: Any) -> list[Mapping[str, Any]]:
    if type(data) is dict:
        return [data]
    if type(data) is list and data and all(type(row) is dict for row in data):
        return data
    return []


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(float(value))


def _finite_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or type(value) not in (str, int, float, Decimal):
        return None
    if type(value) is str and not value.strip():
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _ohlc_valid(row: Mapping[str, Any], fields: Mapping[str, str]) -> bool:
    values = {name: _finite_decimal(row.get(field)) for name, field in fields.items()}
    if any(value is None for value in values.values()):
        return False
    return values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"] and values["volume"] >= 0


def _rule_passes(rows: list[Mapping[str, Any]], rule: Mapping[str, str], arguments: Mapping[str, Any]) -> bool:
    expected = arguments.get(rule["argument"])
    if expected is None:
        return False
    for row in rows:
        if rule["field"] not in row:
            if rule["mode"] == "request_bound":
                continue
            return False
        observed = row[rule["field"]]
        if rule["mode"] == "iso_date_exact":
            if not _iso_date_exact(observed, expected):
                return False
        elif observed != expected:
            return False
    return True


def _iso_date_exact(observed: Any, expected: Any) -> bool:
    """Accept a date-only value or timestamp whose calendar date is exact."""
    if type(observed) is not str or type(expected) is not str:
        return False
    try:
        dt.date.fromisoformat(expected)
    except ValueError:
        return False
    return observed == expected or bool(re.fullmatch(re.escape(expected) + r"T.+", observed))


def validate_quality(payload: Any, candidate: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Run the small, declared structural gate without copying provider values to the ledger."""
    spec = _quality_spec(candidate)
    if spec is None:
        return {"status": "not_configured", "checks": [], "failure_codes": []}
    checks: list[str] = []
    failures: list[str] = []
    data = _data_at(payload, spec["data_path"])
    rows = _quality_rows(data)
    if spec.get("nonempty", False) and type(data) in (dict, list) and not data:
        failures.append("data_empty")
    elif not rows:
        failures.append("data_path_missing_or_not_object_rows")
    else:
        checks.append("data_path")
        if spec.get("nonempty", False):
            checks.append("nonempty")
        for label in ("required_keys", "finite_numeric_fields", "finite_decimal_fields", "financial_fields"):
            fields = spec.get(label, [])
            if fields and not all(all(field in row and (label != "financial_fields" or row[field] not in (None, "")) for field in fields) for row in rows):
                failures.append(label + "_missing")
            elif fields:
                checks.append(label)
        numeric = spec.get("finite_numeric_fields", [])
        if numeric and not all(all(_finite(row[field]) for field in numeric if field in row) for row in rows):
            failures.append("finite_numeric_fields_invalid")
        decimal_fields = spec.get("finite_decimal_fields", [])
        if decimal_fields and not all(all(_finite_decimal(row[field]) is not None for field in decimal_fields if field in row) for row in rows):
            failures.append("finite_decimal_fields_invalid")
        for label in ("identity", "date", "period"):
            rule = spec.get(label)
            if rule and not _rule_passes(rows, _match_rule(rule, label), arguments):
                failures.append(label + "_mismatch")
            elif rule:
                checks.append(label)
        timestamps = spec.get("timestamp_fields", [])
        if timestamps and not all(any(row.get(field) not in (None, "") for field in timestamps) for row in rows):
            failures.append("timestamp_missing")
        elif timestamps:
            checks.append("timestamp")
        if spec.get("ohlc"):
            fields = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"} if spec["ohlc"] is True else spec["ohlc"]
            valid = all(_ohlc_valid(row, fields) for row in rows)
            if valid:
                checks.append("ohlc")
            else:
                failures.append("ohlc_invariant_failed")
    return {"status": "passed" if not failures else "failed", "checks": sorted(checks), "failure_codes": sorted(set(failures))}


def _number(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and not isinstance(value, bool) else None


def _receipt(payload: Mapping[str, Any] | None) -> tuple[float | None, float | None]:
    if type(payload) is not dict:
        return None, None
    actual = next((_number(payload.get(name)) for name in ("actual_cost", "actual_credits", "credits_used", "cost") if _number(payload.get(name)) is not None), None)
    return actual, _number(payload.get("remaining_credits"))


def _execution_id(payload: Mapping[str, Any] | None) -> str | int | None:
    if type(payload) is not dict:
        return None
    for source in (payload, payload.get("data")):
        if type(source) is dict:
            for name in ("execution_id", "executionId"):
                value = source.get(name)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    return value
    return None


def post(opener: Any, tool_id: str, arguments: Mapping[str, Any], key: str, idempotency_key: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(BASE_URL + "/tools/execute?" + urllib.parse.urlencode({"tool_id": tool_id}), data=json.dumps({"parameters": arguments}, separators=(",", ":")).encode("utf-8"), headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": "Bearer " + key, "Idempotency-Key": idempotency_key}, method="POST")
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw, status = response.read(MAX_RESPONSE_BYTES + 1), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(MAX_RESPONSE_BYTES + 1), error.code
    except (TimeoutError, socket.timeout):
        return {"outcome": "uncertain", "business_status": "not_received", "http_status": None, "latency_ms": round((time.monotonic() - started) * 1000), "payload": None, "response_is_json": False, "response_sha256": None}
    except urllib.error.URLError as error:
        return {"outcome": "uncertain", "business_status": "not_received", "http_status": None, "latency_ms": round((time.monotonic() - started) * 1000), "payload": None, "response_is_json": False, "response_sha256": None, "transport_error": type(error.reason).__name__}
    raw_hash, latency_ms = hashlib.sha256(raw).hexdigest(), round((time.monotonic() - started) * 1000)
    if len(raw) > MAX_RESPONSE_BYTES:
        return {"outcome": "failed", "business_status": "response_too_large", "http_status": status, "latency_ms": latency_ms, "payload": None, "response_is_json": False, "response_sha256": None}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
        response_is_json = False
    else:
        response_is_json = True
    if not 200 <= status < 300:
        outcome, business = "failed", "not_evaluated"
    elif type(payload) is not dict:
        outcome, business = "uncertain", "invalid_payload"
    elif payload.get("success") is False:
        outcome, business = "failed", "reported_failure"
    elif payload.get("success") is True:
        outcome, business = "success", "reported_success"
    else:
        outcome, business = "uncertain", "missing_business_success"
    return {"outcome": outcome, "business_status": business, "http_status": status, "latency_ms": latency_ms, "payload": payload, "response_is_json": response_is_json, "response_sha256": raw_hash}


def run(args: argparse.Namespace, opener: Any | None = None) -> dict[str, Any]:
    manifest, plan, selected, checked_manifest_hash, plan_hash = resolve_approved_case(args.manifest, args.plan, args.case)
    policy = manifest["execution_policy"]
    expected, total_budget = _fixed_cost(selected["expected_cost"]), _fixed_cost(policy["total_budget_credits"])
    _safe_case_id(selected["case_id"])
    if not args.execute:
        return {"outcome": "dry_run", "case_id": selected["case_id"], "alias": selected["alias"], "plan_hash": plan_hash, "manifest_hash": checked_manifest_hash, "expected_credits": expected}
    frozen_idempotency_key = _frozen_idempotency_key(selected["idempotency_key"])
    if args.idempotency_key != frozen_idempotency_key:
        raise PilotError("CLI idempotency key does not match the frozen plan case")
    _verify_provenance_artifacts(manifest)
    _verify_approval_digest(args.approval_digest_file, plan_hash)
    key = load_key(args.env_file)
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=build_ssl_context()))
    ledger_path, private_result_dir = _plan_storage_paths(DEFAULT_LEDGER if args.ledger is None else args.ledger, DEFAULT_PRIVATE_RESULT_DIR if args.private_result_dir is None else args.private_result_dir, plan_hash)
    with LockedLedger(ledger_path) as ledger:
        _ensure_not_replayed(ledger.records, selected["case_id"], frozen_idempotency_key)
        prior_actual = _prior_actual_credits(ledger.records, plan, plan_hash, selected["case_id"])
        if prior_actual + expected > total_budget:
            raise PilotError("approved total budget would be exceeded")
        candidate = _candidates(manifest)[selected["alias"]]
        planned = {"record_type": "planned", "at": _now(), "case_id": selected["case_id"], "alias": selected["alias"], "tool_id": candidate["tool_id"], "arguments_hash": canonical_hash(selected["arguments"]), "expected_credits": expected, "approval_id": selected["approval_id"], "manifest_hash": checked_manifest_hash, "plan_hash": plan_hash, "connector_protocol_version": plan["connector_protocol_version"], "idempotency_key": frozen_idempotency_key}
        ledger.append(planned)
        response = post(opener, planned["tool_id"], selected["arguments"], key, frozen_idempotency_key, args.timeout)
        payload = response.pop("payload")
        response_is_json = response.pop("response_is_json")
        if response_is_json:
            private_result, private_result_status = _write_private_result(private_result_dir, selected["case_id"], response["response_sha256"], payload)
        else:
            private_result, private_result_status = None, "not_json"
        actual, remaining = _receipt(payload)
        actual_is_valid = actual is not None and math.isfinite(actual) and actual >= 0
        budget_violation = actual is not None and (not actual_is_valid or actual > expected or prior_actual + actual > total_budget)
        outcome = "budget_violation" if budget_violation else ("receipt_missing" if response["outcome"] == "success" and actual is None else response["outcome"])
        quality = validate_quality(payload, candidate, selected["arguments"]) if response["outcome"] == "success" else {"status": "not_evaluated", "checks": [], "failure_codes": []}
        if outcome == "success" and quality["status"] == "failed":
            outcome = "failed"
            response["business_status"] = "quality_failed"
        if budget_violation:
            response["business_status"] = "budget_violation"
        final_state = "uncertain" if outcome == "uncertain" else "settled"
        result = {"record_type": final_state, "at": _now(), **{name: planned[name] for name in ("case_id", "alias", "tool_id", "arguments_hash", "expected_credits", "approval_id", "manifest_hash", "plan_hash", "connector_protocol_version", "idempotency_key")}, **response, "outcome": outcome, "execution_id": _execution_id(payload), "actual_credits": actual if actual_is_valid else None, "remaining_credits": remaining, "receipt_status": "budget_violation" if budget_violation else ("reported" if actual_is_valid else "missing"), "quality_status": quality["status"], "quality_checks": quality["checks"], "quality_failure_codes": quality["failure_codes"], "private_result": private_result, "private_result_status": private_result_status, "sanitized_error": _sanitized_error(payload), "result_shape": _result_shape(payload), "result_top_level_keys": _result_top_level_keys(payload), "business_success_raw": _business_success_raw(payload)}
        ledger.append(result)
    return {name: result.get(name) for name in ("outcome", "case_id", "alias", "tool_id", "http_status", "business_status", "actual_credits", "remaining_credits", "latency_ms", "receipt_status")}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--idempotency-key", required=True, help="must exactly match the selected frozen plan case")
    parser.add_argument("--ledger", type=Path, help="ledger base path; the runner appends a plan-hash subdirectory")
    parser.add_argument("--private-result-dir", type=Path, help="private evidence base path; the runner appends a plan-hash subdirectory")
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--execute", action="store_true", help="permit one live POST after external approval-digest validation; default is dry-run")
    parser.add_argument("--approval-digest-file", type=Path, help="external, owner-only 0600 regular file containing this frozen plan hash; required with --execute")
    args = parser.parse_args(argv)
    if not args.case.strip() or not args.idempotency_key.strip() or args.timeout <= 0:
        parser.error("case and idempotency key must be non-empty and timeout must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        print(json.dumps(run(parse_args(argv)), ensure_ascii=False, sort_keys=True))
    except PilotError as error:
        print(json.dumps({"outcome": "not_dispatched", "reason": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

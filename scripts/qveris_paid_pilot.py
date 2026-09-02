#!/usr/bin/env python3
"""Dry-run one frozen pilot case by default; --execute permits one approved call."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any


BASE_URL = "https://qveris.ai/api/v1"
DEFAULT_LEDGER = Path("artifacts/tool-audit/pilot-ledger.jsonl")
SYSTEM_CA_FILE = Path("/etc/ssl/cert.pem")
MAX_RESPONSE_BYTES = 1_000_000
SENSITIVE_NAMES = ("authorization", "api_key", "apikey", "token", "secret", "password", "cookie", "header")
ERROR_FIELDS = ("error_code", "status", "code", "error_message", "message")
LEDGER_STATES = frozenset({"planned", "dispatched", "settled", "uncertain"})
CONNECTOR_PROTOCOL_VERSION = "qveris.execute.parameters.v1"


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
    if type(value) in (int, float) and not isinstance(value, bool) and value >= 0:
        return float(value)
    raise PilotError("unknown or unbounded expected cost is not approved for live execution")


def _plan_case(plan: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    if set(plan) != {"approval_id", "manifest_hash", "connector_protocol_version", "cases"} or type(plan.get("approval_id")) is not str or not plan["approval_id"] or plan.get("connector_protocol_version") != CONNECTOR_PROTOCOL_VERSION:
        raise PilotError("plan schema is invalid")
    rows = plan.get("cases")
    if type(rows) is not list:
        raise PilotError("plan cases are invalid")
    matches = [row for row in rows if type(row) is dict and row.get("case_id") == case_id]
    if len(matches) != 1:
        raise PilotError("case is absent or duplicated in approved plan")
    row = matches[0]
    if set(row) != {"case_id", "alias", "arguments", "expected_cost", "approval_id"}:
        raise PilotError("plan case must contain exact execution fields")
    if any(type(row[name]) is not str or not row[name] for name in ("case_id", "alias", "approval_id")) or type(row["arguments"]) is not dict:
        raise PilotError("plan case types are invalid")
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
    candidate = _candidates(manifest).get(planned["alias"])
    if candidate is None or candidate.get("live_status") != "approved_for_pilot" or _has_blocked(candidate):
        raise PilotError("candidate is not approved for pilot")
    if type(candidate.get("tool_id")) is not str or not candidate["tool_id"]:
        raise PilotError("candidate tool_id is invalid")
    if type(candidate.get("call_parameters")) is not dict or canonical_hash(candidate["call_parameters"]) != canonical_hash(planned["arguments"]):
        raise PilotError("plan arguments do not exactly match manifest")
    if _fixed_cost(candidate.get("catalog_expected_credits")) != _fixed_cost(planned["expected_cost"]):
        raise PilotError("plan expected cost does not exactly match manifest")
    return manifest, plan, planned, actual_manifest_hash, plan_hash


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


def _approved_total(records: list[Mapping[str, Any]], plan_hash: str, approval_id: str, connector_protocol_version: str, case_id: str, idempotency_key: str) -> float:
    seen_cases: set[str] = set()
    total = 0.0
    for record in records:
        if record.get("plan_hash") == plan_hash or record["case_id"] == case_id or record["idempotency_key"] == idempotency_key:
            raise PilotError("case, plan hash, or idempotency key was already planned; do not resend")
        if record["record_type"] == "planned" and record.get("plan_hash") == plan_hash and record.get("approval_id") == approval_id and record.get("connector_protocol_version") == connector_protocol_version:
            if record["case_id"] in seen_cases:
                raise PilotError("pilot ledger contains duplicate planned case")
            seen_cases.add(record["case_id"])
            total += float(record["expected_credits"])
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
    _verify_approval_digest(args.approval_digest_file, plan_hash)
    key = load_key(args.env_file)
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=build_ssl_context()))
    with LockedLedger(args.ledger) as ledger:
        if _approved_total(ledger.records, plan_hash, selected["approval_id"], plan["connector_protocol_version"], selected["case_id"], args.idempotency_key) + expected > total_budget:
            raise PilotError("approved total budget would be exceeded")
        candidate = _candidates(manifest)[selected["alias"]]
        planned = {"record_type": "planned", "at": _now(), "case_id": selected["case_id"], "alias": selected["alias"], "tool_id": candidate["tool_id"], "arguments_hash": canonical_hash(selected["arguments"]), "expected_credits": expected, "approval_id": selected["approval_id"], "manifest_hash": checked_manifest_hash, "plan_hash": plan_hash, "connector_protocol_version": plan["connector_protocol_version"], "idempotency_key": args.idempotency_key}
        ledger.append(planned)
        response = post(opener, planned["tool_id"], selected["arguments"], key, args.idempotency_key, args.timeout)
        payload = response.pop("payload")
        response_is_json = response.pop("response_is_json")
        if response_is_json:
            private_result, private_result_status = _write_private_result(args.private_result_dir, selected["case_id"], response["response_sha256"], payload)
        else:
            private_result, private_result_status = None, "not_json"
        actual, remaining = _receipt(payload)
        budget_violation = actual is not None and actual > total_budget
        outcome = "budget_violation" if budget_violation else ("receipt_missing" if response["outcome"] == "success" and actual is None else response["outcome"])
        if budget_violation:
            response["business_status"] = "budget_violation"
        final_state = "uncertain" if outcome == "uncertain" else "settled"
        result = {"record_type": final_state, "at": _now(), **{name: planned[name] for name in ("case_id", "alias", "tool_id", "arguments_hash", "expected_credits", "approval_id", "manifest_hash", "plan_hash", "connector_protocol_version", "idempotency_key")}, **response, "outcome": outcome, "execution_id": _execution_id(payload), "actual_credits": actual, "remaining_credits": remaining, "receipt_status": "budget_violation" if budget_violation else ("reported" if actual is not None else "missing"), "private_result": private_result, "private_result_status": private_result_status, "sanitized_error": _sanitized_error(payload), "result_shape": _result_shape(payload), "result_top_level_keys": _result_top_level_keys(payload), "business_success_raw": _business_success_raw(payload)}
        ledger.append(result)
    return {name: result.get(name) for name in ("outcome", "case_id", "alias", "tool_id", "http_status", "business_status", "actual_credits", "remaining_credits", "latency_ms", "receipt_status")}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--private-result-dir", type=Path, default=Path("artifacts/private"))
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

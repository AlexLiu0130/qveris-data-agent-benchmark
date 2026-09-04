"""Crash-safe, unscored execution records for the GET benchmark.

This module deliberately owns execution evidence only.  It does not contain a
scorer, an oracle, a ranking, or any network/Gateway implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import threading
import time
from typing import Any, Protocol

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUITES = ("realtime_quote", "historical_price", "financial_statements")
_SENSITIVE = frozenset({
    "authorization", "token", "secret", "api_key", "password", "cookie",
    "credential", "access_token", "private_key", "idempotency_key", "header",
    "raw_response", "provider_payload", "provider_response",
})
_USAGE_TOKENS = frozenset({"input_tokens", "output_tokens", "total_tokens"})
_USAGE_AUDIT = frozenset({"receipt_id", "measurement_version", "cache_status", "request_id", "issuer"})
_USAGE = _USAGE_TOKENS | _USAGE_AUDIT
_EVENTS = frozenset({"run_started", "dispatch_intent", "reference_before", "reference_after", "reference_after_unavailable", "terminal", "run_finished"})
_GET_RESPONSE_FIELDS = frozenset({"schema_version", "status", "resolved_request", "data", "as_of", "source", "clarification", "terminal_reason", "meta"})
_GET_DATA_FORBIDDEN = frozenset({"provider", "provider_response", "provider_payload", "raw_response", "receipt", "execution_id"})
_CANONICAL_RESPONSE_STATUSES = frozenset({"success", "partial", "needs_clarification", "unsupported", "no_data", "error"})
_EXECUTION_OUTCOME_STATES = {
    "success": "success",
    "partial": "incomplete",
    "needs_clarification": "blocked",
    "unsupported": "blocked",
    "no_data": "blocked",
    "error": "failed",
    "runtime_evidence_invalid": "failed",
}

_VARIANT_IDENTITY_FIELDS = (
    "agent_variant_id", "agent_version", "get_variant_id", "get_version",
    "model_identifier", "model_version", "model_config_digest",
)
_TOOL_ALIASES = {"get": "get", "public_get": "get", "qveris_get": "get"}


class RunBackendError(ValueError):
    """A rejected manifest, journal, or run operation."""


@dataclass(frozen=True)
class ExecutionEvidence:
    """Trusted adapter attestation for one completed public GET call."""

    agent_variant_id: str
    agent_version: str
    get_variant_id: str
    get_version: str
    model_identifier: str
    model_version: str
    model_config_digest: str
    agent_invocations: int
    tool_executions: int
    structured_outputs: int
    tools_used: tuple[str, ...]


@dataclass(frozen=True)
class PublicGetResult:
    """The only accepted adapter result: public data plus private discipline evidence."""

    public_response: Mapping[str, Any]
    execution_evidence: ExecutionEvidence


class PublicGetClient(Protocol):
    """Minimal trusted-adapter boundary; clients may expose this call as run/get."""

    def __call__(self, query: str, *, request_id: str, idempotency_key: str) -> PublicGetResult: ...


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RunBackendError("value must be JSON") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _safe_id(value: Any, field: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise RunBackendError("%s must be a safe opaque id" % field)
    return value


def _variant_identity(variant: Mapping[str, Any]) -> dict[str, str]:
    return {field: variant[field] for field in _VARIANT_IDENTITY_FIELDS}


def _variant_contract_digest(variants: list[Mapping[str, Any]]) -> str:
    return _digest([
        {"variant_id": variant["variant_id"], **_variant_identity(variant)}
        for variant in sorted(variants, key=lambda item: item["stable_display_order"])
    ])


def _validate_variant_identity(value: Any, field: str = "variant identity") -> dict[str, str]:
    if type(value) is not dict or set(value) != set(_VARIANT_IDENTITY_FIELDS):
        raise RunBackendError("%s has an invalid schema" % field)
    result = {}
    for name in _VARIANT_IDENTITY_FIELDS:
        result[name] = _safe_id(value.get(name), "%s.%s" % (field, name))
    if _SHA256.fullmatch(result["model_config_digest"]) is None:
        raise RunBackendError("%s.model_config_digest must be a SHA256 digest" % field)
    return result


def _normalize_tool_name(value: Any) -> str:
    if type(value) is not str:
        raise RunBackendError("execution evidence tool name is invalid")
    normalized = re.sub(r"[\s._-]+", "_", value.strip().lower()).strip("_")
    if normalized in {"search", "inspect"}:
        raise RunBackendError("Search and Inspect are forbidden")
    if normalized not in _TOOL_ALIASES:
        raise RunBackendError("execution evidence tool is not allowed")
    return _TOOL_ALIASES[normalized]


def _evidence_projection(value: Any, expected_identity: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not ExecutionEvidence:
        raise RunBackendError("GET result requires trusted ExecutionEvidence")
    identity = _validate_variant_identity({field: getattr(value, field) for field in _VARIANT_IDENTITY_FIELDS}, "execution evidence")
    if identity != _variant_identity(expected_identity):
        raise RunBackendError("execution evidence identity does not match manifest variant")
    counts = (value.agent_invocations, value.tool_executions, value.structured_outputs)
    if any(type(count) is not int or isinstance(count, bool) or count != 1 for count in counts):
        raise RunBackendError("execution evidence requires exactly one agent, tool, and structured output")
    tools = tuple(_normalize_tool_name(item) for item in value.tools_used) if type(value.tools_used) is tuple else ()
    if tools != ("get",):
        raise RunBackendError("execution evidence requires exactly one public get tool")
    return {**identity, "agent_invocations": 1, "tool_executions": 1, "structured_outputs": 1, "tools_used": ["get"]}


def _reject_sensitive(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                raise RunBackendError("JSON object keys must be strings")
            normalized = key.lower().replace("-", "_")
            compact = "".join(char for char in normalized if char.isalnum())
            if normalized in _SENSITIVE or any(part in _SENSITIVE for part in normalized.split("_")) or any(secret.replace("_", "") in compact for secret in _SENSITIVE):
                raise RunBackendError("sensitive field is not permitted: %s" % (path + key))
            _reject_sensitive(child, path + key + ".")
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive(child, path)


def _freeze_digest(manifest: Mapping[str, Any]) -> str:
    for name in ("freeze_digest", "manifest_digest", "policy_digest"):
        value = manifest.get(name)
        if type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    raise RunBackendError("manifest requires a SHA256 freeze digest")


def _contains_sla(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any("sla" in str(key).lower() or _contains_sla(child) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_sla(child) for child in value)
    return False


def _validate_reference_contract(value: Any) -> None:
    """Require a safe, frozen independent-reference window contract."""
    if type(value) is not dict or not value:
        raise RunBackendError("realtime_quote cases require a non-empty reference_contract object")
    _reject_sensitive(value)
    _canonical(value)
    if type(value.get("source_contract_hash")) is not str or _SHA256.fullmatch(value["source_contract_hash"]) is None:
        raise RunBackendError("reference_contract.source_contract_hash must be a SHA256 digest")
    _safe_id(value.get("window_rule_version"), "reference_contract.window_rule_version")


def _validate_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    if type(raw) is not dict:
        raise RunBackendError("manifest must be a JSON object")
    _reject_sensitive(raw)
    manifest = json.loads(_canonical(raw))
    _safe_id(manifest.get("run_id"), "run_id")
    if manifest.get("mode") not in ("diagnostic", "official"):
        raise RunBackendError("mode must be diagnostic or official")
    _freeze_digest(manifest)
    if type(manifest.get("policy")) is not dict or not manifest["policy"]:
        raise RunBackendError("manifest requires a non-empty frozen policy object")
    scoring_contract = manifest.get("scoring_contract")
    if scoring_contract is not None:
        if type(scoring_contract) is not dict or set(scoring_contract) != {"policy_digest", "oracle_bundle_digest", "scorer_version", "scorer_digest", "variant_contract_digest"} or not all(type(scoring_contract.get(name)) is str and _SHA256.fullmatch(scoring_contract[name]) for name in ("policy_digest", "oracle_bundle_digest", "scorer_digest", "variant_contract_digest")) or type(scoring_contract.get("scorer_version")) is not str or not scoring_contract["scorer_version"]:
            raise RunBackendError("scoring_contract requires scorer, policy, and oracle digests")
    if type(manifest.get("timeout_ms")) is not int or isinstance(manifest["timeout_ms"], bool) or manifest["timeout_ms"] <= 0:
        raise RunBackendError("timeout_ms must be a positive integer, not an SLA")
    if manifest.get("concurrency") != 1 or isinstance(manifest["concurrency"], bool):
        raise RunBackendError("only serial concurrency=1 is supported")
    if _contains_sla(manifest):
        raise RunBackendError("SLA declarations do not belong in a benchmark run manifest")
    variants = manifest.get("variants")
    if type(variants) is not list or not 2 <= len(variants) <= 8:
        raise RunBackendError("manifest requires 2-8 variants")
    variant_ids, orders, identities = set(), set(), set()
    for variant in variants:
        if type(variant) is not dict or set(variant) != {"variant_id", "stable_display_order", *_VARIANT_IDENTITY_FIELDS}:
            raise RunBackendError("variant must be an object")
        variant_id = _safe_id(variant.get("variant_id"), "variant_id")
        order = variant.get("stable_display_order")
        if variant_id in variant_ids or type(order) is not int or isinstance(order, bool) or order in orders:
            raise RunBackendError("variant ids and stable_display_order values must be unique")
        identity = _validate_variant_identity(_variant_identity(variant), "variant")
        identity_key = tuple(identity[field] for field in _VARIANT_IDENTITY_FIELDS)
        if identity_key in identities:
            raise RunBackendError("variant identities must be unique")
        variant_ids.add(variant_id)
        orders.add(order)
        identities.add(identity_key)
    if scoring_contract is not None and scoring_contract["variant_contract_digest"] != _variant_contract_digest(variants):
        raise RunBackendError("scoring_contract variant identity digest does not match manifest")
    cases = manifest.get("cases")
    if type(cases) is not list or not cases:
        raise RunBackendError("manifest requires at least one case")
    case_ids: set[str] = set()
    suite_counts = {suite: 0 for suite in _SUITES}
    suite_case_types = {suite: {"normal": 0, "boundary": 0} for suite in _SUITES}
    for case in cases:
        if type(case) is not dict:
            raise RunBackendError("case must be an object")
        case_id = _safe_id(case.get("case_id"), "case_id")
        if case_id in case_ids or case.get("suite") not in _SUITES or type(case.get("query")) is not str or not case["query"].strip():
            raise RunBackendError("cases require unique id, known suite, and non-empty query")
        if case["suite"] == "realtime_quote":
            _validate_reference_contract(case.get("reference_contract"))
        score_case = case.get("score_case")
        if score_case is not None:
            if type(score_case) is not dict or set(score_case) != {"expected_status", "oracle_id", "case_type"}:
                raise RunBackendError("score_case has an invalid schema")
            statuses = score_case.get("expected_status")
            case_type = score_case.get("case_type")
            allowed = {"success"} if case_type == "normal" else {"needs_clarification", "unsupported", "no_data"} if case_type == "boundary" else set()
            if type(statuses) is not list or not statuses or len(statuses) != len(set(statuses)) or any(type(status) is not str or status not in allowed for status in statuses):
                raise RunBackendError("score_case.expected_status must be a non-empty list")
            _safe_id(score_case.get("oracle_id"), "score_case.oracle_id")
            suite_case_types[case["suite"]][case_type] += 1
        elif manifest["mode"] == "official":
            raise RunBackendError("official runs require score_case for every case")
        case_ids.add(case_id)
        suite_counts[case["suite"]] += 1
    if manifest["mode"] == "official" and suite_counts != {suite: 100 for suite in _SUITES}:
        raise RunBackendError("official runs require exactly 100 cases for each of the three suites")
    if manifest["mode"] == "official" and suite_case_types != {suite: {"normal": 80, "boundary": 20} for suite in _SUITES}:
        raise RunBackendError("official runs require 80 normal and 20 boundary cases per suite")
    return manifest


def _require_directory(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise RunBackendError("unknown run_id") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RunBackendError("run path is not a directory")


def _mkdir_private(path: Path) -> None:
    try:
        _require_directory(path)
    except RunBackendError:
        if os.path.lexists(path):
            raise
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        _require_directory(path)
    os.chmod(path, 0o700)


def _open_regular(path: Path, flags: int, mode: int = 0o600) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RunBackendError("safe no-follow file access is unavailable")
    try:
        fd = os.open(path, flags | nofollow, mode)
    except FileNotFoundError as exc:
        raise RunBackendError("unknown run_id") from exc
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise RunBackendError("run file is unsafe") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RunBackendError("run file is not regular")
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_regular(path: Path) -> bytes:
    fd = _open_regular(path, os.O_RDONLY)
    try:
        return b"".join(iter(lambda: os.read(fd, 65536), b""))
    finally:
        os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    """A successful short write is not a durable journal entry."""
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise RunBackendError("could not write run file")
        offset += written


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RunStore:
    """The only disk boundary for a run manifest, evidence journal, and snapshot."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        _mkdir_private(self.root)

    def path_for(self, run_id: str) -> Path:
        return self.root / _safe_id(run_id, "run_id")

    def create(self, manifest: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        normalized = _validate_manifest(manifest)
        run_dir = self.path_for(normalized["run_id"])
        if os.path.lexists(run_dir):
            _require_directory(run_dir)
            existing = self.load_manifest(normalized["run_id"])
            if _digest(existing) != _digest(normalized):
                raise RunBackendError("run_id already belongs to a different manifest")
            return existing, _digest(existing)
        _require_directory(self.root)
        _mkdir_private(run_dir)
        path = run_dir / "manifest.json"
        payload = _canonical(normalized)
        try:
            fd = _open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            existing = self.load_manifest(normalized["run_id"])
            if _digest(existing) == _digest(normalized):
                return existing, _digest(existing)
            raise RunBackendError("run_id already belongs to a different manifest") from exc
        try:
            _write_all(fd, payload)
            os.fsync(fd)
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        _fsync_directory(run_dir)
        _fsync_directory(self.root)
        return normalized, _digest(normalized)

    def load_manifest(self, run_id: str) -> dict[str, Any]:
        run_dir = self.path_for(run_id)
        _require_directory(run_dir)
        try:
            raw = _read_regular(run_dir / "manifest.json")
        except RunBackendError as exc:
            if str(exc) == "unknown run_id":
                raise
            raise
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunBackendError("manifest is not valid JSON") from exc
        if _canonical(value) != raw:
            raise RunBackendError("manifest must be canonical JSON")
        return _validate_manifest(value)

    @contextmanager
    def locked(self, run_id: str):
        run_dir = self.path_for(run_id)
        _require_directory(run_dir)
        self.load_manifest(run_id)
        fd = _open_regular(run_dir / ".lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def append(self, run_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        manifest = self.load_manifest(run_id)
        run_dir = self.path_for(run_id)
        _require_directory(run_dir)
        path = run_dir / "events.jsonl"
        fd = _open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            events = self.events(run_id, manifest_hash=_digest(manifest))
            record = dict(event)
            record["sequence"] = len(events) + 1
            record["manifest_hash"] = _digest(manifest)
            if record.get("event_type") in {"reference_before", "reference_after"} and isinstance(record.get("reference"), Mapping) and "hash" not in record["reference"]:
                record["reference"] = _reference_projection(record["reference"])
            record["previous_event_hash"] = events[-1]["event_hash"] if events else None
            record["event_hash"] = _event_hash(record)
            _validate_event(record, run_id, len(events) + 1, _digest(manifest))
            _validate_journal(manifest, events + [record])
            payload = _canonical(record) + b"\n"
            _write_all(fd, payload)
            os.fsync(fd)
            os.fchmod(fd, 0o600)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        _fsync_directory(run_dir)
        return record

    def events(self, run_id: str, *, manifest_hash: str | None = None) -> list[dict[str, Any]]:
        manifest = self.load_manifest(run_id)
        expected_hash = manifest_hash or _digest(manifest)
        run_dir = self.path_for(run_id)
        _require_directory(run_dir)
        path = run_dir / "events.jsonl"
        if not os.path.lexists(path):
            return []
        raw = _read_regular(path)
        if raw and not raw.endswith(b"\n"):
            raise RunBackendError("event journal is truncated")
        result: list[dict[str, Any]] = []
        for sequence, line in enumerate(raw.splitlines(), start=1):
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RunBackendError("event journal contains invalid JSON") from exc
            if _canonical(event) != line:
                raise RunBackendError("event journal must contain canonical JSON lines")
            _validate_event(event, run_id, sequence, expected_hash)
            result.append(event)
        _validate_journal(manifest, result)
        return result

    def write_snapshot(self, run_id: str, snapshot: Mapping[str, Any]) -> None:
        run_dir = self.path_for(run_id)
        _require_directory(run_dir)
        self.load_manifest(run_id)
        destination = run_dir / "snapshot.json"
        if os.path.lexists(destination):
            info = os.lstat(destination)
            if not stat.S_ISREG(info.st_mode):
                raise RunBackendError("run file is unsafe")
        payload = _canonical(snapshot)
        name = run_dir / (".snapshot-" + os.urandom(16).hex())
        fd = _open_regular(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            os.fchmod(fd, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RunBackendError("run file is not regular")
            _write_all(fd, payload)
            os.fsync(fd)
            os.replace(name, destination)
            _fsync_directory(run_dir)
        finally:
            os.close(fd)
            if os.path.lexists(name):
                os.unlink(name)

    # Score evidence has its own chain: execution evidence remains immutable.
    def score_events(self, run_id: str) -> list[dict[str, Any]]:
        manifest = self.load_manifest(run_id)
        path = self.path_for(run_id) / "score-events.jsonl"
        if not os.path.lexists(path):
            return []
        raw = _read_regular(path)
        if raw and not raw.endswith(b"\n"):
            raise RunBackendError("score journal is truncated")
        events: list[dict[str, Any]] = []
        for sequence, line in enumerate(raw.splitlines(), 1):
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RunBackendError("score journal contains invalid JSON") from exc
            if _canonical(event) != line:
                raise RunBackendError("score journal must contain canonical JSON lines")
            _validate_score_event(event, sequence, _digest(manifest))
            events.append(event)
        previous = None
        for event in events:
            if event["previous_score_hash"] != previous:
                raise RunBackendError("score hash chain mismatch")
            previous = event["score_event_hash"]
        _validate_score_journal(manifest, events, self.events(run_id))
        return events

    def append_score_event(self, run_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        manifest = self.load_manifest(run_id)
        run_dir = self.path_for(run_id)
        path = run_dir / "score-events.jsonl"
        fd = _open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            events = self.score_events(run_id)
            record = dict(event)
            record["sequence"] = len(events) + 1
            record["manifest_hash"] = _digest(manifest)
            record["previous_score_hash"] = events[-1]["score_event_hash"] if events else None
            record["score_event_hash"] = _digest({key: value for key, value in record.items() if key != "score_event_hash"})
            _validate_score_event(record, len(events) + 1, _digest(manifest))
            _validate_score_journal(manifest, events + [record], self.events(run_id))
            _write_all(fd, _canonical(record) + b"\n")
            os.fsync(fd)
            os.fchmod(fd, 0o600)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        _fsync_directory(run_dir)
        return record

    def load_score_projection(self, run_id: str) -> dict[str, Any] | None:
        path = self.path_for(run_id) / "score-projection.json"
        if not os.path.lexists(path):
            return None
        try:
            value = json.loads(_read_regular(path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunBackendError("score projection is not valid JSON") from exc
        if _canonical(value) != _read_regular(path):
            raise RunBackendError("score projection must be canonical JSON")
        if type(value) is not dict or value.get("projection_hash") != _score_projection_hash(value):
            raise RunBackendError("score projection hash mismatch")
        return value

    def write_score_projection(self, run_id: str, projection: Mapping[str, Any]) -> None:
        run_dir = self.path_for(run_id)
        self.load_manifest(run_id)
        destination = run_dir / "score-projection.json"
        if os.path.lexists(destination) and not stat.S_ISREG(os.lstat(destination).st_mode):
            raise RunBackendError("run file is unsafe")
        payload = _canonical(projection)
        name = run_dir / (".score-projection-" + os.urandom(16).hex())
        fd = _open_regular(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, payload)
            os.fsync(fd)
            os.replace(name, destination)
            _fsync_directory(run_dir)
        finally:
            os.close(fd)
            if os.path.lexists(name):
                os.unlink(name)


def _validate_event(event: Any, run_id: str, sequence: int, manifest_hash: str) -> None:
    if type(event) is not dict or event.get("event_type") not in _EVENTS:
        raise RunBackendError("illegal event")
    # Token counts are safe numeric receipts, unlike token values/credentials.
    public = event.get("public_response")
    safe_event = {key: value for key, value in event.items() if key != "usage"}
    if isinstance(public, Mapping) and isinstance(public.get("meta"), Mapping):
        safe_public = dict(public); safe_public.pop("meta")
        safe_event["public_response"] = safe_public
    _reject_sensitive(safe_event)
    usage = event.get("usage")
    if usage is not None and usage != "unknown":
        if not isinstance(usage, Mapping) or not set(usage).issubset(_USAGE):
            raise RunBackendError("usage receipt is invalid")
        for name, value in usage.items():
            if name in _USAGE_TOKENS and (type(value) is not int or isinstance(value, bool) or value < 0):
                raise RunBackendError("usage receipt is invalid")
            if name in _USAGE_AUDIT and (type(value) is not str or not value):
                raise RunBackendError("usage receipt is invalid")
    if event.get("sequence") != sequence or event.get("manifest_hash") != manifest_hash:
        raise RunBackendError("event sequence or manifest hash mismatch")
    if event.get("previous_event_hash") is not None and not (type(event["previous_event_hash"]) is str and re.fullmatch(r"[0-9a-f]{64}", event["previous_event_hash"])):
        raise RunBackendError("event previous hash is invalid")
    if type(event.get("event_hash")) is not str or not re.fullmatch(r"[0-9a-f]{64}", event["event_hash"]) or event["event_hash"] != _event_hash(event):
        raise RunBackendError("event hash mismatch")
    if "event_id" in event and (type(event["event_id"]) is not str or not event["event_id"]):
        raise RunBackendError("event id is invalid")
    if "emitted_at" in event and (type(event["emitted_at"]) not in (int, float) or isinstance(event["emitted_at"], bool)):
        raise RunBackendError("event timestamp is invalid")
    if event["event_type"] in {"dispatch_intent", "reference_before", "reference_after", "reference_after_unavailable", "terminal"}:
        for field in ("cell_id", "attempt_id"):
            _safe_id(event.get(field), field)
        if "trial" in event and event["trial"] != 1:
            raise RunBackendError("cell event trial must be 1")
    if event["event_type"] == "dispatch_intent":
        if event.get("trial") != 1:
            raise RunBackendError("dispatch intent trial must be 1")
        if not all(type(event.get(field)) is str and re.fullmatch(r"[0-9a-f]{64}", event[field]) for field in ("input_hash", "request_hash")):
            raise RunBackendError("dispatch intent must contain hashes")
        _validate_variant_identity(event.get("variant_identity"), "dispatch variant identity")
    if event["event_type"] == "run_finished" and event.get("status") not in {"execution_complete", "execution_failed", "incomplete"}:
        raise RunBackendError("run finish status is invalid")
    if event["event_type"] == "terminal":
        if type(event.get("elapsed_ms")) not in (int, float) or event["elapsed_ms"] < 0 or type(event.get("transport_status")) is not str:
            raise RunBackendError("terminal event is incomplete")
        response = event.get("public_response")
        if response is None:
            if event.get("response_hash") is not None:
                raise RunBackendError("terminal response hash is invalid")
        elif not isinstance(response, Mapping) or event.get("response_hash") != _digest(response):
            raise RunBackendError("terminal response hash mismatch")
        if isinstance(response, Mapping) and "meta" in response:
            meta = response["meta"]
            if not isinstance(meta, Mapping) or set(meta) != {"usage"} or not isinstance(meta["usage"], Mapping) or not set(meta["usage"]).issubset(_USAGE):
                raise RunBackendError("terminal meta usage is invalid")
        meta_usage = response.get("meta", {}).get("usage") if isinstance(response, Mapping) and isinstance(response.get("meta"), Mapping) else None
        if isinstance(meta_usage, Mapping) and meta_usage:
            if event.get("usage_source") != "public_meta_usage" or usage == "unknown" or usage != meta_usage:
                raise RunBackendError("usage receipt source is invalid")
        elif event.get("usage_source") == "public_meta_usage" or usage != "unknown":
            raise RunBackendError("usage receipt source is invalid")
        _validate_variant_identity(event.get("variant_identity"), "terminal variant identity")
        evidence = event.get("execution_evidence")
        if event.get("transport_status") == "completed":
            if type(evidence) is not dict:
                raise RunBackendError("completed terminal requires execution evidence")
            identity = _validate_variant_identity({field: evidence.get(field) for field in _VARIANT_IDENTITY_FIELDS}, "terminal execution evidence")
            if identity != _validate_variant_identity(event["variant_identity"], "terminal variant identity"):
                raise RunBackendError("terminal execution evidence identity mismatch")
            if (evidence.get("agent_invocations"), evidence.get("tool_executions"), evidence.get("structured_outputs"), evidence.get("tools_used")) != (1, 1, 1, ["get"]):
                raise RunBackendError("terminal execution evidence violates runtime discipline")
        elif evidence is not None:
            raise RunBackendError("non-completed terminal cannot carry execution evidence")
    if event["event_type"] in {"reference_before", "reference_after"}:
        reference = event.get("reference")
        if not isinstance(reference, Mapping) or set(reference) != {"hash", "as_of", "source", "comparability"} or reference.get("hash") != _digest({key: value for key, value in reference.items() if key != "hash"}):
            raise RunBackendError("reference hash mismatch")


def _event_hash(event: Mapping[str, Any]) -> str:
    return _digest({key: value for key, value in event.items() if key != "event_hash"})


def _score_projection_hash(projection: Mapping[str, Any]) -> str:
    """The journal tail is deliberately outside the artifact body hash.

    Otherwise the event that carries this hash would recursively depend on its
    own hash through ``score_tail_hash``.
    """
    return _digest({key: value for key, value in projection.items() if key not in {"projection_hash", "score_tail_hash"}})


def _validate_score_event(event: Any, sequence: int, manifest_hash: str) -> None:
    if type(event) is not dict or event.get("event_type") not in {"score_started", "score_record", "scorer_projection"}:
        raise RunBackendError("illegal score event")
    _reject_sensitive(event)
    if event.get("sequence") != sequence or event.get("manifest_hash") != manifest_hash:
        raise RunBackendError("score event sequence or manifest hash mismatch")
    previous = event.get("previous_score_hash")
    if previous is not None and (type(previous) is not str or _SHA256.fullmatch(previous) is None):
        raise RunBackendError("score previous hash is invalid")
    if type(event.get("score_event_hash")) is not str or _SHA256.fullmatch(event["score_event_hash"]) is None:
        raise RunBackendError("score event hash is invalid")
    if event["score_event_hash"] != _digest({key: value for key, value in event.items() if key != "score_event_hash"}):
        raise RunBackendError("score event hash mismatch")
    bindings = event.get("bindings")
    if type(bindings) is not dict or set(bindings) != {"execution_tail_hash", "policy_digest", "oracle_bundle_digest", "scorer_version", "scorer_digest", "variant_contract_digest"} or not all(type(bindings.get(name)) is str and _SHA256.fullmatch(bindings[name]) for name in ("execution_tail_hash", "policy_digest", "oracle_bundle_digest", "scorer_digest", "variant_contract_digest")) or type(bindings.get("scorer_version")) is not str or not bindings["scorer_version"]:
        raise RunBackendError("score event bindings are invalid")
    if event["event_type"] == "score_record":
        record = event.get("record")
        if type(record) is not dict:
            raise RunBackendError("score record is invalid")
        for field in ("variant_id", "case_id", "cell_id", "response_hash", "oracle_hash"):
            if field in record and record[field] is not None:
                if field.endswith("_hash"):
                    if type(record[field]) is not str or _SHA256.fullmatch(record[field]) is None:
                        raise RunBackendError("score record hash is invalid")
                else:
                    _safe_id(record[field], field)
        if record.get("trial") != 1:
            raise RunBackendError("score record trial must be 1")
        if "variant_identity" in record:
            _validate_variant_identity(record["variant_identity"], "score record variant identity")
    if event["event_type"] == "scorer_projection":
        if set(event) - {"event_type", "bindings", "projection_hash", "sequence", "manifest_hash", "previous_score_hash", "score_event_hash"} or type(event.get("projection_hash")) is not str or _SHA256.fullmatch(event["projection_hash"]) is None:
            raise RunBackendError("score projection event is invalid")


def _validate_score_journal(manifest: Mapping[str, Any], events: list[dict[str, Any]], execution: list[dict[str, Any]]) -> None:
    """Accept only a resumable prefix of the deterministic score state machine."""
    if not events:
        return
    contract = manifest.get("scoring_contract")
    if type(contract) is not dict or set(contract) != {"policy_digest", "oracle_bundle_digest", "scorer_version", "scorer_digest", "variant_contract_digest"}:
        raise RunBackendError("score journal requires a scoring contract")
    if not execution or execution[-1].get("event_type") != "run_finished":
        raise RunBackendError("score journal requires a finished execution tail")
    expected_bindings = {
        "execution_tail_hash": execution[-1]["event_hash"],
        "policy_digest": contract["policy_digest"],
        "oracle_bundle_digest": contract["oracle_bundle_digest"],
        "scorer_version": contract["scorer_version"],
        "scorer_digest": contract["scorer_digest"],
        "variant_contract_digest": contract["variant_contract_digest"],
    }
    bindings = events[0].get("bindings")
    if events[0].get("event_type") != "score_started":
        raise RunBackendError("score journal must start once")
    if bindings != expected_bindings:
        raise RunBackendError("score journal bindings do not match the manifest contract")
    expected = [
        (variant["variant_id"], case["case_id"], 1, "cell-" + _digest([manifest["run_id"], variant["variant_id"], case["case_id"], 1])[:48], _variant_identity(variant))
        for variant in sorted(manifest["variants"], key=lambda item: item["stable_display_order"])
        for case in manifest["cases"] if "score_case" in case
    ]
    records = 0
    projection_seen = False
    for index, event in enumerate(events):
        if event.get("bindings") != bindings:
            raise RunBackendError("score journal bindings changed")
        kind = event["event_type"]
        if index == 0:
            continue
        if kind == "score_record":
            if projection_seen or records >= len(expected):
                raise RunBackendError("duplicate or late score record")
            record = event["record"]
            if (record.get("variant_id"), record.get("case_id"), record.get("trial"), record.get("cell_id"), record.get("variant_identity")) != expected[records]:
                raise RunBackendError("score record does not bind to the manifest cell")
            records += 1
        elif kind == "scorer_projection":
            if projection_seen or records != len(expected) or index != len(events) - 1:
                raise RunBackendError("illegal scorer projection transition")
            projection_seen = True
        else:
            raise RunBackendError("illegal score journal transition")


def _reference_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    _reject_sensitive(value)
    _canonical(value)
    as_of, source = value.get("as_of"), value.get("source")
    if (as_of is not None and type(as_of) is not str) or (source is not None and type(source) is not str):
        raise RunBackendError("reference public fields are invalid")
    projected = {"as_of": as_of, "source": source, "comparability": value.get("comparability", "comparable")}
    projected["hash"] = _digest(projected)
    return projected


def _expected_cells(manifest: Mapping[str, Any]) -> dict[str, tuple[str, bool, dict[str, str]]]:
    result = {}
    for variant in manifest["variants"]:
        for case in manifest["cases"]:
            cell_id = "cell-" + _digest([manifest["run_id"], variant["variant_id"], case["case_id"], 1])[:48]
            attempt_id = "attempt-" + _digest([manifest["run_id"], variant["variant_id"], case["case_id"], 1, "get"])[:48]
            result[cell_id] = (attempt_id, case["suite"] == "realtime_quote", _variant_identity(variant))
    return result


def _validate_journal(manifest: Mapping[str, Any], events: list[dict[str, Any]]) -> None:
    expected, state, started, finished = _expected_cells(manifest), {}, False, False
    for index, event in enumerate(events):
        previous = events[index - 1]["event_hash"] if index else None
        if event.get("previous_event_hash") != previous:
            raise RunBackendError("event hash chain mismatch")
        kind = event["event_type"]
        if kind == "run_started":
            if started or index != 0:
                raise RunBackendError("illegal run start transition")
            started = True
            continue
        if kind == "run_finished":
            references_complete = all(
                not reference_required or "before" not in state.get(cell_id, set()) or "after" in state[cell_id]
                for cell_id, (_attempt_id, reference_required, _identity) in expected.items()
            )
            if not started or finished or index != len(events) - 1 or len(state) != len(expected) or any("terminal" not in value for value in state.values()) or not references_complete:
                raise RunBackendError("illegal run finish transition")
            finished = True
            continue
        if not started or finished:
            raise RunBackendError("cell event outside active run")
        cell_id, attempt_id = event["cell_id"], event["attempt_id"]
        if cell_id not in expected or attempt_id != expected[cell_id][0]:
            raise RunBackendError("event does not bind to a manifest cell")
        entry = state.setdefault(cell_id, set())
        reference_required = expected[cell_id][1]
        expected_identity = expected[cell_id][2]
        if kind == "dispatch_intent":
            if event.get("variant_identity") != expected_identity:
                raise RunBackendError("dispatch identity does not bind to manifest variant")
            if "intent" in entry or "terminal" in entry:
                raise RunBackendError("duplicate or late dispatch intent")
            entry.add("intent")
        elif kind == "reference_before":
            if not reference_required or "before" in entry or "intent" in entry or "terminal" in entry:
                raise RunBackendError("illegal reference-before transition")
            entry.add("before")
        elif kind == "terminal":
            if event.get("variant_identity") != expected_identity:
                raise RunBackendError("terminal identity does not bind to manifest variant")
            evidence = event.get("execution_evidence")
            if event.get("transport_status") == "completed" and (not isinstance(evidence, Mapping) or {field: evidence.get(field) for field in _VARIANT_IDENTITY_FIELDS} != expected_identity):
                raise RunBackendError("terminal evidence does not bind to manifest variant")
            reference_unavailable = reference_required and event.get("transport_status") == "reference_unavailable" and event.get("error_class") in {"reference_before_unavailable", "reference_after_unavailable", "reference_contract_mismatch"}
            if "terminal" in entry or ("intent" not in entry and not reference_unavailable):
                raise RunBackendError("terminal lacks a dispatch intent")
            entry.add("terminal")
        elif kind in {"reference_after", "reference_after_unavailable"}:
            if not reference_required or "terminal" not in entry or "after" in entry or ("intent" not in entry and "before" not in entry):
                raise RunBackendError("illegal reference-after transition")
            entry.add("after")


class RunService:
    """Execute exactly one public GET call per variant/case/trial cell."""

    def __init__(self, store: RunStore, clients: Mapping[str, Any], reference_hook: Callable[[Mapping[str, Any], str], Mapping[str, Any] | None] | None = None, monotonic_ns: Callable[[], int] = time.monotonic_ns, wall_clock: Callable[[], Any] = time.time) -> None:
        self.store, self.clients, self.reference_hook = store, dict(clients), reference_hook
        self.monotonic_ns, self.wall_clock = monotonic_ns, wall_clock

    def create_run(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _validate_manifest(manifest)
        self._require_clients(normalized)
        self._require_single_reference_contract(normalized)
        normalized, manifest_hash = self.store.create(normalized)
        events = self.store.events(normalized["run_id"], manifest_hash=manifest_hash)
        if not events:
            self._append(normalized["run_id"], {"event_type": "run_started"})
        self._assert_manifest_binding(normalized["run_id"], manifest_hash)
        return self._snapshot(normalized["run_id"])

    def execute(self, run_id: str) -> dict[str, Any]:
        self._require_timeout_support()
        with self.store.locked(run_id):
            manifest = self.store.load_manifest(run_id)
            manifest_hash = _digest(manifest)
            self._assert_manifest_binding(run_id, manifest_hash)
            self._require_clients(manifest)
            self._require_single_reference_contract(manifest)
            events = self.store.events(run_id, manifest_hash=manifest_hash)
            if any(event["event_type"] == "run_finished" for event in events):
                snapshot = self._snapshot(run_id)
                self.store.write_snapshot(run_id, snapshot)
                return snapshot
            for variant in sorted(manifest["variants"], key=lambda item: item["stable_display_order"]):
                for case in manifest["cases"]:
                    self._execute_cell(manifest, variant, case)
            snapshot = self._snapshot(run_id)
            terminal_status = "incomplete" if snapshot["execution"]["incomplete"] else "execution_failed" if snapshot["execution"]["failed"] else "execution_complete"
            self._append(run_id, {"event_type": "run_finished", "status": terminal_status})
            snapshot = self._snapshot(run_id)
            self.store.write_snapshot(run_id, snapshot)
            return snapshot

    def get_snapshot(self, run_id: str) -> dict[str, Any]:
        return self._snapshot(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        _require_directory(self.store.root)
        result = []
        for path in sorted(self.store.root.iterdir()):
            try:
                _require_directory(path)
            except RunBackendError:
                continue
            try:
                snapshot = self.get_snapshot(path.name)
                result.append({key: snapshot[key] for key in ("schema_version", "run_id", "manifest_hash", "status", "snapshot_sequence", "event_cursor", "updated_at", "connection_basis", "projection_status", "projection_reason", "internal_status")})
            except RunBackendError:
                raise
        return result

    def get_events(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        if type(after_sequence) is not int or isinstance(after_sequence, bool) or after_sequence < 0:
            raise RunBackendError("after_sequence must be a non-negative integer")
        events = self.store.events(run_id)
        projection = self._valid_score_projection(run_id, events)
        if projection is not None:
            events = events + [{"event_type": "scorer_projection", "sequence": len(events) + 1, "run_id": run_id, "projection_status": projection["projection_status"]}]
        return [event for event in events if event["sequence"] > after_sequence]

    def _valid_score_projection(self, run_id: str, execution: list[dict[str, Any]]) -> dict[str, Any] | None:
        projection = self.store.load_score_projection(run_id)
        if projection is None or not execution or execution[-1].get("event_type") != "run_finished":
            return None
        score_events = self.store.score_events(run_id)
        manifest = self.store.load_manifest(run_id)
        contract = manifest.get("scoring_contract")
        bindings = projection.get("bindings")
        if (not score_events or score_events[-1].get("event_type") != "scorer_projection" or projection.get("score_tail_hash") != score_events[-1].get("score_event_hash") or projection.get("projection_hash") != score_events[-1].get("projection_hash") or type(contract) is not dict or bindings != score_events[-1].get("bindings") or bindings != {"execution_tail_hash": execution[-1]["event_hash"], "policy_digest": contract.get("policy_digest"), "oracle_bundle_digest": contract.get("oracle_bundle_digest"), "scorer_version": contract.get("scorer_version"), "scorer_digest": contract.get("scorer_digest"), "variant_contract_digest": contract.get("variant_contract_digest")} or projection.get("manifest_hash") != _digest(manifest)):
            return None
        return projection

    def _append(self, run_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        emitted_at = self.wall_clock()
        if type(emitted_at) not in (int, float) or isinstance(emitted_at, bool):
            raise RunBackendError("wall_clock must return a JSON number")
        sequence = len(self.store.events(run_id)) + 1
        record = dict(event)
        record["emitted_at"] = emitted_at
        record["event_id"] = "event-" + _digest([run_id, sequence, emitted_at])[:48]
        return self.store.append(run_id, record)

    def _require_clients(self, manifest: Mapping[str, Any]) -> None:
        for variant in manifest["variants"]:
            if variant["variant_id"] not in self.clients:
                raise RunBackendError("missing client for variant")

    def _require_single_reference_contract(self, manifest: Mapping[str, Any]) -> None:
        """Attribute-based hooks deliberately support one frozen contract per run."""
        if self.reference_hook is None:
            return
        contracts = {
            (case["reference_contract"]["source_contract_hash"], case["reference_contract"]["window_rule_version"])
            for case in manifest["cases"]
            if case["suite"] == "realtime_quote"
        }
        if len(contracts) > 1:
            raise RunBackendError("reference hook supports one reference contract per run")

    def _reference_contract_matches(self, case: Mapping[str, Any]) -> bool:
        """Read the hook identity immediately before every reference/GET boundary."""
        if self.reference_hook is None:
            return False
        try:
            source_contract_hash = self.reference_hook.source_contract_hash
            window_rule_version = self.reference_hook.window_rule_version
        except Exception:
            return False
        expected = case["reference_contract"]
        return (
            type(source_contract_hash) is str
            and _SHA256.fullmatch(source_contract_hash) is not None
            and type(window_rule_version) is str
            and _ID.fullmatch(window_rule_version) is not None
            and source_contract_hash == expected["source_contract_hash"]
            and window_rule_version == expected["window_rule_version"]
        )

    @staticmethod
    def _require_timeout_support() -> None:
        if threading.current_thread() is not threading.main_thread() or not all(hasattr(signal, name) for name in ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")):
            raise RunBackendError("outer timeout requires POSIX SIGALRM on the main thread")

    def _assert_manifest_binding(self, run_id: str, expected_hash: str) -> None:
        current = self.store.load_manifest(run_id)
        if _digest(current) != expected_hash:
            raise RunBackendError("manifest changed after run binding")
        events = self.store.events(run_id, manifest_hash=expected_hash)
        started = next((event for event in events if event["event_type"] == "run_started"), None)
        if started is None or started.get("manifest_hash") != expected_hash:
            raise RunBackendError("manifest is not durably bound to the run")

    def _execute_cell(self, manifest: Mapping[str, Any], variant: Mapping[str, Any], case: Mapping[str, Any]) -> None:
        variant_id, case_id = variant["variant_id"], case["case_id"]
        variant_identity = _variant_identity(variant)
        cell_id = "cell-" + _digest([manifest["run_id"], variant_id, case_id, 1])[:48]
        attempt_id = "attempt-" + _digest([manifest["run_id"], variant_id, case_id, 1, "get"])[:48]
        events = self.store.events(manifest["run_id"])
        cell_events = [event for event in events if event.get("cell_id") == cell_id]
        terminal = any(event["event_type"] == "terminal" for event in cell_events)
        reference_after = any(event["event_type"] in {"reference_after", "reference_after_unavailable"} for event in cell_events)
        reference_required = case["suite"] == "realtime_quote"
        if terminal:
            if reference_required and not reference_after:
                self._append(manifest["run_id"], {"event_type": "reference_after_unavailable", "cell_id": cell_id, "attempt_id": attempt_id})
            return
        if any(event["event_type"] == "dispatch_intent" for event in cell_events):
            self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, 0, "uncertain", "recovery_uncertain", None, "unknown", "not_comparable", variant_identity=variant_identity))
            if reference_required:
                self._append(manifest["run_id"], {"event_type": "reference_after_unavailable", "cell_id": cell_id, "attempt_id": attempt_id})
            return
        comparable = "not_applicable"
        if reference_required:
            comparable = "not_comparable"
            before_event = next((event for event in cell_events if event["event_type"] == "reference_before"), None)
            if self.reference_hook is not None and not self._reference_contract_matches(case):
                self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, 0, "reference_unavailable", "reference_contract_mismatch", None, "unknown", "not_comparable", variant_identity=variant_identity))
                if before_event is not None:
                    self._append(manifest["run_id"], {"event_type": "reference_after_unavailable", "cell_id": cell_id, "attempt_id": attempt_id})
                return
            if before_event is not None:
                reference = before_event.get("reference")
                comparable = reference.get("comparability", "not_comparable") if isinstance(reference, Mapping) else "not_comparable"
                if self.reference_hook is None:
                    self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, 0, "reference_unavailable", "reference_after_unavailable", None, "unknown", "not_comparable", variant_identity=variant_identity))
                    self._append(manifest["run_id"], {"event_type": "reference_after_unavailable", "cell_id": cell_id, "attempt_id": attempt_id})
                    return
            elif self.reference_hook is not None:
                try:
                    before = self._reference(case, "before")
                except Exception:
                    self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, 0, "reference_unavailable", "reference_before_unavailable", None, "unknown", "not_comparable", variant_identity=variant_identity))
                    return
                self._append(manifest["run_id"], {"event_type": "reference_before", "cell_id": cell_id, "attempt_id": attempt_id, "reference": before})
                comparable = before["comparability"]
            else:
                self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, 0, "reference_unavailable", "reference_before_unavailable", None, "unknown", "not_comparable", variant_identity=variant_identity))
                return
        if reference_required and self.reference_hook is not None and not self._reference_contract_matches(case):
            self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, 0, "reference_unavailable", "reference_contract_mismatch", None, "unknown", "not_comparable", variant_identity=variant_identity))
            self._append(manifest["run_id"], {"event_type": "reference_after_unavailable", "cell_id": cell_id, "attempt_id": attempt_id})
            return
        self._assert_manifest_binding(manifest["run_id"], _digest(manifest))
        request_id = attempt_id
        idempotency_key = "idem-" + attempt_id[8:]
        self._append(manifest["run_id"], {
            "event_type": "dispatch_intent", "cell_id": cell_id, "attempt_id": attempt_id,
            "variant_id": variant_id, "case_id": case_id, "trial": 1,
            "variant_identity": variant_identity,
            "input_hash": _digest({"query": case["query"]}),
            "request_hash": _digest({"query": case["query"], "request_id": request_id, "idempotency_key": idempotency_key}),
        })
        started = self.monotonic_ns()
        result: Any = None
        transport, error = "completed", None
        call_completed = False
        try:
            result = self._call_with_timeout(self.clients[variant_id], case["query"], request_id, idempotency_key, manifest["timeout_ms"])
            call_completed = True
        except TimeoutError:
            transport, error = "timeout", "timeout"
        except Exception:
            transport, error = "failed", "client_exception"
        elapsed_ms = (self.monotonic_ns() - started) / 1_000_000
        evidence: dict[str, Any] | None = None
        if call_completed:
            try:
                projected, usage, evidence = self._project_result(result, variant)
            except RunBackendError:
                projected, usage, transport, error = {"schema_version": "qveris-get-response/v1", "status": "invalid_public_response"}, "unknown", "failed", "runtime_evidence_invalid"
        else:
            projected, usage = None, "unknown"
        result_status = projected.get("status") if projected is not None else None
        if transport == "completed" and result_status != "success":
            error = "invalid_public_response" if result_status == "invalid_public_response" else "get_%s" % (result_status or "missing")
        if error == "runtime_evidence_invalid":
            result_status = "runtime_evidence_invalid"
        self._append(manifest["run_id"], self._terminal(cell_id, attempt_id, elapsed_ms, transport, error, projected, usage, comparable, variant_identity=variant_identity, execution_evidence=evidence, usage_source="public_meta_usage" if usage != "unknown" else "unknown", call_completed=call_completed, result_status=result_status))
        if reference_required and self.reference_hook is not None:
            try:
                after = self._reference(case, "after")
                self._append(manifest["run_id"], {"event_type": "reference_after", "cell_id": cell_id, "attempt_id": attempt_id, "reference": after})
            except Exception:
                self._append(manifest["run_id"], {"event_type": "reference_after_unavailable", "cell_id": cell_id, "attempt_id": attempt_id})

    @staticmethod
    def _call(client: Any, query: str, request_id: str, idempotency_key: str) -> Any:
        target = client if callable(client) else getattr(client, "run", None) or getattr(client, "get", None)
        if not callable(target):
            raise RunBackendError("variant client must be callable or expose run/get")
        return target(query, request_id=request_id, idempotency_key=idempotency_key)

    @classmethod
    def _call_with_timeout(cls, client: Any, query: str, request_id: str, idempotency_key: str, timeout_ms: int) -> Any:
        cls._require_timeout_support()
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
        previous_handler = signal.getsignal(signal.SIGALRM)
        started = time.monotonic()

        def expired(_signum: int, _frame: Any) -> None:
            raise TimeoutError("outer timeout expired")

        try:
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1000)
            return cls._call(client, query, request_id, idempotency_key)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, max(previous_timer[0] - (time.monotonic() - started), 0.000001), previous_timer[1])

    def _reference(self, case: Mapping[str, Any], phase: str) -> dict[str, Any]:
        if not self._reference_contract_matches(case):
            raise RunBackendError("reference contract mismatch")
        value = self.reference_hook(case, phase) if self.reference_hook is not None else None
        if type(value) is not dict:
            raise RunBackendError("reference is unavailable")
        return _reference_projection(value)

    @staticmethod
    def _terminal(cell_id: str, attempt_id: str, elapsed_ms: float, transport: str, error: str | None, response: Mapping[str, Any] | None, usage: Mapping[str, Any] | str, comparability: str, *, variant_identity: Mapping[str, Any], execution_evidence: Mapping[str, Any] | None = None, usage_source: str = "unknown", call_completed: bool = False, result_status: str | None = None) -> dict[str, Any]:
        record: dict[str, Any] = {"event_type": "terminal", "cell_id": cell_id, "attempt_id": attempt_id, "elapsed_ms": round(elapsed_ms, 3), "transport_status": transport, "transport_completed": call_completed, "call_completed": call_completed, "execution_outcome": result_status, "result_status": result_status, "usage": usage, "usage_source": usage_source, "comparability": comparability, "response_hash": None, "variant_identity": dict(variant_identity)}
        if error is not None:
            record["error_class"] = error
        if response is not None:
            record["public_response"] = response
            record["response_hash"] = _digest(response)
        if execution_evidence is not None:
            record["execution_evidence"] = dict(execution_evidence)
        return record

    @staticmethod
    def _project_result(result: Any, variant: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any] | str, dict[str, Any]]:
        if type(result) is not PublicGetResult:
            raise RunBackendError("GET adapter must return PublicGetResult, not a bare response")
        evidence = _evidence_projection(result.execution_evidence, variant)
        response, usage = RunService._project_response(result.public_response)
        return response, usage, evidence

    @staticmethod
    def _project_response(response: Any) -> tuple[dict[str, Any] | None, Mapping[str, Any] | str]:
        if response is None:
            return None, "unknown"
        if isinstance(response, Mapping):
            try:
                _reject_sensitive({key: value for key, value in response.items() if key not in {"usage", "meta"}})
                if not set(response).issubset(_GET_RESPONSE_FIELDS) or type(response.get("status")) is not str or response["status"] not in _CANONICAL_RESPONSE_STATUSES:
                    raise RunBackendError("invalid public GET response")
                projected = {key: response[key] for key in ("schema_version", "status", "resolved_request", "data", "as_of", "source", "clarification", "terminal_reason", "meta") if key in response}
                for field in ("data", "resolved_request"):
                    if field in projected:
                        RunService._validate_public_data(projected[field])
                for field in ("schema_version", "as_of"):
                    if field in projected and projected[field] is not None and type(projected[field]) is not str:
                        raise RunBackendError("invalid public GET response")
                if "source" in projected and (type(projected["source"]) is not str or not projected["source"]):
                    raise RunBackendError("invalid public GET response")
                for field in ("clarification", "terminal_reason"):
                    if field in projected and type(projected[field]) is not str:
                        raise RunBackendError("invalid public GET response")
                if "meta" in response and (not isinstance(response["meta"], Mapping) or set(response["meta"]) != {"usage"}):
                    raise RunBackendError("invalid public GET response")
            except RunBackendError:
                projected = {"schema_version": "qveris-get-response/v1", "status": "invalid_public_response"}
            meta = response.get("meta")
            usage_raw = meta.get("usage") if isinstance(meta, Mapping) and set(meta) == {"usage"} else None
        else:
            return {"schema_version": "qveris-get-response/v1", "status": "invalid_public_response"}, "unknown"
        try:
            _reject_sensitive({key: value for key, value in projected.items() if key != "meta"})
            if "data" in projected:
                RunService._validate_public_data(projected["data"])
            _canonical(projected)
        except RunBackendError:
            projected = {"schema_version": "qveris-get-response/v1", "status": "invalid_public_response"}
        usage = {}
        if isinstance(usage_raw, Mapping) and set(usage_raw).issubset(_USAGE):
            for key, value in usage_raw.items():
                if key in _USAGE_TOKENS and type(value) is int and not isinstance(value, bool) and value >= 0:
                    usage[key] = value
                elif key in _USAGE_AUDIT and type(value) is str and value:
                    usage[key] = value
                else:
                    usage = {}
                    break
        return projected, usage or "unknown"

    @staticmethod
    def _validate_public_data(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = key.lower().replace("-", "_") if type(key) is str else ""
                compact = "".join(char for char in normalized if char.isalnum())
                if type(key) is not str or normalized in _GET_DATA_FORBIDDEN or compact in {name.replace("_", "") for name in _GET_DATA_FORBIDDEN}:
                    raise RunBackendError("raw provider data is not public")
                RunService._validate_public_data(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                RunService._validate_public_data(child)
        else:
            _canonical(value)

    def _snapshot(self, run_id: str) -> dict[str, Any]:
        manifest = self.store.load_manifest(run_id)
        events = self.store.events(run_id)
        projection = self._valid_score_projection(run_id, events)
        by_cell = {event["cell_id"]: event for event in events if event["event_type"] == "terminal"}
        after_failures = {event["cell_id"] for event in events if event["event_type"] == "reference_after_unavailable"}
        cells: list[dict[str, Any]] = []
        variants: list[dict[str, Any]] = []
        success = failed = incomplete = blocked = 0
        for variant in sorted(manifest["variants"], key=lambda item: item["stable_display_order"]):
            suites = {suite: {"completed": 0, "total": 0, "success": 0, "failed": 0, "incomplete": 0, "blocked": 0} for suite in _SUITES}
            for case in manifest["cases"]:
                cell_id = "cell-" + _digest([run_id, variant["variant_id"], case["case_id"], 1])[:48]
                suites[case["suite"]]["total"] += 1
                terminal = by_cell.get(cell_id)
                state = "queued"
                if terminal:
                    suites[case["suite"]]["completed"] += 1
                    outcome = terminal.get("execution_outcome", terminal.get("result_status"))
                    state = "incomplete" if cell_id in after_failures or terminal["transport_status"] in {"uncertain", "reference_unavailable"} else _EXECUTION_OUTCOME_STATES.get(outcome, "failed")
                    suites[case["suite"]][state] += 1
                    if state == "success": success += 1
                    elif state == "failed": failed += 1
                    elif state == "incomplete": incomplete += 1
                    else: blocked += 1
                cells.append({"variant_id": variant["variant_id"], "case_id": case["case_id"], "trial": 1, "state": state})
            variants.append({"variant_id": variant["variant_id"], "stable_display_order": variant["stable_display_order"], "suites": suites})
        total = len(manifest["variants"]) * len(manifest["cases"])
        finished = next((event.get("status") for event in reversed(events) if event["event_type"] == "run_finished"), None)
        internal_status = finished or ("running" if any(event["event_type"] == "run_started" for event in events) else "queued")
        public_status = "queued" if internal_status == "queued" else "running" if internal_status == "running" else "completed" if projection and projection.get("projection_status") == "SCORED" and projection.get("ranked_results") else "incomplete"
        event_cursor = (events[-1]["sequence"] if events else 0) + (1 if projection else 0)
        updated_at = events[-1].get("emitted_at") if events else None
        if projection:
            score_variants = {item["variant_id"]: item for item in projection.get("variants", []) if type(item) is dict and type(item.get("variant_id")) is str}
            for variant in variants:
                scored = score_variants.get(variant["variant_id"])
                if scored:
                    variant.update({key: value for key, value in scored.items() if key not in {"variant_id", "stable_display_order"}})
            scoring = {"semantic_accuracy": "SCORED", "data_accuracy": "SCORED", "e2e_latency": "SCORED", "token_usage": "SCORED", "coverage": "SCORED", "rank": "SCORED" if projection.get("projection_status") == "SCORED" else None, "eligibility": "SCORED" if projection.get("projection_status") == "SCORED" else None}
            projection_status, projection_reason = projection["projection_status"], "score_projection_available"
        else:
            scoring = {"semantic_accuracy": "UNSCORED", "data_accuracy": "UNSCORED", "e2e_latency": "UNSCORED", "token_usage": "UNSCORED", "coverage": None, "rank": None, "eligibility": None}
            projection_status, projection_reason = "UNSCORED", "scorer_projection_unavailable"
        return {"schema_version": "qveris-run-snapshot/v1", "run_id": run_id, "manifest_hash": _digest(manifest), "status": public_status, "internal_status": internal_status, "projection_status": projection_status, "projection_reason": projection_reason, "snapshot_sequence": event_cursor, "event_cursor": event_cursor, "updated_at": updated_at, "connection_basis": "durable_event_journal", "variants": variants, "cells": cells, "execution": {"total": total, "completed": success + failed + incomplete + blocked, "success": success, "failed": failed, "incomplete": incomplete, "blocked": blocked}, "scoring": scoring}

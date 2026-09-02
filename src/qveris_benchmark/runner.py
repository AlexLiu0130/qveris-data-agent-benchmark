"""Offline, one-plan/one-tool benchmark replay runner."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .agent import SemanticAgent, _stdlib_post
from .connector import CallOutcome, Connector, FakeReplayTransport
from .contracts import Domain, PlanStatus
from .manifest import PlanManifestMismatch, UnknownToolAlias
from .scoring import METRIC_DEFINITIONS, derive_token_usage, match_data, score_data_accuracy, score_semantics
from .strict_json import StrictJSONError


class Outcome(str, Enum):
    NOT_SCORED_ORACLE = "not_scored_oracle"
    SEMANTIC_ERROR = "semantic_error"
    VALIDATOR_CONNECTOR_ERROR = "validator_connector_error"
    PROVIDER_ERROR = "provider_error"
    DATA_MISMATCH = "data_mismatch"
    SUCCESS = "success"


class RunMode(str, Enum):
    REPLAY_FIXTURE_SELF_CHECK = "replay_fixture_self_check"
    MODEL_LIVE_REPLAY_DATA = "model_live_replay_data"


_REFERENCE_KINDS = frozenset({"independent_source", "same_source_snapshot", "replay_fixture"})
_CASE_FIELDS = frozenset({"case_id", "family_id", "suite", "query", "expected_status", "expected_semantics", "expected_tool_alias", "expected_arguments", "oracle_ref", "comparison_rule"})


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    family_id: str
    suite: str
    query: str
    expected_status: str
    expected_semantics: Mapping[str, Any]
    expected_tool_alias: str | None
    expected_arguments: Mapping[str, Any]
    oracle_ref: str
    comparison_rule: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BenchmarkCase":
        if type(raw) is not dict or set(raw) != _CASE_FIELDS:
            raise ValueError("case must contain exactly the benchmark case schema fields")
        strings = ("case_id", "family_id", "suite", "query", "expected_status", "oracle_ref")
        if any(type(raw[name]) is not str or not raw[name] for name in strings):
            raise ValueError("case string fields must be non-empty strings")
        try:
            Domain(raw["suite"])
            PlanStatus(raw["expected_status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("suite or expected_status is invalid") from exc
        if type(raw["expected_semantics"]) is not dict or type(raw["expected_arguments"]) is not dict:
            raise ValueError("expected semantics and arguments must be objects")
        if raw["expected_tool_alias"] is not None and (type(raw["expected_tool_alias"]) is not str or not raw["expected_tool_alias"]):
            raise ValueError("expected_tool_alias must be a non-empty string or null")
        if type(raw["comparison_rule"]) is not dict:
            raise ValueError("comparison_rule must be an object")
        return cls(**raw)


def load_cases(path: str | Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    cases.append(BenchmarkCase.from_mapping(json.loads(line)))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError("invalid case JSON at line %d" % line_number) from exc
    return cases


def load_oracle(base_dir: str | Path, oracle_ref: str) -> Mapping[str, Any] | None:
    """Load a local oracle without allowing absolute paths or symlink escapes."""
    reference = Path(oracle_ref)
    if reference.is_absolute():
        return None
    try:
        base = Path(base_dir).resolve(strict=True)
        target = (base / reference).resolve(strict=True)
        if not target.is_relative_to(base):
            return None
        with target.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if (
        type(value) is not dict
        or type(value.get("response")) is not dict
        or type(value.get("expected")) is not dict
        or value.get("reference_kind") not in _REFERENCE_KINDS
        or (value.get("reference_kind") == "replay_fixture" and value.get("synthetic") is not True)
        or not _domain_value(value.get("domain"))
    ):
        return None
    return value


def append_result(path: str | Path, record: Mapping[str, Any]) -> None:
    """Append one durable, locked JSONL record; write failures propagate."""
    encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError("short results.jsonl write")
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class BenchmarkRunner:
    """Run a case using a fake connector; live model semantics need an explicit mode."""

    def __init__(self, agent: SemanticAgent, connector: Connector, *, mode: RunMode | str = RunMode.REPLAY_FIXTURE_SELF_CHECK) -> None:
        try:
            resolved_mode = RunMode(mode)
        except ValueError as exc:
            raise ValueError("unknown runner mode") from exc
        if not isinstance(connector._transport, FakeReplayTransport):  # noqa: SLF001 - hard replay boundary
            raise ValueError("runner requires FakeReplayTransport")
        if agent._transport is _stdlib_post and resolved_mode is not RunMode.MODEL_LIVE_REPLAY_DATA:  # noqa: SLF001 - explicit model network mode
            raise ValueError("live model replay requires mode=model_live_replay_data")
        self._agent = agent
        self._connector = connector
        self._mode = resolved_mode

    def run_case(self, case: BenchmarkCase, oracle: Mapping[str, Any] | None) -> dict[str, Any]:
        started = time.monotonic_ns()
        calls_before = len(self._connector._transport.calls)  # noqa: SLF001 - fake replay receipt count
        record = self._record_base(case, oracle)
        if self._preflight(case, oracle):
            return self._finish(record, Outcome.NOT_SCORED_ORACLE, started, calls_before, self_check="not_run")

        agent_started = time.monotonic_ns()
        try:
            agent_result = self._agent.plan(case.query, self._connector._manifest)  # noqa: SLF001 - immutable connector manifest
        except (StrictJSONError, UnknownToolAlias, PlanManifestMismatch):
            record["agent_call_ms"] = _elapsed_ms(agent_started)
            return self._finish(record, Outcome.SEMANTIC_ERROR, started, calls_before, self_check="not_run")
        except Exception:
            record["agent_call_ms"] = _elapsed_ms(agent_started)
            return self._finish(record, Outcome.PROVIDER_ERROR, started, calls_before, self_check="not_run")

        record["agent_call_ms"] = _elapsed_ms(agent_started)
        record["agent_usage_receipt"] = None if agent_result.raw_usage is None else dict(agent_result.raw_usage)
        record["metrics"]["token_usage"] = derive_token_usage(record["agent_usage_receipt"])
        record["validated_plan"] = _plan_record(agent_result.plan)
        gate_started = time.monotonic_ns()
        semantic = score_semantics(
            agent_result.plan,
            expected_status=case.expected_status,
            expected_semantics=case.expected_semantics,
            expected_tool_alias=case.expected_tool_alias,
            expected_arguments=case.expected_arguments,
        )
        record["metrics"]["semantic_exact"] = semantic.exact
        record["plan_gate_ms"] = _elapsed_ms(gate_started)
        if not semantic.exact:
            return self._finish(record, Outcome.SEMANTIC_ERROR, started, calls_before, self_check="not_run")
        if agent_result.plan.status is not PlanStatus.READY:
            return self._finish(record, Outcome.SUCCESS, started, calls_before, self_check="pass")

        try:
            connector_started = time.monotonic_ns()
            connector_result = self._connector.execute(agent_result.plan, idempotency_key=case.case_id)
        except Exception:
            record["connector_ms"] = _elapsed_ms(connector_started)
            record["connector_outcome"] = "validator_error"
            return self._finish(record, Outcome.VALIDATOR_CONNECTOR_ERROR, started, calls_before, self_check="not_run")

        record["connector_ms"] = _elapsed_ms(connector_started)
        record["connector_outcome"] = connector_result.outcome.value
        record["idempotency_key"] = connector_result.metadata.idempotency_key
        record.update(_receipt_fields(connector_result.payload))
        if connector_result.outcome is not CallOutcome.SUCCESS:
            outcome = _connector_failure_outcome(connector_result.outcome, connector_result.metadata.http_status, connector_result.reason)
            return self._finish(record, outcome, started, calls_before, self_check="not_run")

        try:
            matches = match_data(connector_result.payload or {}, oracle["expected"], case.comparison_rule)  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return self._finish(record, Outcome.NOT_SCORED_ORACLE, started, calls_before, self_check="failed")
        record["fixture_response_match"] = matches
        return self._finish(record, Outcome.NOT_SCORED_ORACLE, started, calls_before, self_check="pass" if matches else "failed")

    def run_cases(self, cases: Iterable[BenchmarkCase], oracle_loader: Callable[[BenchmarkCase], Mapping[str, Any] | None], results_path: str | Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for case in cases:
            record = self.run_case(case, oracle_loader(case))
            append_result(results_path, record)
            records.append(record)
        return records

    def _preflight(self, case: BenchmarkCase, oracle: Mapping[str, Any] | None) -> bool:
        if case.expected_status != PlanStatus.READY.value:
            return case.expected_tool_alias is not None or bool(case.expected_arguments)
        if (
            oracle is None
            or oracle.get("reference_kind") not in _REFERENCE_KINDS
            or (oracle.get("reference_kind") == "replay_fixture" and oracle.get("synthetic") is not True)
            or oracle.get("domain") != case.suite
            or type(oracle.get("response")) is not dict
            or type(oracle.get("expected")) is not dict
        ):
            return True
        if case.expected_semantics.get("domain") != case.suite or case.expected_tool_alias is None:
            return True
        try:
            entry = self._connector._manifest.resolve(case.expected_tool_alias)  # noqa: SLF001 - package gate
        except UnknownToolAlias:
            return True
        return entry.domain.value != case.suite

    def _record_base(self, case: BenchmarkCase, oracle: Mapping[str, Any] | None) -> dict[str, Any]:
        profile = self._agent._profile  # noqa: SLF001 - runtime profile is evaluation provenance
        return {
            "mode": self._mode.value,
            "model_profile": {"model_id": profile.model_id, "reasoning_effort": profile.reasoning_effort, "api_base": profile.api_base},
            "case_id": case.case_id,
            "family_id": case.family_id,
            "suite": case.suite,
            "case_sha256": _hash(asdict(case)),
            "manifest_sha256": _hash(_manifest_record(self._connector._manifest)),  # noqa: SLF001
            "oracle_sha256": None if oracle is None else _hash(oracle),
            "validated_plan": None,
            "connector_call_count": 0,
            "connector_outcome": "not_called",
            "fixture_response_match": None,
            "idempotency_key": None,
            "receipt": None,
            "provenance": None,
            "as_of": None,
            "cost": None,
            "agent_usage_receipt": None,
            "metrics": {"semantic_exact": None, "data_accuracy": score_data_accuracy(False, comparable=False), "token_usage": derive_token_usage(None), "definitions": METRIC_DEFINITIONS},
            "agent_call_ms": None,
            "plan_gate_ms": None,
            "connector_ms": None,
            "e2e_ms": None,
        }

    def _finish(self, record: dict[str, Any], outcome: Outcome, started: int, calls_before: int, *, self_check: str) -> dict[str, Any]:
        record["outcome"] = outcome.value
        record["connector_call_count"] = len(self._connector._transport.calls) - calls_before  # noqa: SLF001
        record["e2e_ms"] = _elapsed_ms(started)
        record["self_check"] = self_check
        return record


def _domain_value(value: Any) -> bool:
    try:
        Domain(value)
    except (TypeError, ValueError):
        return False
    return True


def _plan_record(plan: Any) -> dict[str, Any]:
    record = {"status": plan.status.value}
    if plan.status is PlanStatus.READY:
        record.update({"domain": plan.domain.value if plan.domain else None, "tool_alias": plan.tool_alias, "request": dict(plan.request or {})})
    else:
        record["message"] = plan.message
    return record


def _receipt_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    value = payload or {}
    return {"receipt": value.get("receipt"), "provenance": value.get("provenance"), "as_of": value.get("as_of", value.get("asOf")), "cost": value.get("cost")}


def _connector_failure_outcome(connector_outcome: CallOutcome, http_status: int | None, reason: str | None) -> Outcome:
    if connector_outcome in (CallOutcome.BLOCKED, CallOutcome.EMPTY):
        return Outcome.PROVIDER_ERROR
    if connector_outcome is CallOutcome.FAILED and not (reason or "").startswith("response schema validation failed"):
        return Outcome.PROVIDER_ERROR
    return Outcome.VALIDATOR_CONNECTOR_ERROR


def _manifest_record(manifest: Any) -> dict[str, Any]:
    return {alias: {"tool_id": entry.tool_id, "request_schema": dict(entry.request_schema), "response_schema": dict(entry.response_schema), "domain": entry.domain.value, "auth_mode": entry.auth_mode.value} for alias, entry in manifest.entries.items()}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _elapsed_ms(started: int) -> float:
    return round((time.monotonic_ns() - started) / 1_000_000, 3)

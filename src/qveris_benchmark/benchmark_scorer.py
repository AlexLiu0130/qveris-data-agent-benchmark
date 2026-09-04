"""Deterministic scorer for the immutable Runner evidence journal."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from math import ceil
from pathlib import Path
import re
import time
from typing import Any

from .run_backend import RunBackendError, RunStore, _digest, _safe_id, _score_projection_hash, _variant_contract_digest, _variant_identity


_PATH = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*)(?:\[[0-9]+\])?(?:\.(?:[A-Za-z_][A-Za-z0-9_]*)(?:\[[0-9]+\])?)*$")
_METRICS = ("semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage")
_LEGACY_METRICS = ("semantic_accuracy", "data_accuracy", "token_usage", "e2e_latency")
_GATES = ("schema_valid", "status_correct", "semantic_pass", "data_pass", "execution_complete")
_RANK_KEYS = ("case_pass_rate", "data_accuracy", "semantic_accuracy", "end_to_end_latency_p95_ms", "average_total_tokens")
_LEGACY_RANK_KEYS = ("case_pass_rate", "data_accuracy", "semantic_accuracy", "e2e_p95_ms", "average_total_tokens")
_RANK_DIRECTIONS = ("desc", "desc", "desc", "asc", "asc")
_RECEIPT_FIELDS = ("receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens")
_RESPONSE_STATUSES = frozenset({"success", "partial", "needs_clarification", "unsupported", "no_data", "error"})
SCORER_VERSION = "qveris-benchmark-scorer/v2"
SCORER_DIGEST = __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest()
_POLICY_KEYS = frozenset({"schema_version", "metric_names", "percentile_method", "assertion_operators", "operator_registry", "case_pass_gate", "completeness", "response_schema_version", "response_status_contracts", "max_reference_window_seconds", "error", "timeout_latency_treatment", "usage_receipt_required_fields", "trusted_receipt_issuers", "eligibility", "ranking"})
_ORACLE_KEYS = frozenset({"schema_version", "oracles"})
_ORACLE_V2_ROOT_KEYS = _ORACLE_KEYS | frozenset({"freeze_digest", "compiler_module_sha256", "compiled_oracle_content_digest", "policy_digest", "source_manifest_hashes", "source_hashes", "expected_status_counts"})
_ORACLE_ITEM_KEYS = frozenset({"oracle_id", "case_id", "independence", "semantic_assertions", "data_assertions", "state_assertions", "reference_evidence", "source_ref", "version", "semantic_review_status", "data_review_status", "state_review_status"})
_OPTIONAL_ORACLE_ITEM_KEYS = frozenset({"alternative_assertion_sets"})
_ORACLE_V2_ITEM_KEYS = _ORACLE_ITEM_KEYS | _OPTIONAL_ORACLE_ITEM_KEYS | frozenset({"suite", "expected_status", "runtime_contract", "data_not_scored_until_receipt", "source_case_id", "source_oracle_id"})
_ASSERTION_KEYS = frozenset({"path", "operator", "expected", "tolerance", "weight", "fatal"})
_STATUS_CONTRACTS = {
    "success": {"required_non_null_paths": ("resolved_request", "data", "as_of", "source"), "required_null_paths": ("clarification", "terminal_reason")},
    "partial": {"required_non_null_paths": ("resolved_request", "data", "as_of", "source"), "required_null_paths": ("clarification", "terminal_reason")},
    "needs_clarification": {"required_non_null_paths": ("clarification",), "required_null_paths": ("data", "terminal_reason")},
    "unsupported": {"required_non_null_paths": ("terminal_reason",), "required_null_paths": ("data", "clarification")},
    "no_data": {"required_non_null_paths": ("terminal_reason",), "required_null_paths": ("data", "clarification")},
    "error": {"required_non_null_paths": ("terminal_reason",), "required_null_paths": ("data", "clarification")},
}


class BenchmarkScoreError(ValueError):
    """A scoring contract, evidence, or score ledger was rejected."""


def _fail(message: str) -> None:
    raise BenchmarkScoreError(message)


def _decimal(value: Any, field: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _fail("%s must be a decimal number" % field)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BenchmarkScoreError("%s must be a decimal number" % field) from exc
    if not result.is_finite() or (nonnegative and result < 0):
        _fail("%s has an invalid value" % field)
    return result


def _number(value: Decimal | int | float) -> float | str:
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if decimal and not Decimal("1e-323") <= abs(decimal) <= Decimal("1e308"):
        return str(decimal)
    floating = float(decimal)
    return format(decimal, "f") if decimal and floating == 0 else floating


def _ratio(numerator: Decimal | int, denominator: Decimal | int) -> float | str | None:
    return None if not denominator else _number(Decimal(numerator) / Decimal(denominator))


def _safe_path(path: Any, prefix: str) -> str:
    if type(path) is not str or _PATH.fullmatch(path) is None or not (path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")):
        _fail("assertion path is unsafe or outside %s" % prefix)
    return path


def _path_value(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split("."):
        name, bracket, index = part.partition("[")
        if type(current) is not dict or name not in current:
            return False, None
        current = current[name]
        if bracket:
            if not isinstance(current, list) or int(index[:-1]) >= len(current):
                return False, None
            current = current[int(index[:-1])]
    return True, current


def _assertion_pass(actual: Any, assertion: Mapping[str, Any]) -> bool:
    if assertion["operator"] == "exact":
        return type(actual) is type(assertion["expected"]) and actual == assertion["expected"]
    if isinstance(actual, bool) or isinstance(assertion["expected"], bool):
        return False
    try:
        return abs(_decimal(actual, "actual") - _decimal(assertion["expected"], "expected")) <= _decimal(assertion["tolerance"], "tolerance", nonnegative=True)
    except BenchmarkScoreError:
        return False


def _oracle_assertion_sets(oracle: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return mutually exclusive, source-coherent assertion sets."""
    base = {name: oracle[name] for name in ("semantic_assertions", "data_assertions", "state_assertions")}
    alternatives = oracle.get("alternative_assertion_sets")
    if alternatives is None:
        return [base]
    return ([base] if any(base.values()) else []) + alternatives


def _validate_assertions(assertions: Any, *, kind: str, case_type: str, expected_status: list[str]) -> None:
    prefix = "data" if kind == "data_assertions" else "resolved_request" if case_type == "normal" else "clarification"
    if type(assertions) is not list:
        _fail("oracle assertions must be arrays")
    for assertion in assertions:
        if type(assertion) is not dict or set(assertion) != _ASSERTION_KEYS:
            _fail("atomic assertion has an invalid schema")
        path = assertion.get("path")
        if kind == "semantic_assertions" and case_type == "normal" and path == "status" and set(expected_status) == {"success"}:
            _safe_path(path, "status")
        elif kind == "semantic_assertions" and case_type == "boundary":
            expected_statuses = set(expected_status)
            if isinstance(path, str) and path.startswith("clarification") and expected_statuses == {"needs_clarification"}:
                _safe_path(path, "clarification")
            elif isinstance(path, str) and path.startswith("terminal_reason") and expected_statuses <= {"unsupported", "no_data"}:
                _safe_path(path, "terminal_reason")
            else:
                _fail("boundary semantic assertion path does not match expected status")
        else:
            state_prefix = "clarification" if kind == "state_assertions" and isinstance(path, str) and path.startswith("clarification") else "terminal_reason" if kind == "state_assertions" and isinstance(path, str) and path.startswith("terminal_reason") else "status" if kind == "state_assertions" else prefix
            _safe_path(path, state_prefix)
        if assertion.get("operator") not in {"exact", "within_abs"} or type(assertion.get("fatal")) is not bool or _decimal(assertion.get("weight"), "assertion.weight", nonnegative=True) <= 0:
            _fail("atomic assertion is invalid")
        if assertion["operator"] == "within_abs":
            _decimal(assertion.get("expected"), "assertion.expected")
            _decimal(assertion.get("tolerance"), "assertion.tolerance", nonnegative=True)
        elif assertion.get("tolerance") is not None:
            _fail("exact assertion tolerance must be null")


def _validate_policy(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _POLICY_KEYS:
        _fail("scoring policy has an invalid schema")
    if type(value["schema_version"]) is not str or not value["schema_version"] or tuple(value["metric_names"]) not in {_METRICS, _LEGACY_METRICS}:
        _fail("scoring policy schema or metrics are invalid")
    if value["percentile_method"] not in {"nearest_rank", "linear"}:
        _fail("unsupported percentile method")
    if type(value["assertion_operators"]) is not list or set(value["assertion_operators"]) != {"exact", "within_abs"} or value["operator_registry"] != value["assertion_operators"]:
        _fail("assertion operator registry is invalid")
    if tuple(value["case_pass_gate"]) != _GATES or type(value["completeness"]) is not dict:
        _fail("case pass or completeness policy is invalid")
    contracts = value["response_status_contracts"]
    if type(value["response_schema_version"]) is not str or not value["response_schema_version"] or type(contracts) is not dict or set(contracts) != _RESPONSE_STATUSES:
        _fail("response policy is invalid")
    for status, expected in _STATUS_CONTRACTS.items():
        contract = contracts.get(status)
        if type(contract) is not dict or set(contract) != {"required_non_null_paths", "required_null_paths"} or any(type(contract[name]) is not list or tuple(contract[name]) != expected[name] for name in expected):
            _fail("response status contracts are invalid")
    _decimal(value["max_reference_window_seconds"], "max_reference_window_seconds", nonnegative=True)
    required = value["usage_receipt_required_fields"]
    if value["error"] != "disabled" or value["timeout_latency_treatment"] not in {"observed", "cap_at_timeout"} or type(required) is not list or len(required) != len(set(required)) or any(type(item) is not str or not item for item in required) or not set(required) >= set(_RECEIPT_FIELDS) or type(value["trusted_receipt_issuers"]) is not list or not value["trusted_receipt_issuers"] or len(value["trusted_receipt_issuers"]) != len(set(value["trusted_receipt_issuers"])) or any(type(item) is not str or not item for item in value["trusted_receipt_issuers"]):
        _fail("error, timeout, or receipt policy is invalid")
    eligibility, ranking = value["eligibility"], value["ranking"]
    if (eligibility is None) != (ranking is None):
        _fail("eligibility and ranking must both be set or null")
    if eligibility is not None:
        if type(eligibility) is not dict or set(eligibility) != {"semantic_coverage_min", "oracle_coverage_min", "receipt_coverage_min", "require_complete_execution"} or type(eligibility["require_complete_execution"]) is not bool:
            _fail("eligibility has an invalid schema")
        if any(_decimal(eligibility[name], name, nonnegative=True) != 1 for name in ("semantic_coverage_min", "oracle_coverage_min", "receipt_coverage_min")):
            _fail("ranking coverage must be 1")
        if type(ranking) is not dict or set(ranking) != {"ordered_keys", "directions", "tie_break"} or tuple(ranking["ordered_keys"]) not in {_RANK_KEYS, _LEGACY_RANK_KEYS} or tuple(ranking["directions"]) != _RANK_DIRECTIONS or ranking["tie_break"] != "variant_id":
            _fail("ranking order is fixed")
    return value


def _validate_v2_bundle_metadata(value: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    for name in ("freeze_digest", "compiler_module_sha256", "compiled_oracle_content_digest", "policy_digest"):
        if name in value and (type(value[name]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None):
            _fail("oracle bundle v2 metadata is invalid")
    source_manifests = value.get("source_manifest_hashes")
    if source_manifests is not None and (type(source_manifests) is not dict or set(source_manifests) != {"candidate_manifest", "financial_statements_manifest", "historical_price_manifest", "realtime_quote_manifest"} or any(type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in source_manifests.values())):
        _fail("oracle bundle v2 source manifests are invalid")
    source_hashes = value.get("source_hashes")
    if source_hashes is not None and (type(source_hashes) is not dict or not source_hashes or any(type(path) is not str or not path or Path(path).is_absolute() or ".." in Path(path).parts or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None for path, digest in source_hashes.items())):
        _fail("oracle bundle v2 source hashes are invalid")
    counts = value.get("expected_status_counts")
    if counts is not None:
        expected = manifest.get("expected_status_counts")
        if type(counts) is not dict or counts != expected:
            _fail("oracle bundle v2 status counts do not bind to manifest")


def _validate_bundle(value: Any, manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if type(value) is not dict or type(value.get("oracles")) is not dict:
        _fail("oracle bundle has an invalid schema")
    schema_version = value.get("schema_version")
    if schema_version == "oracle-bundle/v1":
        if set(value) != _ORACLE_KEYS:
            _fail("oracle bundle has an invalid schema")
        v2 = False
    elif schema_version == "oracle-bundle/v2":
        if not _ORACLE_KEYS <= set(value) <= _ORACLE_V2_ROOT_KEYS:
            _fail("oracle bundle v2 has an invalid schema")
        _validate_v2_bundle_metadata(value, manifest)
        v2 = True
    else:
        _fail("oracle bundle has an invalid schema")
    cases = {case["case_id"]: case for case in manifest["cases"] if "score_case" in case}
    result: dict[str, dict[str, Any]] = {}
    for oracle_id, oracle in value["oracles"].items():
        _safe_id(oracle_id, "oracle_id")
        allowed = _ORACLE_V2_ITEM_KEYS if v2 else _ORACLE_ITEM_KEYS
        if type(oracle) is not dict or not _ORACLE_ITEM_KEYS <= set(oracle) <= allowed or oracle.get("oracle_id") != oracle_id or oracle.get("case_id") not in cases or oracle.get("independence") not in {"independent_frozen", "independent_dynamic", "unavailable"} or any(type(oracle.get(name)) is not str or not oracle[name] for name in ("source_ref", "version")) or any(oracle.get(name) not in {"approved", "unavailable", "not_applicable"} for name in ("semantic_review_status", "data_review_status", "state_review_status")):
            _fail("oracle has an invalid schema")
        case = cases[oracle["case_id"]]
        case_type = case["score_case"]["case_type"]
        if v2 and (("suite" in oracle and oracle["suite"] != case["suite"]) or ("expected_status" in oracle and oracle["expected_status"] != case["score_case"]["expected_status"]) or ("runtime_contract" in oracle and type(oracle["runtime_contract"]) not in {dict, type(None)}) or ("data_not_scored_until_receipt" in oracle and type(oracle["data_not_scored_until_receipt"]) is not bool) or any(name in oracle and (type(oracle[name]) is not str or not oracle[name]) for name in ("source_case_id", "source_oracle_id"))):
            _fail("oracle bundle v2 extension fields are invalid")
        alternatives = oracle.get("alternative_assertion_sets")
        if alternatives is not None and (type(alternatives) is not list or not alternatives or any(type(item) is not dict or set(item) != {"semantic_assertions", "data_assertions", "state_assertions"} for item in alternatives)):
            _fail("alternative assertion sets are invalid")
        assertion_sets = _oracle_assertion_sets(oracle)
        if not assertion_sets:
            _fail("oracle has no assertion sets")
        for assertion_set in assertion_sets:
            for kind in ("semantic_assertions", "data_assertions", "state_assertions"):
                _validate_assertions(assertion_set[kind], kind=kind, case_type=case_type, expected_status=case["score_case"]["expected_status"])
        semantic_ok = oracle["semantic_review_status"] == "approved" and all(bool(item["semantic_assertions"]) for item in assertion_sets)
        data_ok = oracle["data_review_status"] == "approved" and all(bool(item["data_assertions"]) for item in assertion_sets) and oracle["independence"] in {"independent_frozen", "independent_dynamic"}
        state_ok = oracle["state_review_status"] == "approved" and all(bool(item["state_assertions"]) for item in assertion_sets)
        if case_type == "normal":
            if oracle["state_review_status"] != "not_applicable" or any(item["state_assertions"] for item in assertion_sets) or (oracle["data_review_status"] == "not_applicable" and any(item["data_assertions"] for item in assertion_sets)):
                _fail("normal oracle reviews are inconsistent")
        elif oracle["data_review_status"] != "not_applicable" or any(item["data_assertions"] for item in assertion_sets) or oracle["independence"] != "unavailable" or not state_ok:
            _fail("boundary oracle reviews are inconsistent")
        # Unavailable semantic/data review is preserved as an unscored cell, not guessed.
        if not semantic_ok and oracle["semantic_review_status"] not in {"unavailable", "not_applicable"} and not (v2 and case_type == "boundary" and state_ok):
            _fail("semantic oracle is inconsistent")
        if case_type == "normal" and not data_ok and oracle["data_review_status"] not in {"unavailable", "not_applicable", "approved"}:
            _fail("data oracle is inconsistent")
        evidence = oracle["reference_evidence"]
        if v2:
            if evidence is not None and type(evidence) is not dict:
                _fail("oracle bundle v2 reference evidence is invalid")
        elif case["suite"] == "realtime_quote" and any(item["data_assertions"] for item in assertion_sets):
            if oracle["independence"] != "independent_dynamic" or type(evidence) is not dict or set(evidence) != {"before_hash", "after_hash", "source_contract_hash", "window_rule_version"} or any(type(evidence.get(name)) is not str or re.fullmatch(r"[0-9a-f]{64}", evidence[name]) is None for name in ("before_hash", "after_hash", "source_contract_hash")):
                _fail("realtime oracle reference evidence is invalid")
            _safe_id(evidence["window_rule_version"], "window_rule_version")
        elif evidence is not None:
            _fail("reference evidence is only allowed for realtime data oracles")
        result[oracle_id] = oracle
    for case in cases.values():
        oracle = result.get(case["score_case"]["oracle_id"])
        if oracle is None or oracle["case_id"] != case["case_id"]:
            _fail("case oracle is missing or mismatched")
    return result


def _percentile(values: list[float], probability: float, method: str) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if method == "nearest_rank":
        return ordered[max(0, ceil(probability * len(ordered)) - 1)]
    position = (len(ordered) - 1) * probability
    lower, upper = int(position), ceil(position)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean(values: list[int]) -> float | None:
    return None if not values else sum(values) / len(values)


def manifest_case_type(manifest: Mapping[str, Any], case_id: str) -> str:
    return next(case["score_case"]["case_type"] for case in manifest["cases"] if case["case_id"] == case_id)


class BenchmarkScorer:
    def __init__(self, store: RunStore, *, policy: Mapping[str, Any], oracle_bundle: Mapping[str, Any], approved_policy_digests: set[str] | list[str] | tuple[str, ...], approved_oracle_bundle_digests: set[str] | list[str] | tuple[str, ...], wall_clock: Any = time.time) -> None:
        self.store, self.policy_raw, self.bundle_raw, self.wall_clock = store, dict(policy), dict(oracle_bundle), wall_clock
        self.policy_digest, self.oracle_digest = _digest(self.policy_raw), _digest(self.bundle_raw)
        self.approved_policy_digests, self.approved_oracle_bundle_digests = set(approved_policy_digests), set(approved_oracle_bundle_digests)

    def get_projection(self, run_id: str) -> dict[str, Any] | None:
        try:
            projection, events = self.store.load_score_projection(run_id), self.store.score_events(run_id)
        except RunBackendError as exc:
            raise BenchmarkScoreError(str(exc)) from exc
        if projection is None:
            return None
        manifest, execution = self.store.load_manifest(run_id), self.store.events(run_id)
        contract = manifest.get("scoring_contract")
        expected = {"execution_tail_hash": execution[-1]["event_hash"] if execution else None, "policy_digest": self.policy_digest, "oracle_bundle_digest": self.oracle_digest, "scorer_version": SCORER_VERSION, "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(manifest["variants"])}
        if type(contract) is not dict or contract.get("policy_digest") != self.policy_digest or contract.get("oracle_bundle_digest") != self.oracle_digest or contract.get("scorer_version") != SCORER_VERSION or contract.get("scorer_digest") != SCORER_DIGEST or contract.get("variant_contract_digest") != expected["variant_contract_digest"] or projection.get("manifest_hash") != _digest(manifest) or projection.get("bindings") != expected:
            _fail("score projection is detached from current execution inputs")
        if not events or events[-1].get("event_type") != "scorer_projection" or projection.get("score_tail_hash") != events[-1].get("score_event_hash") or projection.get("projection_hash") != events[-1].get("projection_hash"):
            _fail("score projection is detached from the score journal")
        return projection

    def score(self, run_id: str) -> dict[str, Any]:
        try:
            with self.store.locked(run_id):
                manifest, execution = self.store.load_manifest(run_id), self.store.events(run_id)
                if not execution or execution[-1].get("event_type") != "run_finished":
                    _fail("run must be finished before scoring")
                contract = manifest.get("scoring_contract")
                if type(contract) is not dict or contract.get("policy_digest") != self.policy_digest or contract.get("oracle_bundle_digest") != self.oracle_digest or contract.get("scorer_version") != SCORER_VERSION or contract.get("scorer_digest") != SCORER_DIGEST or contract.get("variant_contract_digest") != _variant_contract_digest(manifest["variants"]) or self.policy_digest not in self.approved_policy_digests or self.oracle_digest not in self.approved_oracle_bundle_digests:
                    _fail("scoring contract digest is not approved")
                policy, oracles = _validate_policy(self.policy_raw), _validate_bundle(self.bundle_raw, manifest)
                if manifest["mode"] == "official" and policy["timeout_latency_treatment"] != "cap_at_timeout":
                    _fail("official scoring requires cap_at_timeout latency")
                if any((case["score_case"]["case_type"] == "normal" and case["score_case"]["expected_status"] != ["success"]) or (case["score_case"]["case_type"] == "boundary" and any(status not in {"needs_clarification", "unsupported", "no_data"} for status in case["score_case"]["expected_status"])) for case in manifest["cases"] if "score_case" in case):
                    _fail("case expected_status is outside the frozen policy")
                bindings = {"execution_tail_hash": execution[-1]["event_hash"], "policy_digest": self.policy_digest, "oracle_bundle_digest": self.oracle_digest, "scorer_version": SCORER_VERSION, "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(manifest["variants"])}
                records = self._records(manifest, execution, oracles, policy)
                public_records = [{key: value for key, value in record.items() if key != "_usage_values"} for record in records]
                events = self.store.score_events(run_id)
                if events and events[0].get("bindings") != bindings:
                    _fail("existing score journal has different inputs")
                saved = [event["record"] for event in events if event["event_type"] == "score_record"]
                if saved != public_records[:len(saved)]:
                    _fail("score journal records do not match immutable evidence")
                if events and events[-1]["event_type"] == "scorer_projection":
                    projection = self._projection(manifest, policy, records, bindings)
                    projection["projection_hash"] = _score_projection_hash(projection)
                    if projection["projection_hash"] != events[-1]["projection_hash"]:
                        _fail("score journal projection does not match immutable evidence")
                    projection["score_tail_hash"] = events[-1]["score_event_hash"]
                    existing = self.get_projection(run_id)
                    if existing is not None:
                        if existing != projection:
                            _fail("existing score projection has different inputs")
                        return existing
                    self.store.write_score_projection(run_id, projection)
                    return projection
                if not events:
                    self.store.append_score_event(run_id, {"event_type": "score_started", "bindings": bindings})
                for record in public_records[len(saved):]:
                    self.store.append_score_event(run_id, {"event_type": "score_record", "bindings": bindings, "record": record})
                projection = self._projection(manifest, policy, records, bindings)
                projection["projection_hash"] = _score_projection_hash(projection)
                event = self.store.append_score_event(run_id, {"event_type": "scorer_projection", "bindings": bindings, "projection_hash": projection["projection_hash"]})
                projection["score_tail_hash"] = event["score_event_hash"]
                self.store.write_score_projection(run_id, projection)
                return projection
        except RunBackendError as exc:
            raise BenchmarkScoreError(str(exc)) from exc

    def _records(self, manifest: Mapping[str, Any], events: list[dict[str, Any]], oracles: Mapping[str, Mapping[str, Any]], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
        dispatch = {event["cell_id"]: event for event in events if event["event_type"] == "dispatch_intent"}
        terminals = {event["cell_id"]: event for event in events if event["event_type"] == "terminal"}
        before = {event["cell_id"]: event for event in events if event["event_type"] == "reference_before"}
        after = {event["cell_id"]: event for event in events if event["event_type"] == "reference_after"}
        result = []
        for variant in sorted(manifest["variants"], key=lambda item: item["stable_display_order"]):
            for case in manifest["cases"]:
                if "score_case" not in case:
                    continue
                cell_id = "cell-" + _digest([manifest["run_id"], variant["variant_id"], case["case_id"], 1])[:48]
                comparable = True
                if case["suite"] == "realtime_quote":
                    evidence = oracles[case["score_case"]["oracle_id"]]["reference_evidence"]
                    reference_contract = case["reference_contract"]
                    comparable = self._realtime_comparable(before.get(cell_id), terminals.get(cell_id), after.get(cell_id), evidence, reference_contract, policy)
                result.append(self._record(variant, case, cell_id, dispatch.get(cell_id), terminals.get(cell_id), comparable, oracles[case["score_case"]["oracle_id"]], policy))
        return result

    def _record(self, variant: Mapping[str, Any], case: Mapping[str, Any], cell_id: str, dispatch: Mapping[str, Any] | None, terminal: Mapping[str, Any] | None, comparable: bool, oracle: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
        variant_id, identity = variant["variant_id"], _variant_identity(variant)
        if dispatch is not None and dispatch.get("variant_identity") != identity:
            _fail("dispatch identity does not bind to manifest variant")
        if terminal is not None and terminal.get("variant_identity") != identity:
            _fail("terminal identity does not bind to manifest variant")
        if terminal is not None and terminal.get("transport_status") == "completed":
            evidence = terminal.get("execution_evidence")
            if not isinstance(evidence, Mapping) or {field: evidence.get(field) for field in identity} != identity:
                _fail("terminal execution evidence does not bind to manifest variant")
        attempted, response = dispatch is not None, terminal.get("public_response") if terminal else None
        schema_valid = self._response_valid(response, policy)
        status = response.get("status") if schema_valid else None
        status_correct = schema_valid and status in case["score_case"]["expected_status"]
        timeout = bool(terminal and terminal.get("transport_status") == "timeout")
        execution_complete = terminal is not None and terminal.get("transport_status") == "completed" and not timeout and (case["suite"] != "realtime_quote" or comparable)
        assertion_sets = self._assertion_set_results(response or {}, _oracle_assertion_sets(oracle))
        selected = self._select_assertion_set(assertion_sets)
        coherent = next((item for item in assertion_sets if item["all_passed"]), None)
        semantic, state, data = selected["semantic"], selected["state"], selected["data"]
        semantic_eligible = oracle["semantic_review_status"] == "approved" and all(bool(item["semantic"]["summary"]) for item in assertion_sets)
        semantic_pass = semantic_eligible and schema_valid and status_correct and execution_complete and semantic["all_passed"] and state["all_passed"]
        normal = case["score_case"]["case_type"] == "normal"
        has_data, independent = all(bool(item["data"]["summary"]) for item in assertion_sets), oracle["independence"] in {"independent_frozen", "independent_dynamic"}
        data_eligible = normal and has_data and oracle["data_review_status"] == "approved" and independent and (case["suite"] != "realtime_quote" or comparable)
        data_pass: bool | str = bool(schema_valid and status_correct and execution_complete and state["all_passed"]) if not normal else "not_scored" if not data_eligible else bool(schema_valid and status_correct and execution_complete and data["all_passed"])
        codes: list[str] = []
        if not schema_valid: codes.append("RESPONSE_SCHEMA_INVALID")
        if not status_correct: codes.append("STATUS_MISMATCH")
        if not semantic_pass: codes.append("SEMANTIC_ASSERTION_FAILED")
        if not semantic_eligible: codes.append("SEMANTIC_ORACLE_UNAVAILABLE")
        if normal and not data_eligible: codes.append("ORACLE_UNAVAILABLE")
        if normal and case["suite"] == "realtime_quote" and not comparable: codes.extend(("DATA_INCOMPLETE", "ORACLE_UNAVAILABLE"))
        if has_data and data_pass is False: codes.append("DATA_INCOMPLETE" if data["fatal_missing"] else "DATA_ASSERTION_FAILED")
        if terminal is not None:
            if timeout: codes.append("TIMEOUT")
            elif terminal.get("transport_status") != "completed": codes.append("TRANSPORT_ERROR")
        usage = self._usage(terminal.get("usage") if terminal else "unknown", terminal.get("usage_source") if terminal else None, policy["usage_receipt_required_fields"], policy["trusted_receipt_issuers"], dispatch)
        if usage is None: codes.append("USAGE_UNAVAILABLE")
        return {"variant_id": variant_id, "variant_identity": identity, "case_id": case["case_id"], "trial": 1, "cell_id": cell_id, "oracle_id": oracle["oracle_id"], "oracle_hash": _digest(oracle), "response_hash": terminal.get("response_hash") if terminal else None, "attempted": attempted, "schema_valid": schema_valid, "status_correct": status_correct, "semantic_eligible": semantic_eligible, "semantic_pass": semantic_pass, "semantic_assertions": semantic["summary"], "state_assertions": state["summary"], "data_eligible": data_eligible, "data_pass": data_pass, "data_assertions": data["summary"], "assertion_set_index": selected["index"], "accepted_assertion_set_index": coherent["index"] if coherent is not None else None, "case_pass": schema_valid and status_correct and semantic_eligible and coherent is not None and data_pass is True and execution_complete, "execution_complete": execution_complete, "elapsed_ms": terminal.get("elapsed_ms") if attempted and terminal else None, "timeout": timeout, "usage": "known" if usage is not None else "unknown", "_usage_values": usage, "failure_codes": sorted(set(codes))}

    @staticmethod
    def _response_valid(response: Any, policy: Mapping[str, Any]) -> bool:
        if type(response) is not dict or response.get("schema_version") != policy["response_schema_version"] or response.get("status") not in _RESPONSE_STATUSES:
            return False
        contract = policy["response_status_contracts"][response["status"]]
        for path in contract["required_non_null_paths"]:
            exists, value = _path_value(response, path)
            if not exists or value is None or value == "" or value == {} or value == []:
                return False
        for path in contract["required_null_paths"]:
            exists, value = _path_value(response, path)
            if exists and value not in (None, {}, []):
                return False
        source = response.get("source")
        if response["status"] in {"success", "partial"} and not (type(response.get("resolved_request")) is dict and type(response.get("data")) is dict and type(response.get("as_of")) is str and type(source) is str and bool(source)):
            return False
        if response["status"] == "needs_clarification" and not (type(response.get("clarification")) is str and bool(response["clarification"])):
            return False
        if response["status"] in {"unsupported", "no_data", "error"} and not (type(response.get("terminal_reason")) is str and bool(response["terminal_reason"])):
            return False
        return True

    @staticmethod
    def _assertions(response: Mapping[str, Any], assertions: list[Mapping[str, Any]]) -> dict[str, Any]:
        summary = []
        for assertion in assertions:
            exists, actual = _path_value(response, assertion["path"])
            passed = exists and _assertion_pass(actual, assertion)
            summary.append({"path": assertion["path"], "operator": assertion["operator"], "passed": passed, "weight": str(_decimal(assertion["weight"], "weight")), "fatal": assertion["fatal"]})
        return {"summary": summary, "all_passed": all(item["passed"] for item in summary), "fatal_failed": any(item["fatal"] and not item["passed"] for item in summary), "fatal_missing": any(item["fatal"] and not item["passed"] and not _path_value(response, item["path"])[0] for item in summary)}

    @classmethod
    def _assertion_set_results(cls, response: Mapping[str, Any], assertion_sets: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for index, item in enumerate(assertion_sets):
            semantic, data, state = cls._assertions(response, item["semantic_assertions"]), cls._assertions(response, item["data_assertions"]), cls._assertions(response, item["state_assertions"])
            result.append({"index": index, "semantic": semantic, "data": data, "state": state, "all_passed": semantic["all_passed"] and data["all_passed"] and state["all_passed"]})
        return result

    @staticmethod
    def _select_assertion_set(results: list[dict[str, Any]]) -> dict[str, Any]:
        def score(item: Mapping[str, Any]) -> tuple[Any, ...]:
            data = item["data"]["summary"]
            passed = sum(_decimal(assertion["weight"], "weight") for assertion in data if assertion["passed"])
            total = sum((_decimal(assertion["weight"], "weight") for assertion in data), Decimal())
            return (item["semantic"]["all_passed"] and item["state"]["all_passed"] and item["data"]["all_passed"], item["semantic"]["all_passed"] and item["state"]["all_passed"], passed / total if total else Decimal(), -item["index"])
        return max(results, key=score)

    @staticmethod
    def _usage(value: Any, usage_source: Any, required: list[str], issuers: list[str], dispatch: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if usage_source != "public_meta_usage" or type(value) is not dict or any(name not in value for name in required): return None
        if any(type(value[name]) is not str or not value[name] for name in ("receipt_id", "measurement_version", "cache_status", "request_id", "issuer")): return None
        if any(type(value[name]) is not int or isinstance(value[name], bool) or value[name] < 0 for name in ("input_tokens", "output_tokens", "total_tokens")): return None
        if value["total_tokens"] != value["input_tokens"] + value["output_tokens"] or value["issuer"] not in issuers or dispatch is None or value["request_id"] != dispatch["attempt_id"]: return None
        return {name: value[name] for name in _RECEIPT_FIELDS}

    @staticmethod
    def _reference_comparable(event: Mapping[str, Any] | None) -> bool:
        reference = event.get("reference") if event else None
        return type(reference) is dict and reference.get("comparability") == "comparable" and type(reference.get("source")) is str and bool(reference["source"]) and type(reference.get("as_of")) is str and bool(reference["as_of"])

    @classmethod
    def _realtime_comparable(cls, before: Mapping[str, Any] | None, terminal: Mapping[str, Any] | None, after: Mapping[str, Any] | None, evidence: Any, contract: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
        if not all(cls._reference_comparable(event) for event in (before, after)) or type(terminal) is not dict or terminal.get("comparability") != "comparable" or type(evidence) is not dict:
            return False
        before_ref, after_ref = before["reference"], after["reference"]
        times = [event.get("emitted_at") for event in (before, terminal, after)]
        if any(type(value) not in (int, float) or isinstance(value, bool) for value in times) or not times[0] <= times[1] <= times[2] or Decimal(str(times[2])) - Decimal(str(times[0])) > _decimal(policy["max_reference_window_seconds"], "max_reference_window_seconds", nonnegative=True):
            return False
        return before_ref["source"] == after_ref["source"] and evidence.get("before_hash") == before_ref.get("hash") and evidence.get("after_hash") == after_ref.get("hash") and evidence.get("source_contract_hash") == contract.get("source_contract_hash") and evidence.get("window_rule_version") == contract.get("window_rule_version")

    def _projection(self, manifest: Mapping[str, Any], policy: Mapping[str, Any], records: list[dict[str, Any]], bindings: Mapping[str, str]) -> dict[str, Any]:
        variants, rankable = [], []
        for variant in sorted(manifest["variants"], key=lambda item: item["stable_display_order"]):
            rows = [row for row in records if row["variant_id"] == variant["variant_id"]]
            attempted = [row for row in rows if row["attempted"]]
            semantic_rows = [row for row in attempted if row["semantic_eligible"]]
            semantic_expected = attempted
            data_expected = [row for row in rows if manifest_case_type(manifest, row["case_id"]) == "normal"]
            data_rows = [row for row in data_expected if row["data_eligible"]]
            data_weight = sum((_decimal(item["weight"], "weight") for row in data_rows for item in row["data_assertions"]), Decimal())
            data_passed_weight = sum((_decimal(item["weight"], "weight") for row in data_rows for item in row["data_assertions"] if item["passed"] and row["schema_valid"] and row["status_correct"] and row["execution_complete"]), Decimal())
            latency = [float(min(row["elapsed_ms"], manifest["timeout_ms"])) if row["timeout"] and policy["timeout_latency_treatment"] == "cap_at_timeout" else float(row["elapsed_ms"]) for row in attempted if type(row["elapsed_ms"]) in (int, float) and not isinstance(row["elapsed_ms"], bool)]
            usage_rows = [row["_usage_values"] for row in attempted if row["_usage_values"] is not None]
            semantic_coverage, oracle_coverage, receipt_coverage = _ratio(len(semantic_rows), len(semantic_expected)), _ratio(sum(row["data_eligible"] for row in data_expected), len(data_expected)), _ratio(len(usage_rows), len(attempted))
            token_metrics = {"count": len(usage_rows), "receipt_coverage": receipt_coverage}
            for prefix, field in (("input", "input_tokens"), ("output", "output_tokens"), ("total", "total_tokens")):
                values = [usage[field] for usage in usage_rows]
                token_metrics.update({prefix + "_mean": _mean(values), prefix + "_p50": _percentile([float(value) for value in values], .5, policy["percentile_method"]), prefix + "_p95": _percentile([float(value) for value in values], .95, policy["percentile_method"])})
            metrics = {"semantic_accuracy": {"passed": sum(row["semantic_pass"] for row in semantic_rows), "denominator": len(semantic_rows), "value": _ratio(sum(row["semantic_pass"] for row in semantic_rows), len(semantic_rows))}, "data_accuracy": {"passed_weight": _number(data_passed_weight), "eligible_weight": _number(data_weight), "value": _ratio(data_passed_weight, data_weight)}, "end_to_end_latency": {"count": len(latency), "raw_count": len(latency), "p50_ms": _percentile(latency, .5, policy["percentile_method"]), "p95_ms": _percentile(latency, .95, policy["percentile_method"]), "max_ms": max(latency, default=None), "timeout_rate": _ratio(sum(row["timeout"] for row in attempted), len(attempted))}, "token_usage": token_metrics}
            complete = len(attempted) == len(rows) and all(row["execution_complete"] for row in rows)
            item = {"variant_id": variant["variant_id"], "stable_display_order": variant["stable_display_order"], "metrics": metrics, "case_pass_rate": {"passed": sum(row["case_pass"] for row in attempted), "denominator": len(attempted), "value": _ratio(sum(row["case_pass"] for row in attempted), len(attempted))}, "semantic_oracle_coverage": {"available": len(semantic_rows), "denominator": len(semantic_expected), "value": semantic_coverage}, "oracle_coverage": {"available": sum(row["data_eligible"] for row in data_expected), "denominator": len(data_expected), "value": oracle_coverage}, "receipt_coverage": {"available": len(usage_rows), "denominator": len(attempted), "value": receipt_coverage}, "completeness_reasons": sorted({code for row in rows for code in row["failure_codes"] if code in {"ORACLE_UNAVAILABLE", "SEMANTIC_ORACLE_UNAVAILABLE", "USAGE_UNAVAILABLE"}})}
            reason, eligibility = None, policy["eligibility"]
            if eligibility is not None:
                if eligibility["require_complete_execution"] and not complete: reason = "INCOMPLETE_EXECUTION"
                elif semantic_coverage != 1 or _decimal(semantic_coverage, "semantic_coverage") < _decimal(eligibility["semantic_coverage_min"], "semantic_coverage_min"): reason = "SEMANTIC_COVERAGE"
                elif oracle_coverage != 1 or _decimal(oracle_coverage, "oracle_coverage") < _decimal(eligibility["oracle_coverage_min"], "oracle_coverage_min"): reason = "ORACLE_COVERAGE"
                elif receipt_coverage != 1 or _decimal(receipt_coverage, "receipt_coverage") < _decimal(eligibility["receipt_coverage_min"], "receipt_coverage_min"): reason = "RECEIPT_COVERAGE"
                elif metrics["token_usage"]["total_mean"] is None: reason = "RECEIPT_COVERAGE"
            item["eligibility"] = "eligible" if eligibility is not None and reason is None else "not_ranked" if eligibility is None else "ineligible"
            if reason: item["ineligible_reason"] = reason
            variants.append(item)
            if item["eligibility"] == "eligible": rankable.append(item)
        ranked = self._rank(rankable) if policy["ranking"] else []
        for rank, item in enumerate(ranked, 1): item["rank"] = rank
        return {"schema_version": "qveris-benchmark-score-projection/v1", "run_id": manifest["run_id"], "manifest_hash": _digest(manifest), "bindings": dict(bindings), "receipt_basis": "structurally_bound_attested_receipt", "projection_status": "SCORED" if ranked else "SCORED_NOT_RANKED", "variants": variants, "ranked_results": [{"variant_id": item["variant_id"], "rank": item["rank"]} for item in ranked], "ineligible_results": [{"variant_id": item["variant_id"], "reason": item.get("ineligible_reason", "RANKING_POLICY_ABSENT")} for item in variants if item not in ranked], "public_failure_summaries": sorted({code for row in records for code in row["failure_codes"]})}

    @staticmethod
    def _rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def value(item: Mapping[str, Any], name: str) -> Any:
            return item["case_pass_rate"]["value"] if name == "case_pass_rate" else item["metrics"]["data_accuracy"]["value"] if name == "data_accuracy" else item["metrics"]["semantic_accuracy"]["value"] if name == "semantic_accuracy" else item["metrics"]["end_to_end_latency"]["p95_ms"] if name in {"end_to_end_latency_p95_ms", "e2e_p95_ms"} else item["metrics"]["token_usage"]["total_mean"]
        def key(item: Mapping[str, Any]) -> tuple[Any, ...]:
            result = []
            for name, direction in zip(_RANK_KEYS, _RANK_DIRECTIONS):
                raw = value(item, name)
                result.append((1, Decimal()) if raw is None else (0, -_decimal(raw, name) if direction == "desc" else _decimal(raw, name)))
            return tuple(result) + (item["variant_id"],)
        return sorted(items, key=key)

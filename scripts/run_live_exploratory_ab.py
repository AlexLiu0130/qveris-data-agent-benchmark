#!/usr/bin/env python3
"""Run exactly one non-ranking FS-049 A/B experiment through the Runner.

Default and ``--preflight`` are local-only.  ``--run`` is the only flag that
can make supplier calls; it writes the RunStore solely outside this checkout.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qveris_benchmark.financial_diagnostic import compile_with_digest
from qveris_benchmark.model_gateway import ModelGatewayClient
from qveris_benchmark.qveris_search import DEFAULT_TIMEOUT_SECONDS as QVERIS_TRANSPORT_TIMEOUT_SECONDS, QVerisSearchClient
from qveris_benchmark.run_backend import RunService, RunStore, _digest
from qveris_benchmark.runner_gateway_agent import (
    AGENT_VERSION,
    MODEL_ID,
    MODEL_MAX_TOKENS,
    RunnerGatewayAgent,
    ToolFreeze,
    _selector,
    bind_fs049_parameters,
    output_contract_digests,
    tool_freeze,
)
from qveris_benchmark.web_search import TavilyWebSearchClient


CASE_ID = "FS-049"
FREEZE_SCHEMA_VERSION = "exploratory-ab-tool-freeze/v1"
RUN_ID = "fs049-exploratory-ab-gateway-v1"
RUN_B_ID = "fs049-exploratory-b-gateway-v1"
RUN_GATEWAY_PROBE_ID = "fs049-gateway-only-probe-v5"
LIVE_GATEWAY_TIMEOUT_SECONDS = 90.0
EXPLORATORY_TIMEOUT_MARGIN_SECONDS = 5.0
# B performs Search, Inspect, Execute (each bounded by QVeris' transport timeout)
# followed by one Gateway completion. The outer alarm must not preempt that bound.
EXPLORATORY_CELL_TIMEOUT_MS = int((3 * QVERIS_TRANSPORT_TIMEOUT_SECONDS + LIVE_GATEWAY_TIMEOUT_SECONDS + EXPLORATORY_TIMEOUT_MARGIN_SECONDS) * 1000)
GATEWAY_PROBE_TIMEOUT_MS = int((LIVE_GATEWAY_TIMEOUT_SECONDS + EXPLORATORY_TIMEOUT_MARGIN_SECONDS) * 1000)
MODEL_CONFIG = {
    "model_id": MODEL_ID,
    "temperature": 0.0,
    "max_tokens": MODEL_MAX_TOKENS,
    "response_format": "json_object",
}
ABSOLUTE_EXECUTE_COST_CAP = Decimal("25")
ABSOLUTE_EXECUTE_COST_CAP_CONTRACT = {
    "schema_version": "fs049-execute-cost-cap/v1",
    "case_id": CASE_ID,
    "unit": "credits",
    "maximum": "25",
}
ABSOLUTE_EXECUTE_COST_CAP_DIGEST = _digest(ABSOLUTE_EXECUTE_COST_CAP_CONTRACT)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYSTEM_CA_BUNDLE = Path("/etc/ssl/cert.pem")


class PreflightError(ValueError):
    pass


def _ca_bundle(environment_name: str) -> str | None:
    """Prefer an explicit bundle, then the verified system bundle available here."""
    configured = os.environ.get(environment_name) or os.environ.get("SSL_CERT_FILE")
    if configured:
        return configured
    return str(_SYSTEM_CA_BUNDLE) if _SYSTEM_CA_BUNDLE.is_file() else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PreflightError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any, field: str) -> datetime:
    if type(value) is not str:
        raise PreflightError("tool freeze %s is invalid" % field)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PreflightError("tool freeze %s is invalid" % field) from exc
    if _format_utc(parsed) != value:
        raise PreflightError("tool freeze %s is invalid" % field)
    return parsed


def _decimal(value: Any, field: str) -> Decimal:
    if type(value) not in (str, int, float) or isinstance(value, bool):
        raise PreflightError("%s must be a finite non-negative number" % field)
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PreflightError("%s must be a finite non-negative number" % field) from exc
    if not result.is_finite() or result < 0:
        raise PreflightError("%s must be a finite non-negative number" % field)
    return result


def _execute_budget(value: Any) -> Decimal:
    budget = _decimal(value, "max_execute_cost")
    if budget > ABSOLUTE_EXECUTE_COST_CAP:
        raise PreflightError("max_execute_cost exceeds the absolute FS-049 cap of 25 credits")
    return budget


def _require_env(name: str) -> None:
    value = os.environ.get(name)
    if type(value) is not str or not value or "\r" in value or "\n" in value:
        raise PreflightError("%s is required" % name)


def _environment_state(name: str) -> str:
    value = os.environ.get(name)
    return "ready" if type(value) is str and bool(value) and "\r" not in value and "\n" not in value else "not_ready"


def _ca_state(environment_name: str) -> str:
    configured = os.environ.get(environment_name) or os.environ.get("SSL_CERT_FILE")
    if not configured:
        return "ready"
    try:
        details = os.stat(configured)
    except OSError:
        return "not_ready"
    return "ready" if stat.S_ISREG(details.st_mode) and os.access(configured, os.R_OK) else "not_ready"


def _static_preflight(max_execute_cost: str) -> tuple[Decimal, dict[str, Any]]:
    """Inspect only local configuration; never construct a provider client."""
    budget = _execute_budget(max_execute_cost)
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        temp_state = "ready" if temp_root.is_dir() and os.access(temp_root, os.W_OK | os.X_OK) else "not_ready"
    except OSError:
        temp_state = "not_ready"
    qveris_state, tavily_state = _environment_state("QVERIS_API_KEY"), _environment_state("TAVILY_API_KEY")
    gateway_ca, qveris_ca = _ca_state("GATEWAY_CA_BUNDLE"), _ca_state("QVERIS_CA_BUNDLE")
    return budget, {
        "max_execute_cost": str(budget),
        "absolute_execute_cost_cap": str(ABSOLUTE_EXECUTE_COST_CAP),
        "absolute_execute_cost_cap_digest": ABSOLUTE_EXECUTE_COST_CAP_DIGEST,
        "temporary_directory": temp_state,
        "gateway_ca": gateway_ca,
        "qveris_ca": qveris_ca,
        "variants": {
            "qveris-api-plus-model": "not_ready" if qveris_state != "ready" or gateway_ca != "ready" or qveris_ca != "ready" else "needs_tool_freeze",
            "public-web-plus-model": "not_ready" if tavily_state != "ready" or gateway_ca != "ready" else "needs_tool_freeze",
        },
    }


def _read_private_regular_file(path_value: str) -> bytes:
    """Read a caller-owned regular freeze without following a symlink."""
    path = Path(path_value).expanduser()
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
            raise PreflightError("tool freeze must be a caller-owned regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise PreflightError("tool freeze is unreadable") from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise PreflightError("tool freeze must be a caller-owned regular file")
        if opened.st_size > 65_536:
            raise PreflightError("tool freeze is too large")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    except OSError as exc:
        raise PreflightError("tool freeze is unreadable") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _read_freeze(path_value: str, *, clock: Callable[[], datetime] = _utc_now) -> tuple[ToolFreeze, Decimal, dict[str, Any], dict[str, str]]:
    raw = _read_private_regular_file(path_value)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError("tool freeze is not JSON") from exc
    expected = {
        "schema_version", "tool_id", "data_type", "entity_symbol", "statement_type",
        "fiscal_year", "parameter_schema_digest", "expected_cost", "billing_rule_digest", "result_selector", "result_selector_digest", "absolute_execute_cost_cap", "absolute_execute_cost_cap_digest", "issued_at", "expires_at",
    }
    if type(value) is not dict or set(value) != expected or value["schema_version"] != FREEZE_SCHEMA_VERSION:
        raise PreflightError("tool freeze has an invalid schema")
    if type(value["tool_id"]) is not str or not value["tool_id"]:
        raise PreflightError("tool freeze tool_id is invalid")
    if any(type(value[name]) is not str or not value[name] for name in ("data_type", "entity_symbol", "statement_type")):
        raise PreflightError("tool freeze identity is invalid")
    if type(value["fiscal_year"]) is not int or isinstance(value["fiscal_year"], bool):
        raise PreflightError("tool freeze fiscal_year is invalid")
    if any(type(value[name]) is not str or _SHA256.fullmatch(value[name]) is None for name in ("parameter_schema_digest", "billing_rule_digest", "result_selector_digest")):
        raise PreflightError("tool freeze digest is invalid")
    if type(value["expected_cost"]) is not str:
        raise PreflightError("tool freeze expected_cost is invalid")
    expected_cost = _decimal(value["expected_cost"], "tool freeze expected_cost")
    try:
        selector = _selector(value["result_selector"])
    except ValueError as exc:
        raise PreflightError("tool freeze result_selector is invalid") from exc
    if _digest(selector) != value["result_selector_digest"]:
        raise PreflightError("tool freeze result_selector digest does not match")
    if value["absolute_execute_cost_cap"] != str(ABSOLUTE_EXECUTE_COST_CAP) or value["absolute_execute_cost_cap_digest"] != ABSOLUTE_EXECUTE_COST_CAP_DIGEST:
        raise PreflightError("tool freeze absolute execute-cost cap does not match")
    issued_at = _parse_utc(value["issued_at"], "issued_at")
    expires_at = _parse_utc(value["expires_at"], "expires_at")
    now = _parse_utc(_format_utc(clock()), "clock")
    if issued_at > now:
        raise PreflightError("tool freeze issued_at is in the future")
    if expires_at <= now:
        raise PreflightError("tool freeze has expired")
    return ToolFreeze(
        tool_id=value["tool_id"], data_type=value["data_type"], entity_symbol=value["entity_symbol"],
        statement_type=value["statement_type"], fiscal_year=value["fiscal_year"],
        parameter_schema_digest=value["parameter_schema_digest"], expected_cost=str(value["expected_cost"]),
        billing_rule_digest=value["billing_rule_digest"], result_selector_digest=value["result_selector_digest"],
    ), expected_cost, selector, {"issued_at": value["issued_at"], "expires_at": value["expires_at"]}


def _case_and_metadata() -> tuple[dict[str, Any], list[dict[str, str]]]:
    variants = _variants([], "")
    compiled = compile_with_digest(ROOT, variants=variants)
    case = next(item for item in compiled["run_config"]["cases"] if item["case_id"] == CASE_ID)
    oracle = compiled["oracle_bundle"]["oracles"][case["score_case"]["oracle_id"]]
    metadata = [
        {"assertion_id": item["assertion_id"], "label": item["field"], "currency": item["currency"], "unit": item["unit"], "period": item["period"]}
        for item in oracle["data_assertions"]
    ]
    return case, metadata


def _variants(metadata: list[Mapping[str, str]], query: str) -> list[dict[str, Any]]:
    config_digest = _digest(MODEL_CONFIG)
    common = {
        "agent_version": AGENT_VERSION, "get_version": "not-a-get-v1",
        "model_identifier": MODEL_ID, "model_version": "gateway-v1", "model_config_digest": config_digest,
        **output_contract_digests(metadata, query),
    }
    return [
        {"variant_id": "public-web-plus-model", "stable_display_order": 1, "agent_variant_id": "public-web-plus-model-v1", "get_variant_id": "web-search-v1", **common},
        {"variant_id": "qveris-api-plus-model", "stable_display_order": 2, "agent_variant_id": "qveris-api-plus-model-v1", "get_variant_id": "qveris-search-execute-v1", **common},
    ]


def _manifest(case: dict[str, Any], metadata: list[Mapping[str, str]], freeze: ToolFreeze, selector: Mapping[str, Any], freeze_window: Mapping[str, str], budget: Decimal, *, b_only: bool = False) -> dict[str, Any]:
    freeze_binding = {
        "case_id": CASE_ID, "canonical_request": case["canonical_request"],
        "tool_freeze": {
            "tool_id": freeze.tool_id, "data_type": freeze.data_type,
            "entity_symbol": freeze.entity_symbol, "statement_type": freeze.statement_type,
            "fiscal_year": freeze.fiscal_year, "parameter_schema_digest": freeze.parameter_schema_digest,
            "expected_cost": freeze.expected_cost, "billing_rule_digest": freeze.billing_rule_digest,
            "result_selector_digest": freeze.result_selector_digest,
        },
        "result_selector": dict(selector), "output_contract": output_contract_digests(metadata, case["query"]),
        "issued_at": freeze_window["issued_at"], "expires_at": freeze_window["expires_at"],
        "max_execute_cost": str(budget), "absolute_execute_cost_cap": str(ABSOLUTE_EXECUTE_COST_CAP), "absolute_execute_cost_cap_digest": ABSOLUTE_EXECUTE_COST_CAP_DIGEST, "model_config": MODEL_CONFIG,
    }
    return {
        "run_id": RUN_B_ID if b_only else RUN_ID, "mode": "diagnostic", "execution_profile": "exploratory_ab",
        "freeze_digest": _digest(freeze_binding),
        "policy": {"version": "exploratory-ab-runner/v1", "scope": "exploratory_nonranking", "absolute_execute_cost_cap_digest": ABSOLUTE_EXECUTE_COST_CAP_DIGEST},
        "timeout_ms": EXPLORATORY_CELL_TIMEOUT_MS, "concurrency": 1, "variants": _variants(metadata, case["query"])[1:] if b_only else _variants(metadata, case["query"]),
        "cases": [{key: case[key] for key in ("case_id", "suite", "query", "canonical_request")}],
    }


def _synthetic_probe_result(metadata: list[Mapping[str, str]]) -> dict[str, Any]:
    """Internal no-data fixture: it binds all requested fields without financial values."""
    assertion_ids = [item.get("assertion_id") for item in metadata]
    if len(assertion_ids) != 18 or any(type(item) is not str or not item for item in assertion_ids) or len(set(assertion_ids)) != 18:
        raise PreflightError("FS-049 Gateway probe requires exactly 18 unique requested values")
    return {
        "schema_version": "gateway-only-synthetic-result/v1",
        "fields": [{"assertion_id": item, "value": None} for item in assertion_ids],
    }


def _gateway_probe_manifest(case: dict[str, Any], metadata: list[Mapping[str, str]]) -> dict[str, Any]:
    variant = {**_variants(metadata, case["query"])[0], "variant_id": "gateway-only-synthetic", "stable_display_order": 1, "agent_variant_id": "gateway-only-synthetic-v5", "get_variant_id": "synthetic-no-network-v1"}
    fixture = _synthetic_probe_result(metadata)
    return {
        "run_id": RUN_GATEWAY_PROBE_ID,
        "mode": "diagnostic",
        "execution_profile": "exploratory_gateway_probe",
        "freeze_digest": _digest({"case_id": CASE_ID, "synthetic_result_schema": fixture["schema_version"], "assertion_ids": [item["assertion_id"] for item in metadata], "model_config": MODEL_CONFIG}),
        "policy": {"version": "gateway-only-probe/v5", "scope": "exploratory_nonranking"},
        "timeout_ms": GATEWAY_PROBE_TIMEOUT_MS,
        "concurrency": 1,
        "variants": [variant],
        "cases": [{key: case[key] for key in ("case_id", "suite", "query", "canonical_request")}],
    }


def preflight(tool_freeze_path: str, max_execute_cost: str, *, require_tavily: bool, b_only: bool = False, clock: Callable[[], datetime] = _utc_now) -> tuple[dict[str, Any], list[dict[str, str]], ToolFreeze, dict[str, Any], dict[str, str], Decimal, dict[str, Any]]:
    """Validate every local prerequisite before constructing any client."""
    budget = _execute_budget(max_execute_cost)
    _require_env("QVERIS_API_KEY")
    if require_tavily:
        _require_env("TAVILY_API_KEY")
    freeze, expected_cost, selector, freeze_window = _read_freeze(tool_freeze_path, clock=clock)
    if expected_cost > budget:
        raise PreflightError("tool freeze expected_cost exceeds max_execute_cost")
    case, metadata = _case_and_metadata()
    intent = case["canonical_request"]
    if (freeze.data_type, freeze.entity_symbol, freeze.statement_type, freeze.fiscal_year) != (
        intent["data_type"], intent["entity"]["symbol"], intent["statement_type"], intent["time_or_period"]["fiscal_year"],
    ):
        raise PreflightError("tool freeze does not bind to FS-049")
    return case, metadata, freeze, selector, freeze_window, budget, _manifest(case, metadata, freeze, selector, freeze_window, budget, b_only=b_only)


def _output_root(path_value: str | None) -> Path:
    if path_value is None:
        return Path(tempfile.mkdtemp(prefix="qveris-exploratory-ab-"))
    path = Path(path_value).expanduser().resolve(strict=False)
    if path.exists():
        raise PreflightError("output_dir must not already exist")
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise PreflightError("output_dir must not be inside the repository")
    path.mkdir(mode=0o700, parents=True)
    return path


def run(case: dict[str, Any], metadata: list[dict[str, str]], freeze: ToolFreeze, selector: Mapping[str, Any], budget: Decimal, manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    """The only live path: two serial RunnerGatewayAgent cells via RunService."""
    gateway = ModelGatewayClient(timeout_seconds=LIVE_GATEWAY_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file=_ca_bundle("GATEWAY_CA_BUNDLE"))
    variants = manifest["variants"]
    qveris = QVerisSearchClient(timeout_seconds=QVERIS_TRANSPORT_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file=_ca_bundle("QVERIS_CA_BUNDLE"))
    qveris_agent = RunnerGatewayAgent(variant=variants[-1], case=case, metadata=metadata, gateway=gateway, qveris_search=qveris, tool_freeze=freeze, result_selector=selector, max_execute_cost=budget, force_inspect=True)
    clients = {variants[-1]["variant_id"]: qveris_agent}
    if len(variants) == 2:
        web = TavilyWebSearchClient()
        clients[variants[0]["variant_id"]] = RunnerGatewayAgent(variant=variants[0], case=case, metadata=metadata, gateway=gateway, web_search=web)
    service = RunService(RunStore(output_root), clients)
    service.create_run(manifest)
    return service.execute(manifest["run_id"])


def run_gateway_probe(case: dict[str, Any], metadata: list[dict[str, str]], manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    """One model catalogue call and one completion over a local no-data fixture."""
    gateway = ModelGatewayClient(timeout_seconds=LIVE_GATEWAY_TIMEOUT_SECONDS, use_environment_proxy=False, ca_file=_ca_bundle("GATEWAY_CA_BUNDLE"))
    variant = manifest["variants"][0]
    agent = RunnerGatewayAgent(variant=variant, case=case, metadata=metadata, gateway=gateway, synthetic_result=_synthetic_probe_result(metadata))
    service = RunService(RunStore(output_root), {variant["variant_id"]: agent})
    service.create_run(manifest)
    return service.execute(manifest["run_id"])


def _freeze_output(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if path.exists() or path.parent != temp_root and temp_root not in path.parents:
        raise PreflightError("freeze_output must be a new path under the system temporary directory")
    if not path.parent.is_dir():
        raise PreflightError("freeze_output parent directory must exist")
    return path


def _write_new_private_json(destination: Path, value: Mapping[str, Any]) -> None:
    """Atomically publish a new private freeze without overwriting a file."""
    fd, staging = tempfile.mkstemp(prefix=".qveris-freeze-", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(staging, destination)
        except FileExistsError as exc:
            raise PreflightError("freeze_output must be a new path under the system temporary directory") from exc
        if stat.S_IMODE(os.stat(destination, follow_symlinks=False).st_mode) != 0o600:
            raise PreflightError("freeze_output permissions are not private")
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(staging)
        except FileNotFoundError:
            pass


def discover_b_freeze(tool_id: str, max_execute_cost: str, output_path: str, *, clock: Callable[[], datetime] = _utc_now) -> Path:
    """Exactly Search then Inspect; never Execute or construct a Gateway client."""
    budget = _execute_budget(max_execute_cost)
    _require_env("QVERIS_API_KEY")
    if type(tool_id) is not str or not tool_id:
        raise PreflightError("tool_id is required for B freeze discovery")
    case, _metadata = _case_and_metadata()
    destination = _freeze_output(output_path)
    client = QVerisSearchClient(use_environment_proxy=False, ca_file=_ca_bundle("QVERIS_CA_BUNDLE"))
    catalog = client.search(query=case["query"], limit=5, session_id="fs049-freeze-discovery-v1")
    matches = [item for item in catalog.results if item.tool_id == tool_id]
    if len(matches) != 1:
        raise PreflightError("requested tool_id is not uniquely returned by Search")
    inspected = client.inspect(tool_id=tool_id, search_id=catalog.search_id, session_id="fs049-freeze-discovery-v1").tool
    if inspected.tool_id != tool_id or inspected.params is None:
        raise PreflightError("Inspect did not return a complete requested tool contract")
    selector = {"schema_version": "fs049-result-selector/v1", "parameters": {"symbol": "NVDA", "period": "FY", "limit": 5}, "target_symbol": "NVDA", "target_period": "FY", "target_fiscal_year": 2026, "target_date": "2026-01-25", "statement_type": "income_statement", "consolidated": True}
    try:
        parameters = bind_fs049_parameters(inspected.params)
    except ValueError as exc:
        raise PreflightError("tool contract cannot bind frozen FS-049 query parameters") from exc
    if parameters != selector["parameters"]:
        raise PreflightError("tool contract cannot bind FS-049 symbol, period, and limit")
    freeze = tool_freeze(tool=inspected, data_type="financial_statement", entity_symbol="NVDA", statement_type="income_statement", fiscal_year=2026, result_selector=selector)
    if freeze.expected_cost is None or _decimal(freeze.expected_cost, "tool expected_cost") > budget:
        raise PreflightError("tool expected_cost is missing or exceeds max_execute_cost")
    issued_at = _format_utc(clock())
    expires_at = _format_utc(_parse_utc(issued_at, "issued_at") + timedelta(minutes=15))
    value = {
        "schema_version": FREEZE_SCHEMA_VERSION, "tool_id": freeze.tool_id,
        "data_type": freeze.data_type, "entity_symbol": freeze.entity_symbol,
        "statement_type": freeze.statement_type, "fiscal_year": freeze.fiscal_year,
        "parameter_schema_digest": freeze.parameter_schema_digest, "expected_cost": freeze.expected_cost,
        "billing_rule_digest": freeze.billing_rule_digest, "result_selector": selector,
        "result_selector_digest": freeze.result_selector_digest,
        "absolute_execute_cost_cap": str(ABSOLUTE_EXECUTE_COST_CAP), "absolute_execute_cost_cap_digest": ABSOLUTE_EXECUTE_COST_CAP_DIGEST,
        "issued_at": issued_at, "expires_at": expires_at,
    }
    _write_new_private_json(destination, value)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="local-only validation (also the default)")
    mode.add_argument("--run", action="store_true", help="perform the two serial supplier-backed Runner cells")
    mode.add_argument("--run-b", action="store_true", help="perform the one serial QVeris API plus Gateway Runner cell")
    mode.add_argument("--probe-gateway-only", action="store_true", help="perform one Gateway-only diagnostic probe over an internal no-data fixture")
    mode.add_argument("--discover-b-freeze", action="store_true", help="Search then Inspect once to write a reviewed B freeze; never Execute or call Gateway")
    parser.add_argument("--tool-freeze", help="reviewed local FS-049 tool-freeze JSON")
    parser.add_argument("--max-execute-cost", default="25", help="finite numeric cap; freeze expected_cost must not exceed it")
    parser.add_argument("--output-dir", help="new, non-repository directory; omitted uses mktemp for --run")
    parser.add_argument("--tool-id", help="exact QVeris tool ID to freeze with --discover-b-freeze")
    parser.add_argument("--freeze-output", help="new system-temporary JSON path for --discover-b-freeze")
    args = parser.parse_args(argv)
    try:
        if args.discover_b_freeze:
            if args.freeze_output is None:
                raise PreflightError("freeze_output is required for B freeze discovery")
            destination = discover_b_freeze(args.tool_id, args.max_execute_cost, args.freeze_output)
            print(json.dumps({"status": "b_freeze_discovered", "classification": "exploratory_not_official_no_ranking", "case_id": CASE_ID, "tool_freeze_path": str(destination)}, ensure_ascii=False))
            return 0
        budget, static_checks = _static_preflight(args.max_execute_cost)
        if args.probe_gateway_only:
            _require_env("QVERIS_API_KEY")
            case, metadata = _case_and_metadata()
            manifest = _gateway_probe_manifest(case, metadata)
            output_root = _output_root(args.output_dir)
            snapshot = run_gateway_probe(case, metadata, manifest, output_root)
            print(json.dumps({"status": snapshot["internal_status"], "projection_status": snapshot["projection_status"], "classification": "diagnostic_nonranking", "scoring_status": "UNSCORED", "run_id": manifest["run_id"], "output_dir": str(output_root)}, ensure_ascii=False))
            return 0 if snapshot["execution"]["failed"] == 0 else 1
        if args.tool_freeze is None:
            if args.run or args.run_b:
                raise PreflightError("tool_freeze is required for Runner execution")
            print(json.dumps({"status": "needs_tool_freeze", "classification": "exploratory_not_official_no_ranking", "case_id": CASE_ID, "model_id": MODEL_ID, "static_checks": static_checks}, ensure_ascii=False))
            return 0
        case, metadata, freeze, selector, _freeze_window, budget, manifest = preflight(args.tool_freeze, args.max_execute_cost, require_tavily=args.run, b_only=args.run_b)
        if not (args.run or args.run_b):
            print(json.dumps({"status": "ready", "classification": "exploratory_not_official_no_ranking", "case_id": case["case_id"], "model_id": MODEL_ID, "variants": [item["variant_id"] for item in manifest["variants"]], "static_checks": static_checks}, ensure_ascii=False))
            return 0
        output_root = _output_root(args.output_dir)
        snapshot = run(case, metadata, freeze, selector, budget, manifest, output_root)
        print(json.dumps({"status": snapshot["internal_status"], "projection_status": snapshot["projection_status"], "classification": "exploratory_not_official_no_ranking", "run_id": manifest["run_id"], "output_dir": str(output_root)}, ensure_ascii=False))
        return 0 if snapshot["execution"]["failed"] == 0 else 1
    except PreflightError as exc:
        print(json.dumps({"status": "preflight_failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

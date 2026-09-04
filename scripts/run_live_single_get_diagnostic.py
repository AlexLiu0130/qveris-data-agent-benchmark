#!/usr/bin/env python3
"""Run one unscored public-GET diagnostic from the frozen public case input.

Only ``--run`` performs I/O: it lists the configured Gateway model once, then
uses the existing one-agent/one-GET adapter.  It never loads an Oracle or a
Scorer, and writes the RunStore outside this checkout.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qveris_benchmark.live_get_client import QVerisPublicGetConfig, build_qveris_public_get_client
from qveris_benchmark.qveris_tool_gateway import ToolCreditReceipt
from qveris_benchmark.run_backend import RunService, RunStore, _digest


DEFAULT_CASE_ID = "RTQ-025"
DEFAULT_RUNTIME_CASE = ROOT / "benchmarks" / "runtime" / "public" / "single-get-diagnostic.v1.json"
_PUBLIC_CASE_FIELDS = frozenset({"case_id", "suite", "query"})
_TOOL_RECEIPT_NAME = "tool-receipt.json"


class _PrivateToolReceiptSink:
    """Persist the one post-call Tool cost observation outside public artifacts."""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._written = False

    def bind(self, run_directory: Path) -> None:
        if self._path is not None or self._written:
            raise DiagnosticError("private Tool receipt sink is already bound")
        self._path = run_directory / _TOOL_RECEIPT_NAME
        if self._path.exists():
            raise DiagnosticError("private Tool receipt already exists")

    def __call__(self, receipt: ToolCreditReceipt) -> None:
        if self._path is None or self._written:
            raise DiagnosticError("private Tool receipt is invalid")
        if (type(receipt.tool_id) is not str or not receipt.tool_id or type(receipt.request_id) is not str or not receipt.request_id
                or receipt.execution_id is not None and (type(receipt.execution_id) is not str or not receipt.execution_id)
                or receipt.actual_credits is not None and (type(receipt.actual_credits) not in {int, float} or isinstance(receipt.actual_credits, bool) or receipt.actual_credits < 0)):
            raise DiagnosticError("private Tool receipt is invalid")
        value = {
            "schema_version": "single-get-tool-receipt/v1",
            "tool_id": receipt.tool_id,
            "request_id_sha256": sha256(receipt.request_id.encode("utf-8")).hexdigest(),
            "execution_id_sha256": None if receipt.execution_id is None else sha256(receipt.execution_id.encode("utf-8")).hexdigest(),
            # This is an observed post-call receipt, never a pre-dispatch cap.
            "actual_credits": receipt.actual_credits,
        }
        encoded = _canonical(value)
        temporary = self._path.with_name("." + self._path.name + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DiagnosticError("private Tool receipt could not be recorded") from exc
        self._written = True


class DiagnosticError(ValueError):
    """Safe operator error; it deliberately contains no supplier body or key."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")


def _public_case(runtime_case: Path, case_id: str) -> dict[str, str]:
    try:
        info = runtime_case.lstat()
        raw = runtime_case.read_bytes()
    except OSError as exc:
        raise DiagnosticError("runtime case asset is unreadable") from exc
    if not stat.S_ISREG(info.st_mode) or len(raw) > 4 * 1024 * 1024:
        raise DiagnosticError("runtime case asset is invalid")
    try:
        selected = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("runtime case asset is invalid") from exc
    if type(selected) is not dict or type(case_id) is not str or selected.get("case_id") != case_id or set(selected) != _PUBLIC_CASE_FIELDS:
        raise DiagnosticError("runtime case asset must contain only the selected public case")
    if selected["suite"] not in {"realtime_quote", "historical_price", "financial_statements"} or any(type(selected[name]) is not str or not selected[name].strip() for name in _PUBLIC_CASE_FIELDS):
        raise DiagnosticError("runtime case public fields are invalid")
    return {name: selected[name] for name in _PUBLIC_CASE_FIELDS}


def _manifest(case: Mapping[str, str], identity: Mapping[str, str]) -> dict[str, Any]:
    public_case = {name: case[name] for name in sorted(_PUBLIC_CASE_FIELDS)}
    case_digest = _digest({"schema_version": "single-get-diagnostic-case/v1", "case": public_case})
    run_id = "single-get-diagnostic-" + case["case_id"].lower() + "-" + case_digest[:16]
    item = {"case_id": case["case_id"], "suite": case["suite"], "query": case["query"]}
    return {
        "schema_version": "runner-run-manifest/v2",
        "run_id": run_id,
        "mode": "diagnostic",
        "execution_profile": "diagnostic_public_get",
        "freeze_digest": case_digest,
        "policy": {"version": "single-get-diagnostic/v1", "scope": "unscored_nonranking"},
        "timeout_ms": 45_000,
        "concurrency": 1,
        "variants": [{"variant_id": "qveris-public-get-live", "stable_display_order": 1, **dict(identity)}],
        "cases": [item],
    }


def _preflight(client: Any, identity: Mapping[str, str], run_id: str) -> dict[str, Any]:
    resolver = getattr(client, "semantic_resolver", None)
    preflight = getattr(resolver, "preflight_models", None)
    if not callable(preflight):
        raise DiagnosticError("public GET client does not expose model preflight")
    try:
        result = preflight(request_id="preflight-" + _digest(run_id)[:48])
        configured, available = result.configured_model, result.available_model_ids
    except Exception as exc:
        raise DiagnosticError("Gateway model preflight failed") from exc
    if configured != identity["model_identifier"] or type(available) is not tuple or any(type(model) is not str or not model for model in available):
        raise DiagnosticError("Gateway model preflight is invalid")
    if configured not in available:
        raise DiagnosticError("configured Gateway model is unavailable")
    return {"schema_version": "single-get-model-preflight/v1", "model_id": configured, "model_catalog_sha256": _digest({"model_ids": list(available)}), "model_available": True}


def _output_root(value: Path | None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="qveris-single-get-")) if value is None else value.expanduser().resolve()
    if root == ROOT or ROOT in root.parents:
        raise DiagnosticError("output must be outside this checkout")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not root.is_dir():
        raise DiagnosticError("output is not a directory")
    os.chmod(root, 0o700)
    return root


def run_once(
    *,
    runtime_case: Path = DEFAULT_RUNTIME_CASE,
    case_id: str = DEFAULT_CASE_ID,
    output: Path | None = None,
    config: QVerisPublicGetConfig | None = None,
    client_builder: Any = build_qveris_public_get_client,
) -> dict[str, Any]:
    """Run exactly one case after preflight; dependencies are injectable for tests."""
    case = _public_case(runtime_case, case_id)
    live_config = config or QVerisPublicGetConfig.from_environment()
    identity = live_config.identity()
    manifest = _manifest(case, identity)
    tool_receipt_sink = _PrivateToolReceiptSink()
    client = client_builder(live_config, tool_receipt_sink=tool_receipt_sink)
    preflight = _preflight(client, identity, manifest["run_id"])
    root = _output_root(output)
    service = RunService(RunStore(root), {manifest["variants"][0]["variant_id"]: client})
    service.create_run(manifest)
    tool_receipt_sink.bind(root / manifest["run_id"])
    snapshot = service.execute(manifest["run_id"])
    if snapshot["projection_status"] != "UNSCORED" or any(value != "UNSCORED" for key, value in snapshot["scoring"].items() if key in {"semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage"}):
        raise DiagnosticError("single GET diagnostic must remain unscored")
    return {
        "run_id": manifest["run_id"],
        "case_id": case["case_id"],
        "output": str(root / manifest["run_id"]),
        "internal_status": snapshot["internal_status"],
        "projection_status": "UNSCORED",
        "ranking": "non-ranking",
        "model_preflight": preflight,
        "private_tool_receipt_written": tool_receipt_sink._written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real, unscored QVeris public GET diagnostic.")
    parser.add_argument("--case-id", default=DEFAULT_CASE_ID)
    parser.add_argument("--output", type=Path, help="external private RunStore directory")
    parser.add_argument("--run", action="store_true", help="perform one model preflight and one public GET")
    args = parser.parse_args()
    try:
        case = _public_case(DEFAULT_RUNTIME_CASE, args.case_id)
        if not args.run:
            print(json.dumps({"case_id": case["case_id"], "status": "ready_to_run", "ranking": "non-ranking", "scoring": "UNSCORED"}, ensure_ascii=False, sort_keys=True))
            return 0
        print(json.dumps(run_once(runtime_case=DEFAULT_RUNTIME_CASE, case_id=args.case_id, output=args.output), ensure_ascii=False, sort_keys=True))
        return 0
    except (DiagnosticError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

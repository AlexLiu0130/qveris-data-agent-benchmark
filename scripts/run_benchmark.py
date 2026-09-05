#!/usr/bin/env python3
"""Run all frozen v0.3/v3 cases through one explicit public-GET plugin.

The worker process receives only case_id, suite, and query as agent input.
The parent alone compiles and scores with the frozen Oracle bundle.  This is
process/module separation, not a claim of a sandbox against the same account.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import importlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
from tempfile import mkdtemp, TemporaryDirectory
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qveris_benchmark.run_backend import ExecutionEvidence, PublicGetResult, RunService, RunStore, _digest, _variant_contract_digest


FIXTURE = "qveris_benchmark.fixture_get:make_client"
_SANDBOX_PROTOCOL = "sandbox-get-input/v1"
_BROKER_PROTOCOL = "sandbox-http-broker/v1"
_MAX_SANDBOX_LINE_BYTES = 1 << 20
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_DIGEST = re.compile(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}\Z")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("JSON root must be an object")
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _factory(spec: str) -> Mapping[str, Any]:
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise ValueError("--get-client must be module:factory")
    factory = getattr(importlib.import_module(module), name, None)
    if not callable(factory):
        raise ValueError("GET plugin factory is not callable")
    value = factory()
    if not isinstance(value, Mapping) or set(value) != {"variant", "client"} or not isinstance(value["variant"], Mapping) or not callable(value["client"]):
        raise ValueError("GET plugin factory must return {'variant': ..., 'client': PublicGetClient}")
    return value


def _sandbox_descriptor(path: Path) -> dict[str, Any]:
    value = _json(path)
    if set(value) != {"schema_version", "variant"} or value["schema_version"] != "sandbox-get-descriptor/v1" or type(value["variant"]) is not dict:
        raise ValueError("--sandbox-variant must be a sandbox-get-descriptor/v1")
    return dict(value["variant"])


class DockerSandboxClient:
    """One no-network OCI container per public GET request.

    The image talks only JSONL over stdio.  The parent owns the broker, variant
    identity, and Runner invocation record; a container never sees the Oracle,
    repository, host credentials, or a mounted socket.
    """

    def __init__(self, *, image: str, variant: Mapping[str, Any], timeout_ms: int) -> None:
        if type(image) is not str or _IMAGE_DIGEST.fullmatch(image) is None:
            raise ValueError("--sandbox-image must be an immutable image@sha256 digest or local sha256 image ID")
        if type(timeout_ms) is not int or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ValueError("sandbox timeout must be positive")
        self.image, self.variant, self.timeout_ms = image, dict(variant), timeout_ms

    @staticmethod
    def _command(image: str, cidfile: Path) -> list[str]:
        return [
            "docker", "run", "--interactive", "--pull", "never", "--rm", "--cidfile", str(cidfile),
            "--network", "none", "--read-only", "--user", "65534:65534",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--workdir", "/tmp",
            "--env", "HOME=/tmp", "--env", "PATH=/usr/local/bin:/usr/bin:/bin",
            "--env", "PYTHONNOUSERSITE=1", image,
        ]

    @staticmethod
    def _line(stream: Any, buffered: bytes, deadline: float) -> tuple[dict[str, Any], bytes]:
        while b"\n" not in buffered:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
                raise TimeoutError("sandbox protocol timed out")
            chunk = os.read(stream.fileno(), _MAX_SANDBOX_LINE_BYTES + 1 - len(buffered))
            if not chunk:
                raise ValueError("sandbox protocol output is invalid")
            buffered += chunk
            if len(buffered) > _MAX_SANDBOX_LINE_BYTES:
                raise ValueError("sandbox protocol output is invalid")
        raw, buffered = buffered.split(b"\n", 1)
        if len(raw) > _MAX_SANDBOX_LINE_BYTES:
            raise ValueError("sandbox protocol output is invalid")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("sandbox protocol output is invalid") from exc
        if type(value) is not dict:
            raise ValueError("sandbox protocol output is invalid")
        return value, buffered

    @staticmethod
    def _write(stream: Any, value: Mapping[str, Any]) -> None:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        stream.flush()

    @staticmethod
    def _cleanup(process: subprocess.Popen[bytes] | None, cidfile: Path) -> None:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
        try:
            container_id = cidfile.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return
        if _CONTAINER_ID.fullmatch(container_id):
            subprocess.run(["docker", "rm", "--force", container_id], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def __call__(self, query: str, *, request_id: str, idempotency_key: str) -> PublicGetResult:
        del idempotency_key
        from qveris_benchmark.sandbox_broker import SandboxBroker

        broker = SandboxBroker.from_environment(request_id, query=query, model_identifier=self.variant["model_identifier"])
        process: subprocess.Popen[bytes] | None = None
        with TemporaryDirectory(prefix="qveris-sandbox-") as directory:
            cidfile = Path(directory) / "container-id"
            try:
                process = subprocess.Popen(self._command(self.image, cidfile), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
                if process.stdin is None or process.stdout is None:
                    raise ValueError("sandbox stdio is unavailable")
                self._write(process.stdin, {"protocol_version": _SANDBOX_PROTOCOL, "request_id": request_id, "query": query})
                response: dict[str, Any] | None = None
                buffered, deadline = b"", time.monotonic() + self.timeout_ms / 1000
                while response is None:
                    message, buffered = self._line(process.stdout, buffered, deadline)
                    if message.get("schema_version") == _BROKER_PROTOCOL:
                        self._write(process.stdin, broker.reply(message))
                    elif message.get("schema_version") == "get-response/v1":
                        response = message
                    else:
                        raise ValueError("sandbox protocol output is invalid")
                process.stdin.close()
                try:
                    exit_code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise TimeoutError("sandbox protocol timed out") from exc
                if exit_code != 0:
                    raise ValueError("sandbox execution failed")
                if buffered or process.stdout.read(1):
                    raise ValueError("sandbox emitted more than one public response")
            finally:
                self._cleanup(process, cidfile)
        observed = broker.observations()
        model_calls, tool_calls = observed["model_dispatches"], observed["tool_dispatches"]
        if model_calls > 1 or tool_calls > 1 or tool_calls and model_calls != 1:
            raise ValueError("sandbox exceeded the one-agent/one-GET broker contract")
        terminal_reason = response.get("terminal_reason")
        no_tool = response.get("status") in {"needs_clarification", "unsupported"} or (
            response.get("status") == "error" and type(terminal_reason) is str and terminal_reason.startswith("semantic_")
        )
        if tool_calls != (0 if no_tool else 1):
            raise ValueError("sandbox response does not match host-observed GET dispatches")
        # Parent observes one container invocation, one final JSON response, and
        # broker dispatches.  It does not attest hidden image internals.
        evidence = ExecutionEvidence(
            **{name: self.variant[name] for name in self.variant if name not in {"variant_id", "stable_display_order"}},
            agent_invocations=1,
            tool_executions=tool_calls,
            structured_outputs=1,
            tools_used=("get",) if tool_calls else (),
            assurance="host_observed_sandbox",
        )
        return PublicGetResult(response, evidence)


def _public_cases(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{name: case[name] for name in ("case_id", "suite", "query")} for case in manifest["cases"]]


def _runtime(args: argparse.Namespace) -> int:
    manifest, public_input = _json(Path(args.runtime_manifest)), _json(Path(args.public_input))
    public_cases = public_input.get("cases")
    if public_input.get("schema_version") != "benchmark-public-runtime-input/v1" or not isinstance(public_cases, list) or public_cases != _public_cases(manifest):
        raise ValueError("runtime input is not the exact public case projection")
    plugin = _factory(args.get_client)
    variant = plugin["variant"]
    if manifest.get("variants") != [variant]:
        raise ValueError("runtime plugin identity does not bind to the public manifest")
    service = RunService(RunStore(args.run_store), {variant["variant_id"]: plugin["client"]})
    service.create_run(manifest)
    snapshot = service.execute(manifest["run_id"])
    events = service.get_events(manifest["run_id"])
    terminals = [event for event in events if event["event_type"] == "terminal"]
    print(json.dumps({"run_id": snapshot["run_id"], "calls": len(terminals)}, sort_keys=True))
    return 0


def _run(args: argparse.Namespace) -> int:
    # Keep scorer/compiler parent-side: the worker imports only Runner + plugin.
    from qveris_benchmark.benchmark_scorer import BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION
    from qveris_benchmark.v2_compiler import compile_v2

    output = Path(args.output_dir).resolve()
    if ROOT == output or ROOT in output.parents:
        raise ValueError("--output-dir must be outside this repository")
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output-dir must be empty")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    compiled = Path(mkdtemp(prefix="compiled-", dir=output))
    result = compile_v2(
        ROOT / "benchmarks",
        compiled,
        run_id=args.run_id,
        mode="diagnostic",
        candidate_revision="v0.3",
        oracle_revision="v3",
    )
    template, bundle = _json(result["run_manifest"]), _json(result["oracle_bundle"])
    policy = _json(ROOT / "benchmarks/oracles/v2/runner-score-policy.v2.json")
    sandboxed = args.sandbox_image is not None
    if sandboxed:
        if not args.sandbox_variant:
            raise ValueError("--sandbox-image requires --sandbox-variant")
        variant = _sandbox_descriptor(Path(args.sandbox_variant))
        client: Any = DockerSandboxClient(image=args.sandbox_image, variant=variant, timeout_ms=template["timeout_ms"])
    else:
        plugin = _factory(FIXTURE if args.fixture else args.get_client)
        variant = dict(plugin["variant"])
        client = plugin["client"]
    manifest = dict(template)
    manifest.update({
        "schema_version": "runner-run-manifest/v2",
        "compile_status": "diagnostic_nonranking_without_realtime_reference",
        "variants": [variant],
        "scoring_contract": {
            "policy_digest": _digest(policy),
            "oracle_bundle_digest": _digest(bundle),
            "scorer_version": SCORER_VERSION,
            "scorer_digest": SCORER_DIGEST,
            "variant_contract_digest": _variant_contract_digest([variant]),
        },
    })
    runtime_manifest = output / "runtime-manifest.v2.json"
    public_input = output / "public-runtime-input.v1.json"
    run_store = output / "run-store"
    _write(runtime_manifest, manifest)
    _write(public_input, {"schema_version": "benchmark-public-runtime-input/v1", "cases": _public_cases(manifest)})
    if sandboxed:
        # The untrusted image gets only stdin JSONL from DockerSandboxClient.
        # Oracle, score_case, manifest, and run-store paths stay parent-side.
        service = RunService(RunStore(run_store), {variant["variant_id"]: client})
        service.create_run(manifest)
        service.execute(manifest["run_id"])
    else:
        client_spec = FIXTURE if args.fixture else args.get_client
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_runtime", "--runtime-manifest", str(runtime_manifest), "--public-input", str(public_input), "--run-store", str(run_store), "--get-client", client_spec], check=True, capture_output=True, text=True)
    store = RunStore(run_store)
    events = store.events(args.run_id)
    dispatch = [event for event in events if event["event_type"] == "dispatch_intent"]
    terminal = [event for event in events if event["event_type"] == "terminal"]
    if len(dispatch) != 300 or len(terminal) != 300:
        raise RuntimeError("300-case runner did not dispatch and terminalize every case exactly once")
    case_suite = {case["case_id"]: case["suite"] for case in manifest["cases"]}
    suite_calls = {suite: sum(case_suite[event["case_id"]] == suite for event in dispatch) for suite in ("financial_statements", "historical_price", "realtime_quote")}
    if suite_calls != {suite: 100 for suite in suite_calls}:
        raise RuntimeError("runner did not execute exactly 100 cases in every suite")
    scorer = BenchmarkScorer(store, policy=policy, oracle_bundle=bundle, approved_policy_digests={_digest(policy)}, approved_oracle_bundle_digests={_digest(bundle)})
    projection = scorer.score(args.run_id)
    report = {
        "schema_version": "benchmark-300-run-report/v1",
        "run_id": args.run_id,
        "mode": "diagnostic_nonranking",
        "fixture": bool(args.fixture),
        "sandbox": {
            "enabled": sandboxed,
            "network": "none" if sandboxed else None,
            "internal_execution_evidence": "unverified" if sandboxed else "trusted_local_adapter",
        },
        "calls": len(dispatch),
        "suite_calls": suite_calls,
        "metrics": projection["variants"][0]["metrics"],
        "data_accuracy_status_by_suite": {"realtime_quote": {"value": "not_scored", "reason": "runtime_reference_contract_unavailable"}},
        "runtime_boundary": "sandbox mode sends only request_id and query over container stdio; Oracle bundle and scorer remain parent-side. Network is denied unless a future host-owned broker is configured.",
    }
    _write(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the fixed 3x100 public-GET benchmark.")
    parser.add_argument("--output-dir", required=False)
    parser.add_argument("--run-id", default="benchmark-300-diagnostic")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--fixture", action="store_true", help="explicit offline fixture; never a model score")
    choice.add_argument("--get-client", help="explicit plugin factory: module:factory")
    choice.add_argument("--sandbox-image", help="one-shot OCI GET image; runs with no direct network or host mounts")
    parser.add_argument("--sandbox-variant", help="parent-side sandbox-get-descriptor/v1 with frozen variant identity")
    parser.add_argument("--_runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-manifest", help=argparse.SUPPRESS)
    parser.add_argument("--public-input", help=argparse.SUPPRESS)
    parser.add_argument("--run-store", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._runtime:
        if not all((args.runtime_manifest, args.public_input, args.run_store, args.get_client)):
            parser.error("runtime arguments are incomplete")
        return _runtime(args)
    if not args.output_dir or sum(item is not None for item in (args.get_client, args.sandbox_image)) + int(args.fixture) != 1:
        parser.error("provide exactly one of --fixture, --get-client, or --sandbox-image and an external --output-dir")
    if args.sandbox_variant and not args.sandbox_image:
        parser.error("--sandbox-variant requires --sandbox-image")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())

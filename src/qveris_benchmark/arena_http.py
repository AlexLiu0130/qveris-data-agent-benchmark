"""Local, read-only HTTP projection for benchmark Arena runs."""

from __future__ import annotations

import ipaddress
import json
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlsplit


_DEFAULT_SCHEMA_VERSION = "arena-read/v1"
_ID_RE = re.compile(r"^[^/\\\x00-\x1f?#]{1,256}$")
_SENSITIVE_EXACT = frozenset({
    "token", "api_key", "password", "cookie", "raw_response", "raw_usage",
    "oracle", "prompt", "tool_params", "tool_parameters", "provider_payload",
    "internal_trace", "credential", "credentials", "private_key", "idempotency_key",
    "header", "headers", "authorization",
})
_VARIANT_FIELDS = frozenset({
    "variant_id", "stable_display_order", "suites", "metrics", "case_pass_rate",
    "semantic_oracle_coverage", "oracle_coverage", "receipt_coverage",
    "completeness_reasons", "eligibility", "ineligible_reason", "rank",
})
_EVENT_DATA_FIELDS = frozenset({
    "run_id", "variant_id", "case_id", "trial", "status", "elapsed_ms", "transport_status",
    "comparability", "error_class", "reference", "reason_code", "event_id", "emitted_at",
    "projection_status", "projection_hash",
})
_SNAPSHOT_FIELDS = frozenset({
    "schema_version", "run_id", "manifest_hash", "status", "variants", "cells",
    "execution", "scoring", "updated_at",
    "snapshot_sequence", "event_cursor", "connection_basis", "projection_status", "projection_reason", "internal_status",
    "ranked_results", "ineligible_results", "public_failure_summaries", "receipt_basis",
})
_RUN_FIELDS = frozenset({
    "schema_version", "run_id", "manifest_hash", "status", "updated_at",
    "snapshot_sequence", "event_cursor", "connection_basis", "projection_status", "projection_reason", "internal_status",
})
_CELL_FIELDS = frozenset({"variant_id", "case_id", "trial", "state"})
_EXECUTION_FIELDS = frozenset({"total", "completed", "success", "failed", "incomplete", "blocked"})
_SCORING_FIELDS = frozenset({
    "semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage", "coverage", "rank", "eligibility",
})
_SUITE_FIELDS = frozenset({"completed", "total", "success", "failed", "incomplete", "blocked"})
_METRIC_FIELDS = frozenset({
    "semantic_accuracy", "data_accuracy", "end_to_end_latency", "token_usage",
})
_RATIO_FIELDS = frozenset({"available", "denominator", "value"})
_CASE_PASS_FIELDS = frozenset({"passed", "denominator", "value"})
_SEMANTIC_FIELDS = frozenset({"passed", "denominator", "value"})
_DATA_ACCURACY_FIELDS = frozenset({"passed_weight", "eligible_weight", "value"})
_LATENCY_FIELDS = frozenset({"count", "raw_count", "p50_ms", "p95_ms", "max_ms", "timeout_rate"})
_TOKEN_USAGE_FIELDS = frozenset({
    "count", "receipt_coverage", "input_mean", "input_p50", "input_p95",
    "output_mean", "output_p50", "output_p95", "total_mean", "total_p50", "total_p95",
})
_RANKED_RESULT_FIELDS = frozenset({"variant_id", "rank"})
_INELIGIBLE_RESULT_FIELDS = frozenset({"variant_id", "reason"})
_REFERENCE_FIELDS = frozenset({"hash", "as_of", "source", "comparability"})
_EVENT_FIELDS = frozenset({
    "sequence", "seq", "event", "event_type", "type", "data", "payload", "manifest_hash", "run_id",
    "cell_id", "attempt_id", "variant_id", "case_id", "trial", "input_hash", "request_hash",
    "reference", "elapsed_ms", "transport_status", "usage", "comparability", "response_hash",
    "error_class", "public_response", "status", "reason_code", "call_completed", "result_status",
    "transport_completed", "execution_outcome", "usage_source", "previous_event_hash", "event_hash", "event_id", "emitted_at",
    "projection_status", "projection_hash", "variant_identity", "execution_evidence", "execution_profile", "gateway_receipt",
    "external_receipts", "external_action_occurred",
})


class ProjectionError(ValueError):
    """Raised when durable state is not safe to expose publicly."""


def _canonical_key(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).replace("-", "_").lower()


def _is_sensitive_key(key: str) -> bool:
    key = _canonical_key(key)
    return (
        key in _SENSITIVE_EXACT
        or "authorization" in key
        or "secret" in key
        or key.endswith("_token")
        or "api_key" in key
        or "password" in key
        or "cookie" in key
        or "raw_response" in key
        or ("oracle" in key and key not in {"semantic_oracle_coverage", "oracle_coverage"})
        or "prompt" in key
        or "tool_params" in key
        or "tool_parameters" in key
        or "provider_payload" in key
        or "internal_trace" in key
    )


def _public(value: Any) -> Any:
    """Validate JSON data without silently redacting a dangerous projection."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str) or _is_sensitive_key(key):
                raise ProjectionError("unsafe public projection")
            result[key] = _public(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    raise ProjectionError("public projection is not JSON data")


def _known_fields(value: Mapping[str, Any], allowed: frozenset[str], name: str) -> None:
    if not set(value).issubset(allowed):
        raise ProjectionError("unknown %s field" % name)


def _objects(value: Any, fields: frozenset[str], name: str) -> None:
    if not isinstance(value, list):
        raise ProjectionError("%s is not a list" % name)
    for item in value:
        if not isinstance(item, Mapping):
            raise ProjectionError("%s item is not an object" % name)
        _known_fields(item, fields, name)


def _score_metric(metric: Mapping[str, Any]) -> None:
    _known_fields(metric, _METRIC_FIELDS, "metric")
    schemas = {
        "semantic_accuracy": _SEMANTIC_FIELDS,
        "data_accuracy": _DATA_ACCURACY_FIELDS,
        "end_to_end_latency": _LATENCY_FIELDS,
        "token_usage": _TOKEN_USAGE_FIELDS,
    }
    for name, fields in schemas.items():
        if name in metric:
            value = metric[name]
            if not isinstance(value, Mapping):
                raise ProjectionError("%s metric is not an object" % name)
            _known_fields(value, fields, name)


def _snapshot_public(value: Mapping[str, Any]) -> Mapping[str, Any]:
    _known_fields(value, _SNAPSHOT_FIELDS, "snapshot")
    variants = value.get("variants")
    if not isinstance(variants, list):
        raise ProjectionError("snapshot has no variants list")
    for variant in variants:
        if not isinstance(variant, Mapping):
            raise ProjectionError("snapshot variant is not an object")
        _known_fields(variant, _VARIANT_FIELDS, "variant")
        if "suites" in variant:
            suites = variant["suites"]
            if not isinstance(suites, Mapping):
                raise ProjectionError("variant suites are not an object")
            for suite in suites.values():
                if not isinstance(suite, Mapping):
                    raise ProjectionError("variant suite is not an object")
                _known_fields(suite, _SUITE_FIELDS, "suite")
        if "metrics" in variant:
            metrics = variant["metrics"]
            if not isinstance(metrics, Mapping):
                raise ProjectionError("variant metrics are not an object")
            _score_metric(metrics)
        for key in ("case_pass_rate", "semantic_oracle_coverage", "oracle_coverage", "receipt_coverage"):
            if key in variant:
                if not isinstance(variant[key], Mapping):
                    raise ProjectionError("variant %s is not an object" % key)
                _known_fields(variant[key], _CASE_PASS_FIELDS if key == "case_pass_rate" else _RATIO_FIELDS, key)
        if "completeness_reasons" in variant and (not isinstance(variant["completeness_reasons"], list) or not all(isinstance(reason, str) for reason in variant["completeness_reasons"])):
            raise ProjectionError("variant completeness reasons are invalid")
    if "cells" in value:
        cells = value["cells"]
        if not isinstance(cells, list):
            raise ProjectionError("snapshot cells are not a list")
        for cell in cells:
            if not isinstance(cell, Mapping):
                raise ProjectionError("snapshot cell is not an object")
            _known_fields(cell, _CELL_FIELDS, "cell")
    for key, allowed, name in (("execution", _EXECUTION_FIELDS, "execution"), ("scoring", _SCORING_FIELDS, "scoring"), ("run", _RUN_FIELDS, "run")):
        if key in value:
            if not isinstance(value[key], Mapping):
                raise ProjectionError("snapshot %s is not an object" % key)
            _known_fields(value[key], allowed, name)
    if "ranked_results" in value:
        _objects(value["ranked_results"], _RANKED_RESULT_FIELDS, "ranked result")
    if "ineligible_results" in value:
        _objects(value["ineligible_results"], _INELIGIBLE_RESULT_FIELDS, "ineligible result")
    if "public_failure_summaries" in value and (not isinstance(value["public_failure_summaries"], list) or not all(isinstance(code, str) for code in value["public_failure_summaries"])):
        raise ProjectionError("public failure summaries are invalid")
    _public(value)
    return value


def _runs_public(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ProjectionError("runs are not a list")
    for run in value:
        if not isinstance(run, Mapping):
            raise ProjectionError("run is not an object")
        _known_fields(run, _RUN_FIELDS, "run")
        _public(run)
    return value


def _run_id(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _after(handler: BaseHTTPRequestHandler, query: Mapping[str, list[str]]) -> int:
    value = handler.headers.get("Last-Event-ID")
    if value is None:
        values = query.get("after", [])
        if len(values) > 1:
            raise ValueError("invalid after")
        value = values[0] if values else "0"
    if not value.isascii() or not value.isdecimal():
        raise ValueError("invalid after")
    return int(value)


def _sequence(value: Mapping[str, Any]) -> int | None:
    for key in ("sequence", "seq"):
        sequence = value.get(key)
        if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence >= 0:
            return sequence
    return None


def _current_sequence(snapshot: Mapping[str, Any], events: list[Mapping[str, Any]]) -> int:
    for key in ("event_cursor", "snapshot_sequence", "last_sequence", "last_seq", "sequence", "seq"):
        value = snapshot.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    run = snapshot.get("run")
    if isinstance(run, Mapping):
        for key in ("event_cursor", "snapshot_sequence", "last_sequence", "last_seq", "sequence", "seq"):
            value = run.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    valid = [sequence for event in events if (sequence := _sequence(event)) is not None]
    return max(valid, default=0)


def _event_parts(event: Mapping[str, Any]) -> tuple[int, str, Any]:
    _known_fields(event, _EVENT_FIELDS, "event")
    sequence = _sequence(event)
    name = event.get("event", event.get("event_type", event.get("type", "message")))
    data = event.get("data", event.get("payload"))
    if data is None:
        data = {key: event[key] for key in _EVENT_DATA_FIELDS if key in event}
    elif not isinstance(data, Mapping) or not set(data).issubset(_EVENT_DATA_FIELDS):
        raise ProjectionError("unknown event data field")
    if sequence is None:
        raise ProjectionError("invalid durable event sequence")
    if "reference" in data:
        reference = data["reference"]
        if not isinstance(reference, Mapping) or not set(reference).issubset(_REFERENCE_FIELDS):
            raise ProjectionError("unknown reference field")
    if not isinstance(name, str) or not name or "\r" in name or "\n" in name:
        raise ProjectionError("invalid durable event name")
    return sequence, name, _public(data)


def _continuous(after: int, events: list[Mapping[str, Any]]) -> bool:
    return all(_sequence(event) == after + index for index, event in enumerate(events, start=1))


def _sse(event: str, data: Any, sequence: int | None = None) -> bytes:
    lines = []
    if sequence is not None:
        lines.append(f"id: {sequence}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _variant_detail(snapshot: Mapping[str, Any], variant_id: str) -> dict[str, Any] | None:
    variants = snapshot.get("variants")
    if not isinstance(variants, list):
        raise ProjectionError("snapshot has no variants list")
    for variant in variants:
        if isinstance(variant, Mapping) and variant.get("variant_id") == variant_id:
            return {key: variant[key] for key in _VARIANT_FIELDS if key in variant}
    return None


def make_server(store: Any, host: str = "127.0.0.1", port: int = 0, allowed_origin: str | None = None, heartbeat_interval: float = 1.0) -> ThreadingHTTPServer:
    """Build a local read-only Arena server; callers own its lifecycle."""
    if allowed_origin is not None and not isinstance(allowed_origin, str):
        raise ValueError("allowed_origin must be a string or None")
    if not isinstance(heartbeat_interval, (int, float)) or isinstance(heartbeat_interval, bool) or heartbeat_interval <= 0:
        raise ValueError("heartbeat_interval must be positive")
    if not isinstance(host, str) or host.lower() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("unauthenticated Arena server must bind to loopback")
        except ValueError as exc:
            if str(exc) == "unauthenticated Arena server must bind to loopback":
                raise
            raise ValueError("unauthenticated Arena server must bind to loopback") from exc

    class ArenaHandler(BaseHTTPRequestHandler):
        server_version = "QVerisArena/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _cors(self) -> None:
            if allowed_origin is not None and self.headers.get("Origin") == allowed_origin:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
                self.send_header("Vary", "Origin")

        def _json(self, status: HTTPStatus, payload: Any) -> None:
            try:
                body = json.dumps(_public(payload), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            except ProjectionError:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "unsafe_projection")
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _json_error(self, status: HTTPStatus, error: str, allow: str | None = None) -> None:
            body = json.dumps({"error": error}, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if allow is not None:
                self.send_header("Allow", allow)
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _snapshot(self, run_id: str) -> Mapping[str, Any] | None:
            try:
                value = store.get_snapshot(run_id)
            except (KeyError, FileNotFoundError):
                return None
            except ValueError as exc:
                if str(exc) == "unknown run_id":
                    return None
                raise
            if value is None:
                return None
            if not isinstance(value, Mapping):
                raise ProjectionError("snapshot is not an object")
            return _snapshot_public(value)

        def _events(self, run_id: str, after: int) -> list[Mapping[str, Any]] | None:
            try:
                events = store.get_events(run_id, after_sequence=after)
            except (KeyError, FileNotFoundError):
                return None
            except ValueError as exc:
                if str(exc) == "unknown run_id":
                    return None
                raise
            if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
                raise ProjectionError("events are not a list")
            for event in events:
                _event_parts(event)
            return events

        def _stream_headers(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()

        def _stream_once(self, chunks: list[bytes]) -> None:
            self._stream_headers()
            for chunk in chunks:
                self.wfile.write(chunk)
            self.wfile.flush()

        def _stream_events(self, run_id: str, snapshot: Mapping[str, Any], after: int) -> None:
            initial = after == 0
            cursor = _current_sequence(snapshot, []) if initial else after
            try:
                self._stream_headers()
                if initial:
                    snapshot_payload = dict(snapshot)
                    snapshot_payload["snapshot_sequence"] = cursor
                    self.wfile.write(_sse("snapshot", _public(snapshot_payload), cursor))
                    self.wfile.flush()
                while True:
                    events = self._events(run_id, cursor)
                    if events is None or not _continuous(cursor, events):
                        self.wfile.write(_sse("resync_required", {"snapshot_url": f"/v1/arena/runs/{quote(run_id, safe='')}/snapshot"}))
                        self.wfile.flush()
                        return
                    if events:
                        for event in events:
                            sequence, name, data = _event_parts(event)
                            self.wfile.write(_sse(name, data, sequence))
                            cursor = sequence
                        self.wfile.flush()
                        refreshed = self._snapshot(run_id)
                        if refreshed is not None and refreshed.get("status") in {"failed", "incomplete"}:
                            return
                        continue
                    refreshed = self._snapshot(run_id)
                    if refreshed is None or refreshed.get("status") in {"failed", "incomplete"}:
                        return
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    time.sleep(heartbeat_interval)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            parts = [part for part in parsed.path.split("/") if part]
            try:
                if parts == ["v1", "arena", "runs"]:
                    runs = _runs_public(store.list_runs())
                    self._json(HTTPStatus.OK, {"schema_version": _DEFAULT_SCHEMA_VERSION, "runs": runs})
                    return
                if len(parts) >= 4 and parts[:3] == ["v1", "arena", "runs"]:
                    run_id = _run_id(parts[3])
                    snapshot = self._snapshot(run_id)
                    if snapshot is None:
                        self._json_error(HTTPStatus.NOT_FOUND, "not_found")
                        return
                    if len(parts) == 5 and parts[4] == "snapshot":
                        self._json(HTTPStatus.OK, snapshot)
                        return
                    if len(parts) == 6 and parts[4] == "variants":
                        variant = _variant_detail(snapshot, _run_id(parts[5]))
                        if variant is None:
                            self._json_error(HTTPStatus.NOT_FOUND, "not_found")
                            return
                        self._json(HTTPStatus.OK, {
                            "schema_version": snapshot.get("schema_version", _DEFAULT_SCHEMA_VERSION),
                            "run_id": run_id,
                            "variant": variant,
                        })
                        return
                    if len(parts) == 5 and parts[4] == "events":
                        after = _after(self, query)
                        all_events = self._events(run_id, 0)
                        if all_events is None:
                            self._json_error(HTTPStatus.NOT_FOUND, "not_found")
                            return
                        current = _current_sequence(snapshot, all_events)
                        if after > current:
                            self._stream_once([_sse("resync_required", {
                                "snapshot_url": f"/v1/arena/runs/{quote(run_id, safe='')}/snapshot"
                            })])
                            return
                        events = all_events if after == 0 else self._events(run_id, after)
                        if events is None:
                            self._json_error(HTTPStatus.NOT_FOUND, "not_found")
                            return
                        if after and not _continuous(after, events):
                            self._stream_once([_sse("resync_required", {
                                "snapshot_url": f"/v1/arena/runs/{quote(run_id, safe='')}/snapshot"
                            })])
                            return
                        self._stream_events(run_id, snapshot, after)
                        return
            except ProjectionError:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "unsafe_projection")
                return
            except ValueError:
                self._json_error(HTTPStatus.BAD_REQUEST, "bad_request")
                return
            self._json_error(HTTPStatus.NOT_FOUND, "not_found")

        def do_POST(self) -> None:
            self._json_error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "GET")

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

    server = ThreadingHTTPServer((host, port), ArenaHandler)
    server.daemon_threads = True
    return server


def serve(store: Any, host: str = "127.0.0.1", port: int = 0, allowed_origin: str | None = None, heartbeat_interval: float = 1.0) -> None:
    """Serve until interrupted. This function intentionally starts nothing on import."""
    server = make_server(store, host, port, allowed_origin, heartbeat_interval)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    from .run_backend import RunService, RunStore

    parser = argparse.ArgumentParser(description="Serve local read-only QVeris Arena projections")
    parser.add_argument("--root", required=True, help="private RunStore directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--allowed-origin")
    args = parser.parse_args(argv)
    serve(RunService(RunStore(args.root), clients={}), args.host, args.port, args.allowed_origin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

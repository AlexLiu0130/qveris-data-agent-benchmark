#!/usr/bin/env python3
"""One-shot, redacted QVeris Search/Inspect connectivity audit. Never executes tools."""
import argparse
import datetime as dt
import json
import os
import socket
import ssl
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://qveris.ai/api/v1"
MAX_RESPONSE_BYTES = 1_000_000
SYSTEM_CA_FILE = Path("/etc/ssl/cert.pem")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def build_ssl_context(environ=None):
    environ = os.environ if environ is None else environ
    cafile = environ.get("SSL_CERT_FILE") or (str(SYSTEM_CA_FILE) if SYSTEM_CA_FILE.is_file() else None)
    try:
        context = ssl.create_default_context(cafile=cafile)
    except (OSError, ssl.SSLError) as error:
        raise RuntimeError("verifying_ca_unavailable") from error
    if not context.cert_store_stats().get("x509_ca", 0):
        raise RuntimeError("verifying_ca_unavailable")
    return context


def load_key():
    for line in Path(".env.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("QVERIS_API_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    raise RuntimeError("QVERIS_API_KEY is absent from .env.local")


def network_error(reason):
    if isinstance(reason, socket.gaierror):
        return "dns", "dns_error"
    if isinstance(reason, (ssl.SSLError, ssl.SSLCertVerificationError)):
        return "tls", "tls_error"
    if isinstance(reason, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
        return "tcp", "tcp_error"
    return "network", "network_error"


def post(opener, path, payload, key):
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return None, {"phase": "http", "http_status": response.status, "outcome": "response_too_large"}
            try:
                return json.loads(raw.decode("utf-8")), {"phase": "http", "http_status": response.status, "outcome": "success"}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, {"phase": "http", "http_status": response.status, "outcome": "invalid_json"}
    except urllib.error.HTTPError as error:
        return None, {"phase": "http", "http_status": error.code, "outcome": "http_application_error"}
    except urllib.error.URLError as error:
        phase, outcome = network_error(error.reason)
        return None, {"phase": phase, "outcome": outcome}
    except (TimeoutError, socket.timeout):
        return None, {"phase": "network", "outcome": "timeout"}
    except ssl.SSLError:
        return None, {"phase": "tls", "outcome": "tls_error"}


def response_record(meta, body):
    record = dict(meta)
    if isinstance(body, dict):
        results = body.get("results")
        record["result_count"] = len(results) if isinstance(results, list) else None
        if isinstance(body.get("remaining_credits"), (int, float)) and not isinstance(body["remaining_credits"], bool):
            record["remaining_credits"] = body["remaining_credits"]
    return record


def scalar(value):
    return value if isinstance(value, (str, int, float, bool)) or value is None else None


def tool_summary(tool):
    if not isinstance(tool, dict):
        return None
    summary = {key: scalar(tool.get(key)) for key in ("tool_id", "name", "provider_name", "provider", "expected_cost") if key in tool}
    billing = tool.get("billing_rule")
    if isinstance(billing, dict):
        summary["billing_rule"] = {key: scalar(billing.get(key)) for key in ("amount_credits", "metering_mode") if key in billing}
    params = tool.get("params")
    if isinstance(params, list):
        summary["schema_fields"] = [
            {key: scalar(param.get(key)) for key in ("name", "type", "required", "minimum", "maximum") if key in param}
            for param in params if isinstance(param, dict)
        ]
    return summary


def atomic_write(output, value):
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".connection.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="permit the Search/Inspect requests")
    parser.add_argument("--output", type=Path, required=True, help="redacted audit JSON path")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.live:
        print("Refusing network activity: rerun with --live.")
        return 2
    key = load_key()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=build_ssl_context()))
    audit = {
        "format": "BenchmarkQVerisConnectionAudit.v2",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "endpoint": {"scheme": "https", "host": "qveris.ai", "base_path": "/api/v1"},
        "authentication": {"configured": True, "credential_retained": False},
        "requests": {"execute_path_called": False, "search": None, "inspect": None},
    }
    search_body, search_meta = post(opener, "/search", {"query": "stock quote", "limit": 1}, key)
    search = response_record(search_meta, search_body)
    search["request"] = {"query": "stock quote", "limit": 1}
    audit["requests"]["search"] = search
    results = search_body.get("results") if isinstance(search_body, dict) else None
    candidate = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
    tool_id = candidate.get("tool_id") if isinstance(candidate, dict) else None
    search["business_status"] = "search_results_received" if isinstance(results, list) else "unexpected_response_shape"
    if candidate:
        search["first_candidate"] = tool_summary(candidate)
    if search.get("outcome") == "success" and isinstance(tool_id, str) and tool_id:
        inspect_body, inspect_meta = post(opener, "/tools/by-ids", {"tool_ids": [tool_id]}, key)
        inspect = response_record(inspect_meta, inspect_body)
        inspect["requested_tool_count"] = 1
        inspect_results = inspect_body.get("results") if isinstance(inspect_body, dict) else None
        inspect["business_status"] = "inspect_result_received" if isinstance(inspect_results, list) else "unexpected_response_shape"
        if isinstance(inspect_results, list) and inspect_results and isinstance(inspect_results[0], dict):
            inspect["first_candidate"] = tool_summary(inspect_results[0])
        audit["requests"]["inspect"] = inspect
    else:
        audit["requests"]["inspect"] = {"outcome": "skipped_no_search_candidate"}
    atomic_write(args.output, audit)
    print(json.dumps({"search": search["outcome"], "inspect": audit["requests"]["inspect"]["outcome"], "execute_path_called": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

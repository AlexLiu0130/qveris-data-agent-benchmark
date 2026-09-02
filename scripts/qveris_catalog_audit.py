#!/usr/bin/env python3
"""One-shot, redacted QVeris catalog Search/Inspect audit. Never executes tools."""
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
SENSITIVE_NAMES = ("authorization", "api_key", "apikey", "token", "secret", "password", "cookie", "header")


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


def load_key(path=Path(".env.local")):
    for line in path.read_text(encoding="utf-8").splitlines():
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


def safe_value(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [safe_value(item, depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(name): safe_value(item, depth + 1)
            for name, item in list(value.items())[:50]
            if not any(term in str(name).lower().replace("-", "_") for term in SENSITIVE_NAMES)
        }
    return None


def tool_summary(tool):
    if not isinstance(tool, dict):
        return None
    summary = {
        name: safe_value(tool[name])
        for name in ("tool_id", "name", "provider", "provider_name", "description", "expected_cost")
        if name in tool
    }
    for name in ("billing", "billing_rule"):
        if isinstance(tool.get(name), (dict, list)):
            summary["billing"] = safe_value(tool[name])
            break
    if isinstance(tool.get("params"), list):
        summary["schema"] = {"fields": safe_value(tool["params"])}
    elif isinstance(tool.get("input_schema"), dict):
        summary["schema"] = safe_value(tool["input_schema"])
    elif isinstance(tool.get("schema"), dict):
        summary["schema"] = safe_value(tool["schema"])
    return summary


def response_record(meta, body):
    record = dict(meta)
    if not isinstance(body, dict):
        record["business_status"] = "unexpected_response_shape"
        return record
    results = body.get("results")
    record["result_count"] = len(results) if isinstance(results, list) else None
    remaining = body.get("remaining_credits")
    if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
        record["remaining_credits"] = remaining
    if body.get("success") is False:
        record["business_status"] = "reported_failure"
    elif isinstance(results, list):
        record["business_status"] = "results_received"
    else:
        record["business_status"] = "response_received"
    return record


def result_tools(body):
    results = body.get("results") if isinstance(body, dict) else None
    return [tool for tool in results if isinstance(tool, dict)] if isinstance(results, list) else []


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".catalog.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_audit(domain, queries, limit, inspect_top, key, opener):
    search_records, candidates, seen_ids = [], [], set()
    first_balance = last_balance = None
    for query in queries:
        body, meta = post(opener, "/search", {"query": query, "limit": limit}, key)
        record = response_record(meta, body)
        record["request"] = {"query": query, "limit": limit}
        record["candidates"] = [tool_summary(tool) for tool in result_tools(body)]
        search_records.append(record)
        if "remaining_credits" in record:
            first_balance = record["remaining_credits"] if first_balance is None else first_balance
            last_balance = record["remaining_credits"]
        for tool in result_tools(body):
            tool_id = tool.get("tool_id")
            if isinstance(tool_id, str) and tool_id and tool_id not in seen_ids:
                seen_ids.add(tool_id)
                candidates.append(tool_id)

    requested_ids = candidates[:inspect_top]
    inspect_body = None
    if requested_ids:
        inspect_body, inspect_meta = post(opener, "/tools/by-ids", {"tool_ids": requested_ids}, key)
        inspect_record = response_record(inspect_meta, inspect_body)
        inspect_record["requested_tool_ids"] = requested_ids
        inspect_record["tools"] = [tool_summary(tool) for tool in result_tools(inspect_body)]
        if "remaining_credits" in inspect_record:
            last_balance = inspect_record["remaining_credits"]
    else:
        inspect_record = {"outcome": "skipped_no_candidates", "business_status": "not_attempted", "requested_tool_ids": [], "tools": []}

    return {
        "format": "BenchmarkQVerisCatalogAudit.v1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "domain": domain,
        "endpoint": {"scheme": "https", "host": "qveris.ai", "base_path": "/api/v1"},
        "authentication": {"configured": True, "credential_retained": False},
        "request_count": {"total": len(search_records) + int(bool(requested_ids)), "search": len(search_records), "inspect": int(bool(requested_ids)), "execute": 0},
        "balance": {"before_inspect": first_balance, "after_inspect": last_balance, "semantics": "server-reported balance after first and last permitted request"},
        "requests": {"search": search_records, "inspect": inspect_record, "execute_path_called": False},
        "candidate_count": len(candidates),
        "candidate_tool_ids": candidates,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--query", action="append", required=True, dest="queries")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--inspect-top", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.inspect_top < 0:
        parser.error("--limit must be positive and --inspect-top must be non-negative")
    return args


def main(argv=None):
    args = parse_args(argv)
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=build_ssl_context()))
    audit = run_audit(args.domain, args.queries, args.limit, args.inspect_top, load_key(), opener)
    atomic_write(args.output, audit)
    print(json.dumps({"output": str(args.output), "requests": audit["request_count"], "execute_path_called": False}, sort_keys=True))


if __name__ == "__main__":
    main()

import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.live_get_client import QVerisPublicGetConfig, build_qveris_public_get_client
from qveris_benchmark.run_backend import RunBackendError, RunService, RunStore


class Response:
    status = 200

    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _model_opener(calls):
    def open_(request, timeout):
        calls.append(request)
        semantic = {"schema_version": "public-get.semantic/v1", "request": {
            "kind": "market_quote", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "quote_snapshot",
        }}
        request_id = request.get_header("X-request-id")
        body = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "qveris_billing": {"call_id": request_id, "usage_estimated": False}}
        return Response(json.dumps(body).encode(), {"X-QVeris-Call-ID": request_id})
    return open_


def _tool_opener(calls):
    def open_(request, timeout):
        calls.append(request)
        quote = {"Global Quote": {"01. symbol": "AAPL", "02. open": "1", "03. high": "2", "04. low": "0.5", "05. price": "1.5", "06. volume": "3", "07. latest trading day": "2026-09-04", "08. previous close": "1.2", "09. change": "0.3", "10. change percent": "25%"}}
        return Response(json.dumps({"success": True, "result": {"data": quote}}).encode())
    return open_


def _manifest(configs):
    cases = []
    for suite in ("realtime_quote", "historical_price", "financial_statements"):
        for index in range(100):
            case = {
                "case_id": "%s-%03d" % (suite, index),
                "source_case_id": "CANARY_SOURCE_%s_%03d" % (suite.upper(), index),
                # A user query may legitimately contain this word; it is not a leakage canary.
                "query": "AAPL quote; oracle is a legitimate product name",
                "suite": suite,
                "score_case": {"expected_status": ["success"], "oracle_id": "CANARY_ORACLE_%s_%03d" % (suite.upper(), index), "case_type": "normal"},
            }
            if suite == "realtime_quote":
                case["reference_contract"] = {"source_contract_hash": "b" * 64, "window_rule_version": "window-rule.v1"}
                case["reference_contract_status"] = "CANARY_REFERENCE_%03d" % index
            cases.append(case)
    return {
        "schema_version": "runner-run-manifest/v2", "run_id": "leakage-gate", "mode": "official", "freeze_digest": "a" * 64,
        "policy": {"version": "v1"}, "timeout_ms": 1000, "concurrency": 1,
        "expected_status_counts": {suite: {"success": 100} for suite in ("realtime_quote", "historical_price", "financial_statements")},
        "variants": [{"variant_id": "variant-%d" % index, "stable_display_order": index, **config.identity()} for index, config in enumerate(configs, 1)],
        "cases": cases,
    }


class LiveGetLeakageTests(unittest.TestCase):
    def _configs(self):
        first = QVerisPublicGetConfig("model-key", "tool-key", "public-model", agent_variant_id="agent-a")
        return first, dataclasses.replace(first, agent_variant_id="agent-b")

    def test_formal_get_outbound_and_reference_hook_are_allowlisted(self):
        model_calls, tool_calls, reference_calls = [], [], []
        configs = self._configs()
        clients = {"variant-%d" % index: build_qveris_public_get_client(config, model_opener=_model_opener(model_calls), tool_opener=_tool_opener(tool_calls)) for index, config in enumerate(configs, 1)}

        class Reference:
            source_contract_hash = "b" * 64
            window_rule_version = "window-rule.v1"

            def __call__(self, case, phase):
                reference_calls.append((case, phase))
                return {"source": "independent", "as_of": "2026-09-04T00:00:00Z"}

        with tempfile.TemporaryDirectory() as directory:
            service = RunService(RunStore(directory), clients, reference_hook=Reference())
            manifest = _manifest(configs)
            service.create_run(manifest)
            # One formal cell exercises both real outbound client boundaries;
            # the manifest itself still validates the 3x100 formal shape.
            service._execute_cell(manifest, manifest["variants"][0], manifest["cases"][0])

        self.assertEqual((len(model_calls), len(tool_calls), len(reference_calls)), (1, 1, 2))
        for case, phase in reference_calls:
            self.assertIn(phase, {"before", "after"})
            self.assertEqual(set(case), {"case_id", "suite", "query", "reference_contract"})
            self.assertEqual(set(case["reference_contract"]), {"source_contract_hash", "window_rule_version"})
        canaries = ("CANARY_SOURCE_", "CANARY_ORACLE_", "CANARY_REFERENCE_", "CANARY_EXPECTED_", "CANARY_TOLERANCE_", "CANARY_SCORING_", "CANARY_CANONICAL_")
        for request in model_calls:
            self.assertEqual({key.lower() for key, _ in request.header_items()}, {"authorization", "content-type", "x-request-id", "x-qveris-source"})
            payload = json.loads(request.data)
            self.assertEqual(set(payload), {"model", "stream", "temperature", "max_tokens", "messages"})
            self.assertEqual([item["role"] for item in payload["messages"]], ["system", "user"])
            self.assertEqual(payload["messages"][1]["content"], "AAPL quote; oracle is a legitimate product name")
            wire = json.dumps(payload, sort_keys=True)
            self.assertFalse(any(canary in wire for canary in canaries))
        for request in tool_calls:
            self.assertEqual({key.lower() for key, _ in request.header_items()}, {"accept", "authorization", "content-type", "idempotency-key", "x-request-id"})
            self.assertEqual(set(json.loads(request.data)), {"parameters"})
            url = urlsplit(request.full_url)
            self.assertEqual((url.scheme, url.netloc, url.path, parse_qs(url.query)), ("https", "qveris.ai", "/api/v1/tools/execute", {"tool_id": ["alphavantage.global_quote.retrieve.v1.9b8a7c6d"]}))
            wire = request.full_url + json.dumps(json.loads(request.data), sort_keys=True)
            self.assertFalse(any(canary in wire for canary in canaries))

    def test_malformed_formal_runtime_case_fails_before_client_call(self):
        model_calls, tool_calls = [], []
        configs = self._configs()
        clients = {"variant-%d" % index: build_qveris_public_get_client(config, model_opener=_model_opener(model_calls), tool_opener=_tool_opener(tool_calls)) for index, config in enumerate(configs, 1)}
        manifest = _manifest(configs)
        with tempfile.TemporaryDirectory() as directory:
            service = RunService(RunStore(directory), clients)
            service.create_run(manifest)
            malformed = dict(manifest["cases"][100])
            malformed.update({name: "CANARY_%s_" % name.upper() for name in ("oracle", "expected", "tolerance", "reference", "scoring", "canonical_request")})
            with self.assertRaises(RunBackendError):
                service._execute_cell(manifest, manifest["variants"][0], malformed)
        self.assertEqual((model_calls, tool_calls), ([], []))

    def test_runtime_import_path_does_not_load_scorer_or_oracle_modules(self):
        root = pathlib.Path(__file__).parents[1]
        program = "import sys; sys.path.insert(0, %r); import qveris_benchmark.run_backend; import qveris_benchmark.live_get_client; assert not any(name.startswith('qveris_benchmark.') and ('scorer' in name or 'oracle' in name) for name in sys.modules)" % str(root / "src")
        subprocess.run([sys.executable, "-c", program], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()

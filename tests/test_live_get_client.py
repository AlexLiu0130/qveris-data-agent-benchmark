import dataclasses
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.benchmark_scorer import BenchmarkScorer, SCORER_DIGEST, SCORER_VERSION
from qveris_benchmark.live_get_client import QVerisPublicGetConfig, build_qveris_public_get_client
from qveris_benchmark.run_backend import RunService, RunStore, _digest, _variant_contract_digest


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


def raw_quote():
    return {"Global Quote": {
        "01. symbol": "AAPL", "02. open": "1", "03. high": "2", "04. low": "0.5",
        "05. price": "1.5", "06. volume": "3", "07. latest trading day": "2026-09-04",
        "08. previous close": "1.2", "09. change": "0.3", "10. change percent": "25%",
    }}


def model_opener(calls):
    def open_(request, timeout):
        calls.append((request, timeout))
        semantic = {"schema_version": "public-get.semantic/v1", "request": {
            "kind": "market_quote", "security": {"asset_class": "equity", "venue": "US", "local_code": "AAPL"}, "operation": "quote_snapshot",
        }}
        call_id = request.get_header("X-request-id")
        body = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(semantic)}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}, "qveris_billing": {"call_id": call_id, "usage_estimated": False}}
        return Response(json.dumps(body).encode(), {"X-QVeris-Call-ID": call_id})
    return open_


def tool_opener(calls):
    def open_(request, timeout):
        calls.append((request, timeout))
        return Response(json.dumps({"success": True, "result": {"data": raw_quote()}}).encode())
    return open_


def policy():
    contracts = {
        "success": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]},
        "partial": {"required_non_null_paths": ["resolved_request", "data", "as_of", "source"], "required_null_paths": ["clarification", "terminal_reason"]},
        "needs_clarification": {"required_non_null_paths": ["clarification"], "required_null_paths": ["data", "terminal_reason"]},
        "unsupported": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
        "no_data": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
        "error": {"required_non_null_paths": ["terminal_reason"], "required_null_paths": ["data", "clarification"]},
    }
    return {"schema_version": "score-policy/v1", "metric_names": ["semantic_accuracy", "data_accuracy", "token_usage", "e2e_latency"], "percentile_method": "nearest_rank", "assertion_operators": ["exact", "within_abs"], "operator_registry": ["exact", "within_abs"], "case_pass_gate": ["schema_valid", "status_correct", "semantic_pass", "data_pass", "execution_complete"], "completeness": {}, "response_schema_version": "get-response/v1", "response_status_contracts": contracts, "max_reference_window_seconds": 60, "error": "disabled", "timeout_latency_treatment": "observed", "usage_receipt_required_fields": ["receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"], "trusted_receipt_issuers": ["qveris_model_gateway"], "eligibility": None, "ranking": None}


class LiveGetClientTests(unittest.TestCase):
    def config(self):
        return QVerisPublicGetConfig("model-key", "tool-key", "public-model")

    def test_config_requires_exact_environment_values(self):
        with self.assertRaisesRegex(ValueError, "model_gateway_api_key"):
            QVerisPublicGetConfig.from_environment({})
        config = QVerisPublicGetConfig.from_environment({"QVERIS_MODEL_GATEWAY_API_KEY": "model-key", "QVERIS_API_KEY": "tool-key", "QVERIS_MODEL_GATEWAY_MODEL": "public-model"})
        self.assertEqual(config.identity()["model_identifier"], "public-model")

    def test_factory_uses_fixed_gateways_and_runservice_client_contract(self):
        model_calls, tool_calls = [], []
        client = build_qveris_public_get_client(self.config(), model_opener=model_opener(model_calls), tool_opener=tool_opener(tool_calls))
        result = client.run("AAPL quote", request_id="request-1", idempotency_key="idem-1")
        self.assertEqual((result.public_response["status"], result.public_response["meta"]["usage"]["issuer"]), ("success", "qveris_model_gateway"))
        self.assertEqual((model_calls[0][0].full_url, model_calls[0][0].get_method(), tool_calls[0][0].full_url), ("https://aigateway.qveris.ai/v1/chat/completions", "POST", "https://qveris.ai/api/v1/tools/execute?tool_id=alphavantage.global_quote.retrieve.v1.9b8a7c6d"))
        self.assertEqual(tool_calls[0][0].get_header("X-request-id"), "request-1")

    def test_mock_http_chain_is_scored_with_bound_qveris_usage_receipt(self):
        model_calls, tool_calls = [], []
        first = self.config()
        second = dataclasses.replace(first, agent_variant_id="qveris-semantic-agent-b")
        clients = {
            "variant-a": build_qveris_public_get_client(first, model_opener=model_opener(model_calls), tool_opener=tool_opener(tool_calls)),
            "variant-b": build_qveris_public_get_client(second, model_opener=model_opener(model_calls), tool_opener=tool_opener(tool_calls)),
        }
        variants = [
            {"variant_id": "variant-a", "stable_display_order": 1, **first.identity()},
            {"variant_id": "variant-b", "stable_display_order": 2, **second.identity()},
        ]
        scores = policy()
        oracle = {"schema_version": "oracle-bundle/v1", "oracles": {"oracle-a": {"oracle_id": "oracle-a", "case_id": "case-a", "independence": "independent_frozen", "semantic_assertions": [{"path": "resolved_request.security.symbol", "operator": "exact", "expected": "AAPL", "tolerance": None, "weight": 1, "fatal": True}], "data_assertions": [{"path": "data.close", "operator": "exact", "expected": "1.5", "tolerance": None, "weight": 1, "fatal": True}], "state_assertions": [], "reference_evidence": None, "source_ref": "frozen", "version": "v1", "semantic_review_status": "approved", "data_review_status": "approved", "state_review_status": "not_applicable"}}}
        contract = {"policy_digest": _digest(scores), "oracle_bundle_digest": _digest(oracle), "scorer_version": SCORER_VERSION, "scorer_digest": SCORER_DIGEST, "variant_contract_digest": _variant_contract_digest(variants)}
        manifest = {"run_id": "live-chain", "mode": "diagnostic", "freeze_digest": "a" * 64, "policy": {"version": "v1"}, "timeout_ms": 1000, "concurrency": 1, "scoring_contract": contract, "variants": variants, "cases": [{"case_id": "case-a", "suite": "historical_price", "query": "AAPL quote", "score_case": {"expected_status": ["success"], "oracle_id": "oracle-a", "case_type": "normal"}}]}
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(directory)
            service = RunService(store, clients)
            service.create_run(manifest)
            service.execute("live-chain")
            projection = BenchmarkScorer(store, policy=scores, oracle_bundle=oracle, approved_policy_digests={_digest(scores)}, approved_oracle_bundle_digests={_digest(oracle)}).score("live-chain")
        self.assertEqual([item["metrics"]["token_usage"]["total_mean"] for item in projection["variants"]], [5.0, 5.0])
        self.assertEqual((len(model_calls), len(tool_calls)), (2, 2))


if __name__ == "__main__":
    unittest.main()

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.agent import ModelProfile, SemanticAgent
from qveris_benchmark.connector import Connector, FakeReplayTransport, LiveTransport, TransportResponse
from qveris_benchmark.contracts import AuthMode, Domain
from qveris_benchmark.manifest import TOOL_MANIFEST_SCHEMA_VERSION, Manifest, ToolManifestEntry
from qveris_benchmark.runner import BenchmarkCase, BenchmarkRunner, Outcome, RunMode, append_result, load_oracle


class FakeModelTransport:
    def __init__(self, content, usage=None):
        self.content, self.usage, self.calls = content, usage, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        response = {"choices": [{"message": {"content": self.content}}]}
        if self.usage is not None:
            response["usage"] = self.usage
        return json.dumps(response).encode()


def manifest():
    return Manifest.from_entries(
        [ToolManifestEntry("quote", "replay.quote", {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}, {"type": "object"}, Domain.REALTIME_QUOTE, AuthMode.BEARER)],
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
    )


def case(**changes):
    raw = {"case_id": "case-1", "family_id": "quote", "suite": "realtime_quote", "query": "ACME quote", "expected_status": "READY", "expected_semantics": {"domain": "realtime_quote"}, "expected_tool_alias": "quote", "expected_arguments": {"symbol": "ACME"}, "oracle_ref": "quote.json", "comparison_rule": {"fields": {"data.price": {"mode": "float_tolerance", "absolute": 0.01}}}}
    raw.update(changes)
    return BenchmarkCase.from_mapping(raw)


def oracle(response=None, expected=None, *, reference_kind="independent_source", domain="realtime_quote"):
    return {"domain": domain, "reference_kind": reference_kind, "synthetic": reference_kind == "replay_fixture", "response": response or {"success": True, "data": {"price": 1.0}}, "expected": expected or {"data": {"price": 1.0}}}


class RunnerTests(unittest.TestCase):
    def runner(self, plan, fixture=None, usage=None, *, mode=RunMode.REPLAY_FIXTURE_SELF_CHECK):
        model = FakeModelTransport(plan, usage)
        agent = SemanticAgent(ModelProfile("https://replay.invalid", "gpt-5.6-terra", reasoning_effort="high"), model)
        transport = FakeReplayTransport({"replay.quote": fixture or {"success": True, "data": {"price": 1.0}}})
        return BenchmarkRunner(agent, Connector(manifest(), transport), mode=mode), model, transport

    def test_fake_replay_runs_once_without_scoring_data_accuracy(self):
        runner, model, connector = self.runner('{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}', usage={"total_tokens": 9})
        record = runner.run_case(case(), oracle())
        self.assertEqual(record["outcome"], Outcome.NOT_SCORED_ORACLE.value)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(connector.calls), 1)
        self.assertEqual(record["connector_call_count"], 1)
        self.assertEqual(record["connector_outcome"], "success")
        self.assertTrue(record["fixture_response_match"])
        self.assertEqual(record["metrics"]["data_accuracy"], "not_scored")
        self.assertEqual(record["agent_usage_receipt"], {"total_tokens": 9})
        self.assertEqual(record["metrics"]["token_usage"]["source"], "provider_reported")
        self.assertEqual(record["validated_plan"]["tool_alias"], "quote")
        self.assertEqual(set(("mode", "case_sha256", "manifest_sha256", "oracle_sha256", "agent_call_ms", "plan_gate_ms", "connector_ms", "e2e_ms")) <= set(record), True)

    def test_replay_fixture_is_self_check_not_data_accuracy(self):
        runner, _, _ = self.runner('{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}')
        record = runner.run_case(case(), oracle(reference_kind="replay_fixture"))
        self.assertEqual(record["outcome"], Outcome.NOT_SCORED_ORACLE.value)
        self.assertEqual(record["self_check"], "pass")
        self.assertEqual(record["metrics"]["data_accuracy"], "not_scored")

    def test_non_ready_runs_zero_connector_calls(self):
        runner, model, connector = self.runner('{"status":"REJECT","message":"unsupported"}')
        rejected = case(expected_status="REJECT", expected_semantics={"message": "unsupported"}, expected_tool_alias=None, expected_arguments={})
        self.assertEqual(runner.run_case(rejected, None)["outcome"], Outcome.SUCCESS.value)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(connector.calls, [])

    def test_failure_attribution_leaves_are_distinct(self):
        plan = '{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}'
        scenarios = (
            (case(), None, plan, None, Outcome.NOT_SCORED_ORACLE),
            (case(), oracle(), 'not json', None, Outcome.SEMANTIC_ERROR),
            (case(), oracle(), '{"status":"REJECT","message":"unsupported"}', None, Outcome.SEMANTIC_ERROR),
            (case(), oracle(), plan, TransportResponse(500, {"success": False}), Outcome.PROVIDER_ERROR),
            (case(), oracle(), plan, {"success": False}, Outcome.PROVIDER_ERROR),
            (case(), oracle(), plan, {"success": True, "data": {"price": 2.0}}, Outcome.NOT_SCORED_ORACLE),
        )
        for benchmark_case, fixture_oracle, response, fixture, expected in scenarios:
            with self.subTest(expected=expected):
                runner, _, _ = self.runner(response, fixture)
                self.assertEqual(runner.run_case(benchmark_case, fixture_oracle)["outcome"], expected.value)

    def test_fixture_mismatch_is_self_check_failure_not_data_accuracy(self):
        plan = '{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}'
        runner, _, _ = self.runner(plan, {"success": True, "data": {"price": 2.0}})
        record = runner.run_case(case(), oracle())
        self.assertEqual(record["outcome"], Outcome.NOT_SCORED_ORACLE.value)
        self.assertFalse(record["fixture_response_match"])
        self.assertEqual(record["self_check"], "failed")

    def test_connector_terminal_outcomes_are_retained_and_classified(self):
        plan = '{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}'
        scenarios = (
            ({"success": True, "status": "blocked"}, "blocked", Outcome.PROVIDER_ERROR),
            ({"success": True, "data": []}, "empty", Outcome.PROVIDER_ERROR),
            ({"success": False}, "failed", Outcome.PROVIDER_ERROR),
            (TransportResponse(None, error="timeout", timed_out=True), "uncertain", Outcome.VALIDATOR_CONNECTOR_ERROR),
        )
        for fixture, terminal, expected in scenarios:
            with self.subTest(terminal=terminal):
                runner, _, _ = self.runner(plan, fixture)
                record = runner.run_case(case(), oracle())
                self.assertEqual(record["connector_outcome"], terminal)
                self.assertEqual(record["outcome"], expected.value)

    def test_preflight_domain_mismatch_uses_zero_calls(self):
        runner, model, connector = self.runner('{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}')
        record = runner.run_case(case(suite="historical_price"), oracle())
        self.assertEqual(record["outcome"], Outcome.NOT_SCORED_ORACLE.value)
        self.assertEqual(model.calls, [])
        self.assertEqual(connector.calls, [])

    def test_loader_rejects_path_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            base, external = pathlib.Path(directory), pathlib.Path(outside) / "oracle.json"
            external.write_text(json.dumps(oracle()))
            os.symlink(external, base / "escape.json")
            self.assertIsNone(load_oracle(base, "../oracle.json"))
            self.assertIsNone(load_oracle(base, "escape.json"))

    def test_loader_rejects_unmarked_replay_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "oracle.json"
            path.write_text(json.dumps({"domain": "realtime_quote", "reference_kind": "replay_fixture", "response": {"success": True}, "expected": {}}))
            self.assertIsNone(load_oracle(directory, "oracle.json"))

    def test_replay_refuses_live_connector_and_requires_explicit_live_model_mode(self):
        fake_agent = SemanticAgent(ModelProfile("https://replay.invalid", "gpt-5.6-terra"), FakeModelTransport('{"status":"REJECT","message":"x"}'))
        with self.assertRaises(ValueError):
            BenchmarkRunner(fake_agent, Connector(manifest(), LiveTransport(), api_key="not-used"))
        live_agent = SemanticAgent(ModelProfile("https://model.example", "gpt-5.6-terra", frozenset({"https://model.example"})))
        fake_connector = Connector(manifest(), FakeReplayTransport({"replay.quote": {"success": True}}))
        with self.assertRaises(ValueError):
            BenchmarkRunner(live_agent, fake_connector)
        BenchmarkRunner(live_agent, fake_connector, mode=RunMode.MODEL_LIVE_REPLAY_DATA)

    def test_append_is_durable_jsonl(self):
        runner, _, _ = self.runner('{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{"symbol":"ACME"}}')
        record = runner.run_case(case(), oracle())
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "results.jsonl"
            append_result(path, record)
            self.assertEqual(json.loads(path.read_text()), record)


if __name__ == "__main__":
    unittest.main()

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.agent import ModelProfile, SemanticAgent, SemanticPlanReceipt
from qveris_benchmark.connector import (
    CallMetadata,
    CallOutcome,
    Connector,
    ConnectorResult,
    FakeReplayTransport,
    LiveTransport,
    RequestValidationError,
)
from qveris_benchmark.contracts import AuthMode, Domain, PlanStatus, SemanticPlan
from qveris_benchmark.get_interface import GetResultEnvelope, GetStatus, QVerisGet
from qveris_benchmark.manifest import TOOL_MANIFEST_SCHEMA_VERSION, Manifest, ToolManifestEntry


_CLOSED_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "data": {
            "type": "object",
            "properties": {"report": {"type": "array", "items": {"type": "number"}}},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def manifest(response_schema=None):
    return Manifest.from_entries(
        [
            ToolManifestEntry(
                "quote",
                "provider.internal",
                {"type": "object"},
                response_schema or _CLOSED_RESPONSE_SCHEMA,
                Domain.REALTIME_QUOTE,
                AuthMode.BEARER,
            )
        ],
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
    )


def receipt(status: str, *, message: str = "need detail") -> SemanticPlanReceipt:
    if status == "READY":
        plan = SemanticPlan.from_json(
            '{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{}}'
        )
    else:
        plan = SemanticPlan.from_json('{"status":"%s","message":"%s"}' % (status, message))
    return SemanticPlanReceipt(plan, {"total_tokens": 9})


class FakeAgent:
    def __init__(self, manifest, result=None, error=None):
        self.manifest = manifest
        self.result = result
        self.error = error
        self.calls = []

    def plan(self, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return self.result


class FakeConnector:
    def __init__(self, manifest, result=None, error=None, *, is_live=False):
        self.manifest = manifest
        self.is_live = is_live
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, plan, *, idempotency_key):
        self.calls.append((plan, idempotency_key))
        if self.error:
            raise self.error
        return self.result


def connector_result(outcome, payload=None):
    return ConnectorResult(
        outcome,
        payload,
        CallMetadata("quote", "provider.internal", "internal-key", 1, 3, 200),
        "raw provider reason",
    )


class QVerisGetTests(unittest.TestCase):
    def make_get(
        self,
        agent_result=None,
        connector_result_value=None,
        *,
        agent_error=None,
        connector_error=None,
        response_schema=None,
        is_live=False,
        trace_sink=None,
    ):
        active_manifest = manifest(response_schema)
        self.agent = FakeAgent(active_manifest, agent_result, agent_error)
        self.connector = FakeConnector(active_manifest, connector_result_value, connector_error, is_live=is_live)
        return QVerisGet(self.agent, self.connector, trace_sink=trace_sink)

    def test_ready_returns_only_public_envelope_once_and_copies_payload(self):
        payload = {"success": True, "data": {"report": [1]}}
        traces = []
        get = self.make_get(receipt("READY"), connector_result(CallOutcome.SUCCESS, payload), trace_sink=traces.append)

        result = get.get("TSLA latest revenue", request_id="request-1", idempotency_key="idempotency-1")

        self.assertIsInstance(result, GetResultEnvelope)
        self.assertFalse(hasattr(result, "trace"))
        self.assertEqual(result.status, GetStatus.SUCCESS)
        self.assertEqual(result.tool_alias, "quote")
        self.assertEqual(result.payload, payload)
        self.assertIsNot(result.payload, payload)
        self.assertIsNot(result.payload["data"], payload["data"])
        self.assertEqual(len(self.agent.calls), 1)
        self.assertEqual(len(self.connector.calls), 1)
        self.assertEqual(self.connector.calls[0][1], "idempotency-1")
        self.assertEqual(traces[0].connector_call_count, 1)

    def test_clarify_reject_and_semantic_error_use_zero_connector_calls(self):
        for status in (PlanStatus.CLARIFY.value, PlanStatus.REJECT.value):
            with self.subTest(status=status):
                get = self.make_get(receipt(status), connector_result(CallOutcome.SUCCESS, {"success": True}))
                result = get.get("question", request_id="request-2", idempotency_key="idempotency-2")
                self.assertEqual(result.status.value, status)
                self.assertEqual(self.connector.calls, [])
        get = self.make_get(agent_error=ValueError("provider response leaked"))
        result = get.get("question", request_id="request-3", idempotency_key="idempotency-3")
        self.assertEqual(result.status, GetStatus.SEMANTIC_ERROR)
        self.assertEqual(result.message, "semantic_error")
        self.assertEqual(self.connector.calls, [])

    def test_connector_outcomes_are_exact_and_payloads_are_copied(self):
        for outcome in CallOutcome:
            with self.subTest(outcome=outcome):
                payload = {"success": outcome is CallOutcome.SUCCESS, "data": {"report": [1]}}
                get = self.make_get(receipt("READY"), connector_result(outcome, payload))
                result = get.get("question", request_id="request-4", idempotency_key="idempotency-4")
                self.assertEqual(result.status.value, outcome.value)
                self.assertEqual(result.payload, payload)
                self.assertIsNot(result.payload, payload)
                self.assertEqual(len(self.connector.calls), 1)

    def test_connector_validation_is_known_zero_and_other_exception_is_uncertain(self):
        traces = []
        get = self.make_get(receipt("READY"), connector_error=RequestValidationError("bad"), trace_sink=traces.append)
        result = get.get("question", request_id="request-5", idempotency_key="idempotency-5")
        self.assertEqual(result.status, GetStatus.FAILED)
        self.assertEqual(traces[0].connector_call_count, 0)
        traces = []
        get = self.make_get(receipt("READY"), connector_error=RuntimeError("unknown"), trace_sink=traces.append)
        result = get.get("question", request_id="request-6", idempotency_key="idempotency-6")
        self.assertEqual(result.status, GetStatus.UNCERTAIN)
        self.assertEqual(result.message, "tool execution uncertain")
        self.assertIsNone(traces[0].connector_call_count)

    def test_constructor_rejects_mismatch_qveris_tool_live_and_unsafe_response_schemas(self):
        first, second = manifest(), manifest()
        with self.assertRaises(ValueError):
            QVerisGet(FakeAgent(first), FakeConnector(second))
        with self.assertRaises(ValueError):
            QVerisGet(FakeAgent(first), FakeConnector(first, is_live=True))
        unclosed = {"type": "object", "properties": {}}
        with self.assertRaises(ValueError):
            self.make_get(response_schema=unclosed)
        sensitive = {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
            "additionalProperties": False,
        }
        with self.assertRaises(ValueError):
            self.make_get(response_schema=sensitive)

    def test_sensitive_response_schema_keys_use_exact_or_segment_matching(self):
        for key in (
            "authorization",
            "api_key",
            "access_token",
            "secret",
            "password",
            "credential",
            "token",
            "cookie",
            "header",
            "key",
            "tool_id",
            "idempotency",
            "idempotencyKey",
            "execution-id",
        ):
            with self.subTest(key=key):
                response_schema = {
                    "type": "object",
                    "properties": {key: {"type": "string"}},
                    "additionalProperties": False,
                }
                with self.assertRaises(ValueError):
                    self.make_get(response_schema=response_schema)

        for key in ("apiKey", "access-token", "idempotencyKey", "execution-id", "toolId"):
            with self.subTest(normalized_key=key):
                response_schema = {
                    "type": "object",
                    "properties": {"data": {"type": "object", "properties": {key: {"type": "string"}}, "additionalProperties": False}},
                    "additionalProperties": False,
                }
                with self.assertRaises(ValueError):
                    self.make_get(response_schema=response_schema)

        for key in ("report_authorization", "header_value", "prefix_tool_id"):
            with self.subTest(sensitive_segment=key):
                response_schema = {
                    "type": "object",
                    "properties": {key: {"type": "string"}},
                    "additionalProperties": False,
                }
                with self.assertRaises(ValueError):
                    self.make_get(response_schema=response_schema)

        safe_schema = {
            "type": "object",
            "properties": {
                "monkey": {"type": "string"},
                "tokenizer": {"type": "string"},
                "keyboard": {"type": "string"},
                "tool_identifier": {"type": "string"},
                "execution_time": {"type": "number"},
            },
            "additionalProperties": False,
        }
        self.make_get(response_schema=safe_schema)

    def test_rejects_qveris_live_transport_but_allows_semantic_replay_only(self):
        active_manifest = manifest()
        agent = SemanticAgent(
            ModelProfile("https://model.example/v1", "model", frozenset({"https://model.example/v1"})),
            active_manifest,
        )
        QVerisGet(agent, Connector(active_manifest, FakeReplayTransport({})))
        with self.assertRaisesRegex(ValueError, "^QVerisGet only accepts a non-live connector$"):
            QVerisGet(agent, Connector(active_manifest, LiveTransport(), api_key="test-secret"))

    def test_sink_failure_does_not_change_business_result(self):
        get = self.make_get(
            receipt("READY"),
            connector_result(CallOutcome.SUCCESS, {"success": True, "data": {"report": [1]}}),
            trace_sink=lambda _: (_ for _ in ()).throw(RuntimeError("sink failure")),
        )
        self.assertEqual(
            get.get("question", request_id="request-7", idempotency_key="idempotency-7").status,
            GetStatus.SUCCESS,
        )
        self.assertEqual(len(self.connector.calls), 1)

    def test_public_envelope_has_no_metrics_or_execution_secrets(self):
        get = self.make_get(receipt("READY"), connector_result(CallOutcome.SUCCESS, {"success": True}))
        result = get.get("question", request_id="request-8", idempotency_key="idempotency-8")
        self.assertEqual(set(result.__dataclass_fields__), {"request_id", "status", "tool_alias", "payload", "message"})
        for forbidden in ("usage", "token", "cost", "latency", "plan", "call_count", "tool_id", "idempotency", "header", "key", "oracle"):
            self.assertNotIn(forbidden, result.__dataclass_fields__)

    def test_input_limits_and_control_characters_fail_closed_before_calls(self):
        invalid_requests = (
            ("", "request", "key"),
            ("line\nfeed", "request", "key"),
            ("a" * 4097, "request", "key"),
            ("query", "invalid space", "key"),
            ("query", "request", "a" * 129),
        )
        for query, request_id, idempotency_key in invalid_requests:
            with self.subTest(query=query, request_id=request_id, idempotency_key=idempotency_key):
                get = self.make_get(receipt("READY"), connector_result(CallOutcome.SUCCESS, {"success": True}))
                with self.assertRaises(ValueError):
                    get.get(query, request_id=request_id, idempotency_key=idempotency_key)
                self.assertEqual(self.agent.calls, [])
                self.assertEqual(self.connector.calls, [])


if __name__ == "__main__":
    unittest.main()

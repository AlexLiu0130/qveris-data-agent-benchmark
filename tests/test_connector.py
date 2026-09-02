import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.connector import (
    CallOutcome,
    Connector,
    FakeReplayTransport,
    LiveTransport,
    RequestValidationError,
    ResponseTooLargeError,
    TransportResponse,
)
from qveris_benchmark.contracts import AuthMode, Domain, SemanticPlan
from qveris_benchmark.manifest import TOOL_MANIFEST_SCHEMA_VERSION, Manifest, ToolManifestEntry


def manifest(request_schema=None, response_schema=None) -> Manifest:
    return Manifest.from_entries(
        [
            ToolManifestEntry(
                "quote",
                "provider.quote",
                request_schema or {
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
                response_schema or {"type": "object"},
                Domain.REALTIME_QUOTE,
                AuthMode.BEARER,
            )
        ],
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
    )


def plan(request: str = '{"symbol":"AAPL"}') -> SemanticPlan:
    return SemanticPlan.from_json(
        '{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":%s}' % request
    )


class ConnectorTests(unittest.TestCase):
    def make_connector(self, fixture):
        transport = FakeReplayTransport({"provider.quote": fixture})
        return Connector(manifest(), transport, api_key="test-secret"), transport

    def test_resolves_alias_and_injects_internal_bearer(self) -> None:
        connector, transport = self.make_connector({"success": True, "data": {"price": 1}})
        result = connector.execute(plan(), idempotency_key="case-1")
        self.assertEqual(result.outcome, CallOutcome.SUCCESS)
        self.assertEqual(result.metadata.tool_id, "provider.quote")
        self.assertEqual(transport.calls[0]["url"], "https://qveris.ai/api/v1/tools/execute?tool_id=provider.quote")
        self.assertEqual(transport.calls[0]["body"], b'{"parameters":{"symbol":"AAPL"}}')
        self.assertEqual(
            transport.calls[0]["headers"],
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": "case-1",
                "Authorization": "Bearer test-secret",
            },
        )
        self.assertNotIn("Search", transport.calls[0]["url"])
        self.assertNotIn("Inspect", transport.calls[0]["url"])

    def test_invalid_parameters_make_zero_calls(self) -> None:
        connector, transport = self.make_connector({"success": True})
        with self.assertRaises(RequestValidationError):
            connector.execute(plan('{"symbol":1}'), idempotency_key="case-2")
        self.assertEqual(transport.calls, [])

    def test_exposes_manifest_identity_and_live_status_without_key(self) -> None:
        configured_manifest = manifest()
        fake = Connector(configured_manifest, FakeReplayTransport({}))
        live = Connector(configured_manifest, LiveTransport(), api_key="test-secret")
        self.assertIs(fake.manifest, configured_manifest)
        self.assertFalse(fake.is_live)
        self.assertTrue(live.is_live)
        self.assertFalse(hasattr(fake, "api_key"))

    def test_recursive_schema_rejects_nested_overreach_enum_and_range_before_post(self) -> None:
        request_schema = {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "pattern": "^[A-Z]+$"},
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                        "levels": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1, "maximum": 3},
                            "minItems": 1,
                            "maxItems": 2,
                        },
                        "weight": {"type": "number", "minimum": 0, "maximum": 1},
                        "enabled": {"type": "boolean"},
                        "note": {"type": "null"},
                    },
                    "required": ["symbol", "side", "levels", "weight", "enabled", "note"],
                    "additionalProperties": False,
                }
            },
            "required": ["filter"],
            "additionalProperties": False,
        }
        transport = FakeReplayTransport({"provider.quote": {"success": True}})
        connector = Connector(manifest(request_schema), transport)
        for request in (
            '{"filter":{"symbol":"AAPL","side":"hold","levels":[1],"weight":0.5,"enabled":true,"note":null}}',
            '{"filter":{"symbol":"AAPL","side":"buy","levels":[4],"weight":0.5,"enabled":true,"note":null}}',
            '{"filter":{"symbol":"AAPL","side":"buy","levels":[1],"weight":2,"enabled":true,"note":null}}',
            '{"filter":{"symbol":"AAPL","side":"buy","levels":[1],"weight":0.5,"enabled":true,"note":null,"extra":1}}',
        ):
            with self.subTest(request=request):
                with self.assertRaises(RequestValidationError):
                    connector.execute(plan(request), idempotency_key="invalid-schema")
        self.assertEqual(transport.calls, [])

    def test_rejects_any_base_other_than_qveris_v1(self) -> None:
        for base_url in (
            "http://qveris.ai/api/v1",
            "https://example.com/api/v1",
            "https://qveris.ai/api/v1/extra",
            "https://qveris.ai/api/v1?next=x",
            "https://key@qveris.ai/api/v1",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    Connector(manifest(), FakeReplayTransport({}), base_url=base_url)

    def test_each_execute_performs_one_post(self) -> None:
        connector, transport = self.make_connector({"success": True})
        connector.execute(plan(), idempotency_key="case-3")
        self.assertEqual(len(transport.calls), 1)

    def test_http_error_and_timeout_are_not_success(self) -> None:
        for response, expected in (
            (TransportResponse(500, {"success": True}, "server error"), CallOutcome.FAILED),
            (TransportResponse(None, error="timeout", timed_out=True), CallOutcome.UNCERTAIN),
        ):
            with self.subTest(response=response):
                connector, _ = self.make_connector(response)
                self.assertEqual(connector.execute(plan(), idempotency_key="case-4").outcome, expected)

    def test_response_gate_distinguishes_all_business_outcomes(self) -> None:
        for fixture, expected in (
            ({"success": False}, CallOutcome.FAILED),
            ({"success": True, "data": []}, CallOutcome.EMPTY),
            ({"success": True, "status": "blocked"}, CallOutcome.BLOCKED),
            ({"data": {"price": 1}}, CallOutcome.UNCERTAIN),
        ):
            with self.subTest(fixture=fixture):
                connector, _ = self.make_connector(fixture)
                self.assertEqual(connector.execute(plan(), idempotency_key="case-5").outcome, expected)

    def test_response_schema_drift_is_failed_after_exactly_one_post(self) -> None:
        response_schema = {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "data": {
                    "type": "object",
                    "properties": {"price": {"type": "number"}},
                    "required": ["price"],
                    "additionalProperties": False,
                },
            },
            "required": ["success", "data"],
            "additionalProperties": False,
        }
        transport = FakeReplayTransport({"provider.quote": {"success": True, "data": {"price": "drift"}}})
        result = Connector(manifest(response_schema=response_schema), transport).execute(plan(), idempotency_key="case-7")
        self.assertEqual(result.outcome, CallOutcome.FAILED)
        self.assertTrue(result.reason.startswith("response schema validation failed:"))
        self.assertEqual(len(transport.calls), 1)

    def test_rejects_hidden_full_content_download_before_transport(self) -> None:
        connector, transport = self.make_connector({"success": True})
        with self.assertRaises(RequestValidationError):
            connector.execute(plan('{"symbol":"AAPL","full_content_file_url":"https://bad"}'), idempotency_key="case-6")
        self.assertEqual(transport.calls, [])

    def test_fake_transport_is_fixture_only(self) -> None:
        transport = FakeReplayTransport({"provider.quote": {"success": True}})
        response = transport.post("https://qveris.ai/api/v1/tools/execute?tool_id=provider.quote", b"{}", {}, 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(transport.calls), 1)

    def test_live_transport_rejects_response_larger_than_limit_before_parsing(self) -> None:
        class OversizeResponse:
            status = 200

            def __init__(self) -> None:
                self.read_limit = None

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                return False

            def read(self, limit):
                self.read_limit = limit
                return b"x" * limit

        response = OversizeResponse()
        transport = LiveTransport(max_response_bytes=3)
        with patch("qveris_benchmark.connector.urlopen", return_value=response):
            with self.assertRaisesRegex(ResponseTooLargeError, "^response exceeds max_response_bytes$"):
                transport.post("https://qveris.ai/api/v1/tools/execute?tool_id=quote", b"{}", {}, 1)
        self.assertEqual(response.read_limit, 4)


if __name__ == "__main__":
    unittest.main()

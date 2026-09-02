import io
import json
import pathlib
import sys
import unittest
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))

import qveris_catalog_audit as audit


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, size):  # noqa: ARG002
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, json.loads(request.data), timeout, dict(request.header_items())))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


class CatalogAuditTests(unittest.TestCase):
    def test_searches_and_inspects_deduplicated_ids_without_execute(self):
        opener = FakeOpener(
            [
                {"remaining_credits": 9, "results": [{"tool_id": "a", "name": "A", "description": "alpha"}, {"tool_id": "b"}]},
                {"remaining_credits": 8, "results": [{"tool_id": "b"}, {"tool_id": "c"}]},
                {"remaining_credits": 7, "results": [{"tool_id": "a", "provider": "p", "expected_cost": 1, "params": [{"name": "symbol"}]}]},
            ]
        )
        result = audit.run_audit("quotes", ["first", "second"], 3, 2, "not-output", opener)

        self.assertEqual([call[0].removeprefix(audit.BASE_URL) for call in opener.calls], ["/search", "/search", "/tools/by-ids"])
        self.assertEqual(opener.calls[2][1], {"tool_ids": ["a", "b"]})
        self.assertEqual(result["candidate_tool_ids"], ["a", "b", "c"])
        self.assertEqual(result["request_count"], {"total": 3, "search": 2, "inspect": 1, "execute": 0})
        self.assertFalse(result["requests"]["execute_path_called"])
        self.assertEqual(result["balance"]["before_inspect"], 9)
        self.assertEqual(result["balance"]["after_inspect"], 7)

    def test_redacts_sensitive_schema_values_and_never_records_headers(self):
        opener = FakeOpener(
            [{"results": [{"tool_id": "a", "schema": {"type": "object", "headers": {"Authorization": "keep-out"}, "properties": {"access_token": {"default": "keep-out"}, "symbol": {"type": "string"}}}}]}, {"results": []}]
        )
        result = audit.run_audit("quotes", ["one"], 1, 1, "test-secret", opener)
        written = json.dumps(result)

        self.assertNotIn("test-secret", written)
        self.assertNotIn("keep-out", written)
        self.assertNotIn("Authorization", written)
        self.assertNotIn("token", written)
        self.assertEqual(result["requests"]["search"][0]["candidates"][0]["schema"]["properties"], {"symbol": {"type": "string"}})

    def test_http_error_is_recorded_without_retry_or_inspect(self):
        opener = FakeOpener([urllib.error.HTTPError("https://example", 429, "too many", {}, io.BytesIO())])
        result = audit.run_audit("quotes", ["one"], 1, 1, "key", opener)

        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result["requests"]["search"][0]["http_status"], 429)
        self.assertEqual(result["requests"]["search"][0]["outcome"], "http_application_error")
        self.assertEqual(result["requests"]["inspect"]["outcome"], "skipped_no_candidates")


if __name__ == "__main__":
    unittest.main()

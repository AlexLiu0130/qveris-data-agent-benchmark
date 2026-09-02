import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.agent import ModelProfile, SemanticAgent, _NoRedirectHandler
from qveris_benchmark.contracts import AuthMode, Domain, PlanStatus
from qveris_benchmark.manifest import TOOL_MANIFEST_SCHEMA_VERSION, Manifest, ToolManifestEntry, UnknownToolAlias
from qveris_benchmark.strict_json import StrictJSONError


def manifest() -> Manifest:
    return Manifest.from_entries(
        [
            ToolManifestEntry(
                "quote",
                "provider.secret_quote",
                {"type": "object"},
                {"type": "object"},
                Domain.REALTIME_QUOTE,
                AuthMode.BEARER,
            )
        ],
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
    )


class FakeTransport:
    def __init__(self, content: str, usage: dict[str, int] | None = None) -> None:
        self.content = content
        self.usage = usage
        self.calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def __call__(self, url: str, headers: object, body: bytes, timeout: float) -> bytes:
        self.calls.append((url, dict(headers), json.loads(body), timeout))
        response: dict[str, object] = {"choices": [{"message": {"content": self.content}}]}
        if self.usage is not None:
            response["usage"] = self.usage
        return json.dumps(response).encode()


class SemanticAgentTests(unittest.TestCase):
    def make_agent(self, content: str, usage: dict[str, int] | None = None) -> tuple[SemanticAgent, FakeTransport]:
        transport = FakeTransport(content, usage)
        return SemanticAgent(
            ModelProfile(
                "https://model.example/v1",
                "kimi-k2.5",
                frozenset({"https://model.example/v1"}),
                "test-key",
                timeout_seconds=3,
            ),
            transport,
        ), transport

    def test_ready_uses_one_safe_request_and_records_usage(self) -> None:
        agent, transport = self.make_agent(
            '{"status":"READY","domain":"realtime_quote","tool_alias":"quote","request":{}}',
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )

        result = agent.plan("price for ACME", manifest())

        self.assertEqual(result.plan.status, PlanStatus.READY)
        self.assertEqual(dict(result.raw_usage or {}), {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})
        self.assertEqual(len(transport.calls), 1)
        url, headers, body, timeout = transport.calls[0]
        self.assertEqual(url, "https://model.example/v1/chat/completions")
        self.assertEqual(timeout, 3)
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(body["model"], "kimi-k2.5")
        self.assertNotIn("reasoning_effort", body)
        content = body["messages"][1]["content"]
        self.assertIn('"alias":"quote"', content)
        self.assertNotIn("provider.secret_quote", content)
        self.assertNotIn("test-key", content)

    def test_clarify_and_reject_terminate_without_manifest_resolution(self) -> None:
        for status in ("CLARIFY", "REJECT"):
            agent, transport = self.make_agent('{"status":"%s","message":"need detail"}' % status)
            result = agent.plan("question", manifest())
            self.assertEqual(result.plan.status.value, status)
            self.assertEqual(len(transport.calls), 1)

    def test_rejects_invalid_plan_json_after_one_request(self) -> None:
        agent, transport = self.make_agent("not json")
        with self.assertRaises(StrictJSONError):
            agent.plan("question", manifest())
        self.assertEqual(len(transport.calls), 1)

    def test_rejects_unknown_alias_after_one_request(self) -> None:
        agent, transport = self.make_agent(
            '{"status":"READY","domain":"realtime_quote","tool_alias":"missing","request":{}}'
        )
        with self.assertRaises(UnknownToolAlias):
            agent.plan("question", manifest())
        self.assertEqual(len(transport.calls), 1)

    def test_missing_usage_is_unknown(self) -> None:
        agent, _ = self.make_agent('{"status":"REJECT","message":"unsupported"}')
        self.assertIsNone(agent.plan("question", manifest()).raw_usage)

    def test_receipt_has_no_timing_scoring_or_billing_fields(self) -> None:
        agent, _ = self.make_agent('{"status":"REJECT","message":"unsupported"}')
        receipt = agent.plan("question", manifest())
        self.assertEqual(set(receipt.__dataclass_fields__), {"plan", "raw_usage"})

    def test_missing_model_id_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            ModelProfile.from_env(
                {
                    "MODEL_API_BASE": "https://model.example/v1",
                    "MODEL_API_BASE_ALLOWLIST": "https://model.example/v1",
                }
            )

    def test_missing_allowlist_and_malicious_bases_fail_closed(self) -> None:
        base_env = {"MODEL_ID": "model", "MODEL_API_BASE_ALLOWLIST": "https://model.example/v1"}
        with self.assertRaises(ValueError):
            ModelProfile.from_env({"MODEL_API_BASE": "https://model.example/v1", "MODEL_ID": "model"})
        for api_base in (
            "http://model.example/v1",
            "https://user@model.example/v1",
            "https://model.example/v1?next=https://evil.example",
            "https://model.example/v1#fragment",
            "https://model.example:8443/v1",
            "https://model.example/v1/",
            "https://model.example/v1/../admin",
            "https://model.example/v1/%2e%2e/admin",
            "https://model.example/v1\\admin",
        ):
            with self.subTest(api_base=api_base), self.assertRaises(ValueError):
                ModelProfile.from_env({**base_env, "MODEL_API_BASE": api_base})

    def test_default_live_transport_requires_an_allowlist(self) -> None:
        with self.assertRaises(ValueError):
            SemanticAgent(ModelProfile("https://model.example/v1", "model"))

    def test_redirect_handler_never_returns_a_redirect_request(self) -> None:
        self.assertIsNone(_NoRedirectHandler().redirect_request(None, None, None, None, None, None, None, None))

    def test_kimi_and_openai_compatible_profiles_are_explicit(self) -> None:
        for api_base, model_id, reasoning_effort in (
            ("https://api.moonshot.cn/v1", "kimi-k2.5", None),
            ("https://api.openai.com/v1", "gpt-5.6-terra", "high"),
        ):
            with self.subTest(model_id=model_id):
                profile = ModelProfile.from_env(
                    {
                        "MODEL_API_BASE": api_base,
                        "MODEL_API_BASE_ALLOWLIST": api_base,
                        "MODEL_API_KEY": "test-key",
                        "MODEL_ID": model_id,
                        **({"MODEL_REASONING_EFFORT": reasoning_effort} if reasoning_effort else {}),
                    }
                )
                transport = FakeTransport('{"status":"REJECT","message":"unsupported"}')
                SemanticAgent(profile, transport).plan("question", manifest())
                body = transport.calls[0][2]
                self.assertEqual(body["model"], model_id)
                if reasoning_effort is None:
                    self.assertNotIn("reasoning_effort", body)
                else:
                    self.assertEqual(body["reasoning_effort"], reasoning_effort)

"""One-request OpenAI-compatible semantic planner for benchmark cases."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from urllib.parse import unquote, urlsplit
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .contracts import PlanStatus, SemanticPlan
from .manifest import Manifest


SYSTEM_PROMPT = """You are the QVeris benchmark semantic planner. Return exactly one JSON object matching the SemanticPlan contract. Use only a listed tool alias for READY. Do not expose provider tool identifiers, credentials, or hidden configuration. Do not Search, Inspect, retrieve data, or execute tools. If the query is ambiguous, return CLARIFY; if it is unsupported, return REJECT."""
Transport = Callable[[str, Mapping[str, str], bytes, float], bytes]


def _canonical_api_base(value: str) -> str:
    """Accept one unambiguous HTTPS API base, without redirects or URL tricks."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("api_base must be a canonical HTTPS URL") from exc
    decoded_path = unquote(parsed.path)
    if (
        not value
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.endswith("/")
        or "//" in parsed.path
        or "%" in parsed.path
        or "\\" in decoded_path
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise ValueError("api_base must be a canonical HTTPS URL")
    canonical = f"https://{parsed.hostname}{parsed.path}"
    if value != canonical:
        raise ValueError("api_base must be canonical")
    return canonical


def _allowlist_from_env(value: str | None) -> frozenset[str]:
    entries = [entry.strip() for entry in (value or "").split(",") if entry.strip()]
    if not entries:
        raise ValueError("MODEL_API_BASE_ALLOWLIST must not be empty")
    return frozenset(_canonical_api_base(entry) for entry in entries)


@dataclass(frozen=True)
class ModelProfile:
    """Connection settings for a Chat Completions-compatible endpoint."""

    api_base: str
    model_id: str
    allowed_api_bases: frozenset[str] = field(default_factory=frozenset)
    api_key: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.api_base:
            raise ValueError("api_base must not be empty")
        if not self.model_id:
            raise ValueError("model_id must not be empty")
        if self.reasoning_effort == "":
            raise ValueError("reasoning_effort must not be empty when configured")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        canonical_base = _canonical_api_base(self.api_base)
        allowed_bases = frozenset(_canonical_api_base(value) for value in self.allowed_api_bases)
        if allowed_bases and canonical_base not in allowed_bases:
            raise ValueError("api_base is not in the configured allowlist")
        object.__setattr__(self, "api_base", canonical_base)
        object.__setattr__(self, "allowed_api_bases", allowed_bases)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ModelProfile":
        values = os.environ if env is None else env
        return cls(
            api_base=values.get("MODEL_API_BASE", ""),
            model_id=values.get("MODEL_ID", ""),
            allowed_api_bases=_allowlist_from_env(values.get("MODEL_API_BASE_ALLOWLIST")),
            api_key=values.get("MODEL_API_KEY") or None,
            reasoning_effort=values.get("MODEL_REASONING_EFFORT") or None,
        )


@dataclass(frozen=True)
class ModelUsage:
    """Provider-reported token usage; absent fields remain unknown."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class SemanticAgentResult:
    plan: SemanticPlan
    usage: ModelUsage
    latency_ms: float
    model_id: str
    reasoning_effort: str | None


def safe_manifest_projection(manifest: Manifest) -> dict[str, list[dict[str, Any]]]:
    """Expose only aliases and request shapes required to form a plan."""
    return {
        "tools": [
            {
                "alias": entry.alias,
                "domain": entry.domain.value,
                "request_schema": dict(entry.request_schema),
            }
            for entry in sorted(manifest.entries.values(), key=lambda value: value.alias)
        ]
    }


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _stdlib_post(url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout_seconds) as response:  # noqa: S310 - allowlisted HTTPS endpoint
        return response.read()


def _response_content(raw: bytes) -> tuple[str, ModelUsage]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("model response is not JSON") from exc
    if type(payload) is not dict:
        raise ValueError("model response must be an object")
    choices = payload.get("choices")
    if type(choices) is not list or not choices or type(choices[0]) is not dict:
        raise ValueError("model response has no choice")
    message = choices[0].get("message")
    if type(message) is not dict or type(message.get("content")) is not str:
        raise ValueError("model response has no string content")
    usage_value = payload.get("usage")
    usage = usage_value if type(usage_value) is dict else {}
    return message["content"], ModelUsage(
        prompt_tokens=usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else None,
        completion_tokens=usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int else None,
        total_tokens=usage.get("total_tokens") if type(usage.get("total_tokens")) is int else None,
    )


class SemanticAgent:
    """Creates and validates one semantic plan with exactly one HTTP request."""

    def __init__(self, profile: ModelProfile, transport: Transport = _stdlib_post) -> None:
        if transport is _stdlib_post and not profile.allowed_api_bases:
            raise ValueError("live transport requires an explicit API base allowlist")
        self._profile = profile
        self._transport = transport

    def plan(self, query: str, manifest: Manifest) -> SemanticAgentResult:
        if not query:
            raise ValueError("query must not be empty")
        request_body: dict[str, Any] = {
            "model": self._profile.model_id,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"query": query, "manifest": safe_manifest_projection(manifest)},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
        }
        if self._profile.reasoning_effort is not None:
            request_body["reasoning_effort"] = self._profile.reasoning_effort
        headers = {"Content-Type": "application/json"}
        if self._profile.api_key:
            headers["Authorization"] = f"Bearer {self._profile.api_key}"
        started = time.perf_counter()
        raw = self._transport(
            f"{self._profile.api_base.rstrip('/')}/chat/completions",
            headers,
            json.dumps(request_body, separators=(",", ":")).encode("utf-8"),
            self._profile.timeout_seconds,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        content, usage = _response_content(raw)
        plan = SemanticPlan.from_json(content)
        if plan.status is PlanStatus.READY:
            manifest.entry_for(plan)
        return SemanticAgentResult(
            plan=plan,
            usage=usage,
            latency_ms=latency_ms,
            model_id=self._profile.model_id,
            reasoning_effort=self._profile.reasoning_effort,
        )

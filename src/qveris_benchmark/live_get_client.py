"""Production assembly for the fixed one-model/one-Tool public GET client."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
from typing import Any

from .public_get import PublicGetAdapter
from .qveris_model_gateway import (
    MODEL_GATEWAY_BASE_URL,
    MODEL_GATEWAY_MAX_REQUEST_BYTES,
    MODEL_GATEWAY_MAX_RESPONSE_BYTES,
    MODEL_GATEWAY_MAX_TOKENS,
    MODEL_GATEWAY_TIMEOUT_SECONDS,
    QVerisModelGatewaySemanticResolver,
)
from .qveris_tool_gateway import (
    TOOL_GATEWAY_BASE_URL,
    TOOL_GATEWAY_MAX_REQUEST_BYTES,
    TOOL_GATEWAY_MAX_RESPONSE_BYTES,
    TOOL_GATEWAY_TIMEOUT_SECONDS,
    QVerisToolGateway,
    ToolCreditReceipt,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class QVerisPublicGetConfig:
    """All live inputs, with endpoints, limits, and model choice fixed in code."""

    model_gateway_api_key: str
    tool_gateway_api_key: str
    model: str
    agent_variant_id: str = "qveris-semantic-agent"
    agent_version: str = "v1"
    get_variant_id: str = "qveris-public-get"
    get_version: str = "v1"
    model_version: str = "qveris-model-gateway-v1"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "QVerisPublicGetConfig":
        source = environ if environ is not None else os.environ
        values = {
            "model_gateway_api_key": source.get("QVERIS_MODEL_GATEWAY_API_KEY"),
            "tool_gateway_api_key": source.get("QVERIS_API_KEY"),
            "model": source.get("QVERIS_MODEL_GATEWAY_MODEL"),
        }
        missing = sorted(name for name, value in values.items() if type(value) is not str or not value)
        if missing:
            raise ValueError("missing required environment: " + ", ".join(missing))
        return cls(**values)

    def validate(self) -> None:
        if any(type(value) is not str or not value for value in (self.model_gateway_api_key, self.tool_gateway_api_key)):
            raise ValueError("Gateway API keys are required")
        for name, value in (
            ("model", self.model), ("agent_variant_id", self.agent_variant_id), ("agent_version", self.agent_version),
            ("get_variant_id", self.get_variant_id), ("get_version", self.get_version), ("model_version", self.model_version),
        ):
            if type(value) is not str or _ID.fullmatch(value) is None:
                raise ValueError(name + " must be a safe opaque id")
        if (not MODEL_GATEWAY_BASE_URL.startswith("https://") or not TOOL_GATEWAY_BASE_URL.startswith("https://")
                or min(MODEL_GATEWAY_TIMEOUT_SECONDS, TOOL_GATEWAY_TIMEOUT_SECONDS) <= 0
                or min(MODEL_GATEWAY_MAX_REQUEST_BYTES, MODEL_GATEWAY_MAX_RESPONSE_BYTES, TOOL_GATEWAY_MAX_REQUEST_BYTES, TOOL_GATEWAY_MAX_RESPONSE_BYTES, MODEL_GATEWAY_MAX_TOKENS) <= 0):
            raise ValueError("fixed Gateway limits are invalid")

    def identity(self) -> dict[str, str]:
        self.validate()
        frozen_model_config = {
            "model": self.model,
            "model_gateway_base_url": MODEL_GATEWAY_BASE_URL,
            "temperature": 0,
            "max_tokens": MODEL_GATEWAY_MAX_TOKENS,
            "stream": False,
        }
        digest = sha256(json.dumps(frozen_model_config, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        return {
            "agent_variant_id": self.agent_variant_id,
            "agent_version": self.agent_version,
            "get_variant_id": self.get_variant_id,
            "get_version": self.get_version,
            "model_identifier": self.model,
            "model_version": self.model_version,
            "model_config_digest": digest,
        }


def build_qveris_public_get_client(
    config: QVerisPublicGetConfig,
    *,
    model_opener: Callable[[Any, float], Any] | None = None,
    tool_opener: Callable[[Any, float], Any] | None = None,
    tool_receipt_sink: Callable[[ToolCreditReceipt], None] | None = None,
) -> PublicGetAdapter:
    """Build a ``RunService``-compatible client without performing I/O.

    Calling ``resolver.preflight_models`` remains an explicit operator action;
    construction and GET execution do not list or select alternative models.
    """
    config.validate()
    resolver = QVerisModelGatewaySemanticResolver(
        api_key=config.model_gateway_api_key,
        model=config.model,
        timeout_seconds=MODEL_GATEWAY_TIMEOUT_SECONDS,
        opener=model_opener,
    )
    tool = QVerisToolGateway(
        api_key=config.tool_gateway_api_key,
        timeout_seconds=TOOL_GATEWAY_TIMEOUT_SECONDS,
        receipt_sink=tool_receipt_sink,
        opener=tool_opener,
    )
    return PublicGetAdapter(resolver, tool, **config.identity())


__all__ = ["QVerisPublicGetConfig", "build_qveris_public_get_client"]

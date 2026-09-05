"""Network-disabled public-GET sandbox entrypoint using the stdio HTTPS broker."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .public_get import PublicGetAdapter
from .qveris_model_gateway import QVerisModelGatewaySemanticResolver
from .qveris_tool_gateway import QVerisToolGateway
from .sandbox_broker import SandboxBrokerOpener


_INPUT = "sandbox-get-input/v1"
_DESCRIPTOR = "sandbox-get-runtime-config/v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _descriptor(path: str) -> dict[str, str]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("sandbox descriptor is invalid") from exc
    fields = {"schema_version", "model", "agent_variant_id", "agent_version", "get_variant_id", "get_version", "model_version", "model_config_digest"}
    if type(value) is not dict or set(value) != fields or value["schema_version"] != _DESCRIPTOR or any(type(value[name]) is not str or _ID.fullmatch(value[name]) is None for name in fields - {"schema_version", "model_config_digest"}) or _SHA256.fullmatch(value["model_config_digest"]) is None:
        raise ValueError("sandbox descriptor is invalid")
    return {name: value[name] for name in fields - {"schema_version"}}


def _input(line: str) -> dict[str, str]:
    try:
        value = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("sandbox input is invalid") from exc
    if type(value) is not dict or set(value) != {"protocol_version", "request_id", "query"} or value.get("protocol_version") != _INPUT or type(value.get("query")) is not str or not value["query"] or type(value.get("request_id")) is not str or _ID.fullmatch(value["request_id"]) is None:
        raise ValueError("sandbox input is invalid")
    return value


def run_stdio(descriptor_path: str, input_stream: Any = sys.stdin, output_stream: Any = sys.stdout) -> int:
    descriptor, incoming = _descriptor(descriptor_path), _input(input_stream.readline())
    opener = SandboxBrokerOpener(incoming["request_id"], input_stream, output_stream)
    adapter = PublicGetAdapter(
        QVerisModelGatewaySemanticResolver(api_key="sandbox", model=descriptor["model"], opener=opener),
        QVerisToolGateway(api_key="sandbox", opener=opener),
        agent_variant_id=descriptor["agent_variant_id"], agent_version=descriptor["agent_version"],
        get_variant_id=descriptor["get_variant_id"], get_version=descriptor["get_version"],
        model_identifier=descriptor["model"], model_version=descriptor["model_version"],
        model_config_digest=descriptor["model_config_digest"],
    )
    result = adapter.run(incoming["query"], request_id=incoming["request_id"], idempotency_key="idem-" + incoming["request_id"])
    output_stream.write(json.dumps(result.public_response, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    output_stream.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one public GET inside a network-disabled sandbox.")
    parser.add_argument("--descriptor", default="/app/sandbox-get-runtime-config.v1.json")
    return run_stdio(parser.parse_args(argv).descriptor)


if __name__ == "__main__":
    raise SystemExit(main())

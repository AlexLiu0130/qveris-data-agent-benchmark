#!/usr/bin/env python3
"""Create the deliberately small Docker build context for the sandbox GET."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "__init__.py", "sandbox_get_entry.py", "sandbox_broker.py", "public_get.py",
    "qveris_model_gateway.py", "qveris_tool_gateway.py", "provider_payload.py",
    "response_contract.py", "run_backend.py", "runtime_catalog.py", "tls.py",
)
RUNTIME_CONFIG_FIELDS = {
    "schema_version", "model", "agent_variant_id", "agent_version",
    "get_variant_id", "get_version", "model_version", "model_config_digest",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage an Oracle-free Docker context for the sandbox GET entry.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--runtime-config", required=True, help="sandbox-get-runtime-config/v1 JSON; copied into the image")
    args = parser.parse_args()
    output, config = Path(args.output_dir).resolve(), Path(args.runtime_config).resolve()
    if output == ROOT or ROOT in output.parents or output.exists() and any(output.iterdir()):
        parser.error("--output-dir must be an empty directory outside this repository")
    value = json.loads(config.read_text(encoding="utf-8"))
    if (
        type(value) is not dict
        or set(value) != RUNTIME_CONFIG_FIELDS
        or value.get("schema_version") != "sandbox-get-runtime-config/v1"
        or any(type(value[name]) is not str or SAFE_ID.fullmatch(value[name]) is None for name in RUNTIME_CONFIG_FIELDS - {"schema_version", "model_config_digest"})
        or type(value.get("model_config_digest")) is not str
        or SHA256.fullmatch(value["model_config_digest"]) is None
    ):
        parser.error("--runtime-config must be sandbox-get-runtime-config/v1")
    package = output / "qveris_benchmark"
    package.mkdir(parents=True)
    for name in MODULES:
        shutil.copy2(ROOT / "src" / "qveris_benchmark" / name, package / name)
    shutil.copy2(config, output / "sandbox-get-runtime-config.v1.json")
    shutil.copy2(ROOT / "runner" / "sandbox-runtime" / "Dockerfile", output / "Dockerfile")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

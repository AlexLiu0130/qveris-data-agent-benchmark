import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.contracts import AuthMode, Domain, SemanticPlan
from qveris_benchmark.manifest import (
    TOOL_MANIFEST_SCHEMA_VERSION,
    Manifest,
    PlanManifestMismatch,
    ToolManifestEntry,
    UnknownToolAlias,
)


def entry(alias: str = "quote", domain: Domain = Domain.REALTIME_QUOTE) -> ToolManifestEntry:
    return ToolManifestEntry(alias, "provider.quote", {"type": "object"}, {"type": "object"}, domain, AuthMode.BEARER)


def manifest(*entries: ToolManifestEntry) -> Manifest:
    return Manifest.from_entries(entries, schema_version=TOOL_MANIFEST_SCHEMA_VERSION)


class ManifestTests(unittest.TestCase):
    def test_resolves_alias_to_provider_contract(self) -> None:
        runtime_manifest = manifest(entry())
        resolved = runtime_manifest.resolve("quote")
        self.assertEqual(resolved.tool_id, "provider.quote")
        self.assertEqual(resolved.auth_mode, AuthMode.BEARER)

    def test_rejects_unknown_alias(self) -> None:
        with self.assertRaises(UnknownToolAlias):
            manifest(entry()).resolve("missing")

    def test_requires_ready_plan_domain_to_match_manifest(self) -> None:
        runtime_manifest = manifest(entry())
        plan = SemanticPlan.from_json(
            '{"status":"READY","domain":"historical_price","tool_alias":"quote","request":{}}'
        )
        with self.assertRaises(PlanManifestMismatch):
            runtime_manifest.entry_for(plan)

    def test_requires_the_runtime_schema_version(self) -> None:
        with self.assertRaises(TypeError):
            Manifest.from_entries([entry()])
        with self.assertRaises(ValueError):
            Manifest.from_entries([entry()], schema_version="paid-pilot.v1")

    def test_rejects_unimplemented_auth_modes(self) -> None:
        with self.assertRaises(ValueError):
            AuthMode("api_key")

    def test_rejects_paid_pilot_artifacts_as_runtime_entries(self) -> None:
        paid_pilot_artifact = {"execution_policy": {"approval_id": "approval-1"}}
        with self.assertRaises(TypeError):
            Manifest.from_entries([paid_pilot_artifact], schema_version=TOOL_MANIFEST_SCHEMA_VERSION)

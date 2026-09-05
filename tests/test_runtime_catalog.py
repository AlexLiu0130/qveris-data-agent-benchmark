from __future__ import annotations

from collections import Counter
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.runtime_catalog import (
    BASELINE_ENTRY_COUNT,
    DISPATCHABLE_ENTRY_COUNT,
    EXTENSION_ENTRY_COUNT,
    RUNTIME_CATALOG,
    catalog_entry,
)
from qveris_benchmark.domain_routes_financial import SUPPORTED_KEYS as FINANCIAL_ROUTE_TOOLS
from qveris_benchmark.domain_routes_historical import SUPPORTED_KEYS as HISTORICAL_ROUTE_TOOLS
from qveris_benchmark.domain_routes_realtime import SUPPORTED_KEYS as REALTIME_ROUTE_TOOLS


class RuntimeCatalogTests(unittest.TestCase):
    def test_frozen_84_cell_baseline_is_preserved(self):
        baseline = tuple(
            entry for entry in RUNTIME_CATALOG.values()
            if entry.market in {"US", "SSE", "SZSE", "HKEX"}
            and ".weekly_bars." not in entry.scenario
            and ".monthly_bars." not in entry.scenario
        )
        self.assertEqual((len(baseline), BASELINE_ENTRY_COUNT), (84, 84))
        self.assertEqual(Counter(entry.registry_state for entry in baseline), {
            "provisional_basic": 59,
            "unverified": 7,
            "gap": 9,
            "not_applicable": 3,
            "rejected": 6,
        })
        self.assertEqual(Counter(entry.market for entry in baseline), {
            "US": 21,
            "SSE": 21,
            "SZSE": 21,
            "HKEX": 21,
        })

    def test_runtime_extensions_are_explicit_for_each_unmapped_or_admitted_route(self):
        extension_keys = {
            (market, scenario)
            for market in ("JP", "GB", "DE")
            for scenario in (
                "historical.daily_bars.unadjusted.v1",
                "historical.daily_bars.adjusted.v1",
                "financial.income_statement.standard.specified_period.v1",
                "financial.balance_sheet.standard.specified_period.v1",
                "financial.cash_flow.standard.specified_period.v1",
                "financial.direct_line_items.specified_period.v1",
                "financial.latest_filed.direct_metric.v1",
            )
        } | {
            (market, scenario)
            for market in ("US", "SSE", "SZSE", "HKEX")
            for scenario in (
                "historical.weekly_bars.unadjusted.v1",
                "historical.monthly_bars.unadjusted.v1",
            )
        }
        self.assertEqual((len(extension_keys), EXTENSION_ENTRY_COUNT), (29, 29))
        for key in extension_keys:
            entry = RUNTIME_CATALOG[key]
            with self.subTest(key=key):
                self.assertEqual(entry.registry_state, "unverified")
                if entry.disposition == "unsupported":
                    self.assertEqual((entry.reason, entry.tool_ids, entry.evidence), ("route_unmapped", (), None))

    def test_dispatchable_cells_keep_a_frozen_tool_and_evidence_contract(self):
        for entry in RUNTIME_CATALOG.values():
            with self.subTest(entry=(entry.market, entry.scenario)):
                self.assertTrue(entry.reason)
                if entry.disposition == "dispatchable":
                    self.assertTrue(entry.tool_ids)
                    self.assertTrue(entry.evidence)

    def test_dispatchable_catalog_is_exactly_the_domain_route_contract(self):
        expected = {}
        for routes in (FINANCIAL_ROUTE_TOOLS, HISTORICAL_ROUTE_TOOLS, REALTIME_ROUTE_TOOLS):
            for key, tools in routes.items():
                self.assertNotIn(key, expected)
                expected[key] = (tools,) if type(tools) is str else tools
        admitted = {
            key: entry.tool_ids
            for key, entry in RUNTIME_CATALOG.items()
            if entry.disposition == "dispatchable"
        }
        self.assertEqual(len(admitted), DISPATCHABLE_ENTRY_COUNT)
        self.assertEqual(admitted, expected)

    def test_catalog_lookup_is_exact(self):
        entry = catalog_entry("JP", "historical.daily_bars.adjusted.v1")
        self.assertIsNotNone(entry)
        self.assertEqual((entry.disposition, entry.reason), ("unsupported", "route_unmapped"))
        self.assertIsNone(catalog_entry("jp", "historical.daily_bars.adjusted.v1"))


if __name__ == "__main__":
    unittest.main()

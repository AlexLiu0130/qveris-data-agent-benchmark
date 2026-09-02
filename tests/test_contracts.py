import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.contracts import Domain, PlanStatus, SemanticPlan
from qveris_benchmark.strict_json import StrictJSONError


class SemanticPlanTests(unittest.TestCase):
    def test_accepts_ready_plan_for_each_domain(self) -> None:
        for domain in Domain:
            plan = SemanticPlan.from_json(
                '{"status":"READY","domain":"%s","tool_alias":"lookup","request":{}}'
                % domain.value
            )
            self.assertEqual(plan.status, PlanStatus.READY)
            self.assertEqual(plan.domain, domain)

    def test_accepts_clarify_and_reject(self) -> None:
        for status in (PlanStatus.CLARIFY, PlanStatus.REJECT):
            plan = SemanticPlan.from_json('{"status":"%s","message":"reason"}' % status.value)
            self.assertEqual(plan.status, status)

    def test_rejects_unknown_status(self) -> None:
        with self.assertRaises(StrictJSONError):
            SemanticPlan.from_json('{"status":"RUN","message":"no"}')

    def test_rejects_extra_field_and_status_conflict(self) -> None:
        with self.assertRaises(StrictJSONError):
            SemanticPlan.from_json(
                '{"status":"READY","domain":"realtime_quote","tool_alias":"lookup","request":{},"tool_id":"x"}'
            )
        with self.assertRaises(StrictJSONError):
            SemanticPlan.from_json('{"status":"CLARIFY","plan_status":"READY","message":"reason"}')

    def test_rejects_duplicate_key(self) -> None:
        with self.assertRaises(StrictJSONError):
            SemanticPlan.from_json('{"status":"READY","status":"CLARIFY","message":"reason"}')

    def test_rejects_type_coercion(self) -> None:
        with self.assertRaises(StrictJSONError):
            SemanticPlan.from_json('{"status":"READY","domain":"realtime_quote","tool_alias":1,"request":{}}')

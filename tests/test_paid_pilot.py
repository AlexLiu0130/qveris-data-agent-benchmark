import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))

import qveris_paid_pilot as pilot


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, size):  # noqa: ARG002
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return FakeResponse(self.response)


class SlowOpener(FakeOpener):
    def __init__(self):
        super().__init__({"success": True, "actual_cost": 1})
        self.started = threading.Event()
        self.release = threading.Event()

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        self.started.set()
        self.release.wait(2)
        return FakeResponse(self.response)


def candidate(alias="quote", cost=1, status="approved_for_pilot", arguments=None):
    return {
        "alias": alias,
        "tool_id": "provider." + alias,
        "call_parameters": {"symbol": "AAPL"} if arguments is None else arguments,
        "catalog_expected_credits": cost,
        "live_status": status,
    }


class PaidPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.manifest = self.root / "manifest.json"
        self.plan = self.root / "plan.json"
        self.env = self.root / ".env.local"
        self.ledger = self.root / "ledger.jsonl"
        self.approval_digest = self.root / "approval.digest"
        self.env.write_text("QVERIS_API_KEY=unit-secret\n", encoding="utf-8")
        self.write_approved()

    def tearDown(self):
        self.temp.cleanup()

    def write_approved(self, item=None, case_id="case-1", total=10, cases=None):
        items = [candidate() if item is None else item] if type(item) is not list else item
        cases = [{"case_id": case_id, "alias": items[0]["alias"], "arguments": items[0]["call_parameters"], "expected_cost": items[0]["catalog_expected_credits"], "approval_id": "approval-1"}] if cases is None else cases
        manifest = {"execution_policy": {"live_status": "approved_for_pilot", "approval_id": "approval-1", "connector_protocol_version": pilot.CONNECTOR_PROTOCOL_VERSION, "total_budget_credits": total}, "domains": {"quotes": {"primary_candidates": items}}}
        plan = {"approval_id": "approval-1", "manifest_hash": pilot.manifest_hash(manifest), "connector_protocol_version": pilot.CONNECTOR_PROTOCOL_VERSION, "cases": cases}
        manifest["execution_policy"]["approved_plan_hash"] = pilot.canonical_hash(plan)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        self.approval_digest.write_text(pilot.canonical_hash(plan) + "\n", encoding="utf-8")
        os.chmod(self.approval_digest, 0o600)

    def args(self, **changes):
        values = {"manifest": self.manifest, "plan": self.plan, "case": "case-1", "idempotency_key": "pilot-1", "ledger": self.ledger, "private_result_dir": self.root / "private", "env_file": self.env, "timeout": 1.0, "execute": True, "approval_digest_file": self.approval_digest}
        values.update(changes)
        return type("Args", (), values)()

    def test_one_post_and_no_secret_or_raw_payload_in_ledger(self):
        opener = FakeOpener({"success": True, "execution_id": "exec-1", "actual_cost": 1, "remaining_credits": 9, "data": {"price": 987.654}})
        result = pilot.run(self.args(), opener)
        saved = self.ledger.read_text(encoding="utf-8")
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result["outcome"], "success")
        self.assertNotIn("unit-secret", saved)
        self.assertNotIn("Authorization", saved)
        self.assertNotIn("987.654", saved)
        self.assertIn('"execution_id":"exec-1"', saved)

    def test_execute_contract_uses_parameters_body_and_required_headers(self):
        opener = FakeOpener({"success": True, "actual_cost": 1})
        pilot.run(self.args(), opener)
        request, _timeout = opener.calls[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(request.full_url, "https://qveris.ai/api/v1/tools/execute?tool_id=provider.quote")
        self.assertEqual(json.loads(request.data), {"parameters": {"symbol": "AAPL"}})
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["authorization"].split(" ", 1)[0], "Bearer")
        self.assertGreater(len(headers["authorization"]), len("Bearer "))
        self.assertEqual(headers["idempotency-key"], "pilot-1")

    def test_cli_defaults_to_dry_run_with_zero_opener_calls(self):
        args = pilot.parse_args(["--manifest", str(self.manifest), "--plan", str(self.plan), "--case", "case-1", "--idempotency-key", "cli-default"])
        opener = FakeOpener({"success": True})
        self.assertFalse(args.execute)
        self.assertIsNone(args.approval_digest_file)
        self.assertEqual(pilot.run(args, opener)["outcome"], "dry_run")
        self.assertEqual(opener.calls, [])

    def test_execute_requires_external_digest_before_opener_even_after_repo_rewrite(self):
        self.write_approved(item=candidate(arguments={"symbol": "MSFT"}))
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "approval-digest-file"):
            pilot.run(self.args(approval_digest_file=None), opener)
        self.assertEqual(opener.calls, [])

    def test_execute_url_percent_encodes_reserved_tool_id_characters(self):
        opener = FakeOpener({"success": True})
        pilot.post(opener, "provider/quote?x=1&y=2", {"symbol": "AAPL"}, "private-test-key", "key-1", 1)
        request, _timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://qveris.ai/api/v1/tools/execute?tool_id=provider%2Fquote%3Fx%3D1%26y%3D2")

    def test_private_json_evidence_is_hashed_private_and_not_in_ledger(self):
        payload = {"success": False, "actual_cost": 1, "data": {"value": "response-secret"}}
        pilot.run(self.args(), FakeOpener(payload))
        terminal = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()][-1]
        private = self.root / "private" / terminal["private_result"]
        self.assertEqual(terminal["private_result_status"], "saved")
        self.assertEqual(private.name, "case-1-%s.json" % terminal["response_sha256"])
        self.assertEqual(json.loads(private.read_text(encoding="utf-8")), payload)
        self.assertEqual(stat.S_IMODE((self.root / "private").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
        self.assertNotIn("response-secret", self.ledger.read_text(encoding="utf-8"))
        self.assertNotIn("unit-secret", private.read_text(encoding="utf-8"))

    def test_private_evidence_rejects_unsafe_case_and_marks_non_json(self):
        self.write_approved(case_id="../escape")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "unsafe"):
            pilot.run(self.args(case="../escape"), opener)
        self.assertEqual(opener.calls, [])
        self.write_approved()
        pilot.run(self.args(), FakeOpener(b"not-json"))
        terminal = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()][-1]
        self.assertEqual(terminal["private_result_status"], "not_json")
        self.assertIsNone(terminal["private_result"])

    def test_pending_candidate_is_rejected_before_post(self):
        self.write_approved(candidate(status="pending"))
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "not approved"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_unknown_or_unbounded_cost_is_rejected_before_post(self):
        self.write_approved(candidate(cost={"minimum": 1, "maximum": None}))
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "unknown or unbounded"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_plan_tamper_breaks_hash_binding_before_post(self):
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["cases"][0]["arguments"] = {"symbol": "MSFT"}
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "hashes do not bind"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_legacy_plan_without_protocol_version_fails_before_post(self):
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan.pop("connector_protocol_version")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["execution_policy"]["approved_plan_hash"] = pilot.canonical_hash(plan)
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "connector protocols"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_manifest_protocol_mismatch_fails_before_post(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["execution_policy"]["connector_protocol_version"] = "qveris.execute.arguments.v0"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "connector protocols"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_manifest_protocol_at_root_is_not_a_compatible_second_location(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["connector_protocol_version"] = pilot.CONNECTOR_PROTOCOL_VERSION
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "connector protocols"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_actual_cost_over_approved_total_is_budget_violation(self):
        result = pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 11}))
        terminal = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()][-1]
        self.assertEqual(result["outcome"], "budget_violation")
        self.assertEqual(terminal["business_status"], "budget_violation")
        self.assertEqual(terminal["receipt_status"], "budget_violation")
        self.assertEqual(terminal["actual_credits"], 11.0)

    def test_ledger_failure_makes_zero_calls(self):
        opener = FakeOpener({"success": True})
        original = pilot.append_ledger
        pilot.append_ledger = lambda *args: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(OSError):
                pilot.run(self.args(), opener)
        finally:
            pilot.append_ledger = original
        self.assertEqual(opener.calls, [])

    def test_timeout_is_uncertain_and_same_case_cannot_be_resent(self):
        opener = FakeOpener(TimeoutError())
        self.assertEqual(pilot.run(self.args(), opener)["outcome"], "uncertain")
        with self.assertRaisesRegex(pilot.PilotError, "do not resend"):
            pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 1}))
        self.assertEqual(len(opener.calls), 1)

    def test_success_false_and_receipt_missing_are_explicit(self):
        self.assertEqual(pilot.run(self.args(), FakeOpener({"success": False, "actual_cost": 1}))["outcome"], "failed")
        self.ledger.unlink()
        self.assertEqual(pilot.run(self.args(idempotency_key="pilot-2"), FakeOpener({"success": True}))["outcome"], "receipt_missing")

    def test_terminal_error_metadata_is_sanitized_and_shape_only(self):
        payload = {
            "success": False,
            "error_code": "BAD_INPUT",
            "error_message": "Bearer unit-secret token=secret-value api_key=also-secret",
            "message": "x" * 400,
            "Authorization": "never-store",
            "data": {"price": 987.654},
        }
        pilot.run(self.args(), FakeOpener(payload))
        terminal = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()][-1]
        written = json.dumps(terminal)
        self.assertEqual(terminal["business_success_raw"], False)
        self.assertEqual(terminal["result_shape"], "object")
        self.assertEqual(terminal["result_top_level_keys"], ["data", "error_code", "error_message", "message", "success"])
        self.assertEqual(terminal["sanitized_error"]["error_code"], "BAD_INPUT")
        self.assertLessEqual(len(terminal["sanitized_error"]["message"]), 300)
        self.assertNotIn("unit-secret", written)
        self.assertNotIn("secret-value", written)
        self.assertNotIn("also-secret", written)
        self.assertNotIn("987.654", written)
        self.assertNotIn("never-store", written)

    def test_old_v1_planned_cost_does_not_consume_new_v2_budget(self):
        self.write_approved(total=1)
        old = {"record_type": "planned", "at": "2026-01-01T00:00:00Z", "case_id": "old-case", "alias": "old", "tool_id": "provider.old", "arguments_hash": "old-args", "expected_credits": 1, "variable_cost": False, "idempotency_key": "old-key"}
        self.ledger.write_text(json.dumps(old) + "\n", encoding="utf-8")
        opener = FakeOpener({"success": True, "actual_cost": 1})
        self.assertEqual(pilot.run(self.args(), opener)["outcome"], "success")
        self.assertEqual(len(opener.calls), 1)

    def test_same_plan_is_rejected_on_second_attempt(self):
        self.assertEqual(pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 1}))["outcome"], "success")
        with self.assertRaisesRegex(pilot.PilotError, "do not resend"):
            pilot.run(self.args(idempotency_key="pilot-2"), FakeOpener({"success": True, "actual_cost": 1}))

    def test_two_cases_in_one_plan_run_once_each_with_cumulative_budget(self):
        first, second = candidate("quote-one", 24.2), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 24.2, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], total=25.2, cases=cases)
        out_of_order = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "prior plan case"):
            pilot.run(self.args(case="case-two", idempotency_key="batch-two"), out_of_order)
        first_opener, second_opener = FakeOpener({"success": True, "actual_cost": 24.2}), FakeOpener({"success": True, "actual_cost": 1})
        self.assertEqual(pilot.run(self.args(case="case-one", idempotency_key="batch-one"), first_opener)["outcome"], "success")
        self.assertEqual(pilot.run(self.args(case="case-two", idempotency_key="batch-two"), second_opener)["outcome"], "success")
        third_opener = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "do not resend"):
            pilot.run(self.args(case="case-one", idempotency_key="batch-three"), third_opener)
        self.assertEqual(len(first_opener.calls), 1)
        self.assertEqual(len(second_opener.calls), 1)
        self.assertEqual(third_opener.calls, [])
        self.assertEqual(out_of_order.calls, [])

    def test_second_case_over_plan_budget_makes_zero_post(self):
        first, second = candidate("quote-one", 24.2), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 24.2, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], total=25, cases=cases)
        self.assertEqual(pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener({"success": True, "actual_cost": 24.2}))["outcome"], "success")
        opener = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "budget"):
            pilot.run(self.args(case="case-two", idempotency_key="batch-two"), opener)
        self.assertEqual(opener.calls, [])

    def test_prior_actual_cost_above_expected_blocks_second_case_budget(self):
        first, second = candidate("quote-one", 1), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], total=2.5, cases=cases)
        self.assertEqual(pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener({"success": True, "actual_cost": 2}))["outcome"], "success")
        opener = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "budget"):
            pilot.run(self.args(case="case-two", idempotency_key="batch-two"), opener)
        self.assertEqual(opener.calls, [])

    def test_second_receipt_uses_cumulative_actual_cost_for_budget_violation(self):
        first, second = candidate("quote-one", 24.2), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 24.2, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], total=25.2, cases=cases)
        pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener({"success": True, "actual_cost": 24.2}))
        violated = pilot.run(self.args(case="case-two", idempotency_key="batch-two"), FakeOpener({"success": True, "actual_cost": 2}))
        terminal = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()][-1]
        self.assertEqual(violated["outcome"], "budget_violation")
        self.assertEqual(terminal["actual_credits"], 2.0)
        self.assertEqual(terminal["receipt_status"], "budget_violation")
        self.ledger.unlink()
        self.write_approved(item=[first, second], total=25.2, cases=cases)
        pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener({"success": True, "actual_cost": 24.2}))
        self.assertEqual(pilot.run(self.args(case="case-two", idempotency_key="batch-two"), FakeOpener({"success": True, "actual_cost": 1}))["outcome"], "success")

    def test_non_successful_or_invalid_prior_receipt_blocks_second_case(self):
        first, second = candidate("quote-one", 1), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        for payload in ({"success": True}, {"success": False, "actual_cost": 1}, {"success": True, "actual_cost": 3}, TimeoutError(), {"success": True, "actual_cost": -1}, {"success": True, "actual_cost": float("nan")}):
            with self.subTest(payload=payload):
                self.ledger.unlink(missing_ok=True)
                self.write_approved(item=[first, second], total=2, cases=cases)
                pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener(payload))
                opener = FakeOpener({"success": True, "actual_cost": 1})
                with self.assertRaisesRegex(pilot.PilotError, "prior plan case"):
                    pilot.run(self.args(case="case-two", idempotency_key="batch-two"), opener)
                self.assertEqual(opener.calls, [])

    def test_cross_plan_same_case_is_rejected(self):
        self.assertEqual(pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 1}))["outcome"], "success")
        self.write_approved(item=candidate(arguments={"symbol": "MSFT"}))
        with self.assertRaisesRegex(pilot.PilotError, "do not resend"):
            pilot.run(self.args(idempotency_key="pilot-2"), FakeOpener({"success": True, "actual_cost": 1}))

    def test_corrupt_ledger_fails_closed(self):
        self.ledger.write_text('{"record_type":"planned"', encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "truncated"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_unknown_ledger_state_fails_closed(self):
        bad = {"record_type": "mystery", "case_id": "old", "alias": "old", "tool_id": "old", "arguments_hash": "old", "manifest_hash": "old", "plan_hash": "old", "approval_id": "old", "idempotency_key": "old", "expected_credits": 1}
        self.ledger.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "unknown state"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_cross_thread_lock_allows_one_post_for_one_case(self):
        opener = SlowOpener()
        errors = []

        def invoke(key):
            try:
                pilot.run(self.args(idempotency_key=key), opener)
            except Exception as error:  # the duplicate must fail only after the first settles
                errors.append(error)

        first = threading.Thread(target=invoke, args=("pilot-1",))
        second = threading.Thread(target=invoke, args=("pilot-2",))
        first.start()
        self.assertTrue(opener.started.wait(1))
        second.start()
        opener.release.set()
        first.join(2)
        second.join(2)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], pilot.PilotError)

    def test_v3_dry_run_validates_approved_binding_without_key_or_ledger(self):
        self.env.unlink()
        opener = FakeOpener({"success": True})
        result = pilot.run(self.args(execute=False), opener)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "dry_run")
        self.assertEqual(plan["connector_protocol_version"], pilot.CONNECTOR_PROTOCOL_VERSION)
        self.assertEqual(manifest["execution_policy"]["approved_plan_hash"], result["plan_hash"])
        self.assertFalse(self.ledger.exists())
        self.assertEqual(opener.calls, [])


if __name__ == "__main__":
    unittest.main()

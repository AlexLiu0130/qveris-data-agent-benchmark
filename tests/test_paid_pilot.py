import json
import hashlib
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


def candidate(alias="quote", cost=1, status="approved_for_pilot", arguments=None, quality_validator_spec=None):
    item = {
        "alias": alias,
        "tool_id": "provider." + alias,
        "call_parameters": {"symbol": "AAPL"} if arguments is None else arguments,
        "catalog_expected_credits": cost,
        "live_status": status,
    }
    if quality_validator_spec is not None:
        item["quality_validator_spec"] = quality_validator_spec
    return item


class PaidPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.manifest = self.root / "manifest.json"
        self.plan = self.root / "plan.json"
        self.env = self.root / ".env.local"
        self.ledger_base = self.root / "ledger.jsonl"
        self.private_result_base = self.root / "private"
        self.ledger = self.ledger_base
        self.private_result_dir = self.private_result_base
        self.approval_digest = self.root / "approval.digest"
        self.env.write_text("QVERIS_API_KEY=unit-secret\n", encoding="utf-8")
        self.write_approved()

    def tearDown(self):
        self.temp.cleanup()

    def write_approved(self, item=None, case_id="case-1", total=10, cases=None, frozen_case_order=None, provenance_artifacts=None):
        items = [candidate() if item is None else item] if type(item) is not list else item
        cases = [{"case_id": case_id, "alias": items[0]["alias"], "arguments": items[0]["call_parameters"], "expected_cost": items[0]["catalog_expected_credits"], "approval_id": "approval-1", "idempotency_key": "pilot-1"}] if cases is None else [{**row, "idempotency_key": row.get("idempotency_key", "batch-" + row["case_id"].removeprefix("case-"))} for row in cases]
        manifest = {"execution_policy": {"live_status": "approved_for_pilot", "approval_id": "approval-1", "connector_protocol_version": pilot.CONNECTOR_PROTOCOL_VERSION, "total_budget_credits": total}, "domains": {"quotes": {"primary_candidates": items}}}
        if frozen_case_order is not None:
            manifest["frozen_case_order"] = frozen_case_order
        if provenance_artifacts is not None:
            manifest["provenance_artifacts"] = provenance_artifacts
        plan = {"approval_id": "approval-1", "manifest_hash": pilot.manifest_hash(manifest), "connector_protocol_version": pilot.CONNECTOR_PROTOCOL_VERSION, "cases": cases}
        manifest["execution_policy"]["approved_plan_hash"] = pilot.canonical_hash(plan)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        self.approval_digest.write_text(pilot.canonical_hash(plan) + "\n", encoding="utf-8")
        os.chmod(self.approval_digest, 0o600)
        self.ledger, self.private_result_dir = pilot._plan_storage_paths(self.ledger_base, self.private_result_base, pilot.canonical_hash(plan))

    def args(self, **changes):
        values = {"manifest": self.manifest, "plan": self.plan, "case": "case-1", "idempotency_key": "pilot-1", "ledger": self.ledger_base, "private_result_dir": self.private_result_base, "env_file": self.env, "timeout": 1.0, "execute": True, "approval_digest_file": self.approval_digest}
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

    def test_cli_idempotency_override_is_rejected_before_post(self):
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "does not match the frozen plan"):
            pilot.run(self.args(idempotency_key="replacement-key"), opener)
        self.assertEqual(opener.calls, [])

    def test_legacy_plan_without_frozen_idempotency_key_is_rejected_before_post(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["cases"][0].pop("idempotency_key")
        plan["manifest_hash"] = pilot.manifest_hash(manifest)
        manifest["execution_policy"]["approved_plan_hash"] = pilot.canonical_hash(plan)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        self.approval_digest.write_text(pilot.canonical_hash(plan) + "\n", encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "exact execution fields"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_frozen_case_order_must_match_plan_before_post(self):
        first, second = candidate("quote-one"), candidate("quote-two")
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], cases=cases, frozen_case_order=["case-two", "case-one"])
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "frozen case order"):
            pilot.run(self.args(case="case-one", idempotency_key="batch-one"), opener)
        self.assertEqual(opener.calls, [])

    def test_provenance_digest_mismatch_is_rejected_before_post(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            artifact = pathlib.Path(directory) / "receipt.json"
            artifact.write_text("changed", encoding="utf-8")
            self.write_approved(provenance_artifacts=[{"path": str(artifact), "sha256": hashlib.sha256(b"approved").hexdigest()}])
            opener = FakeOpener({"success": True})
            with self.assertRaisesRegex(pilot.PilotError, "provenance artifact digest"):
                pilot.run(self.args(), opener)
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
        private = self.private_result_dir / terminal["private_result"]
        self.assertEqual(terminal["private_result_status"], "saved")
        self.assertEqual(private.name, "case-1-%s.json" % terminal["response_sha256"])
        self.assertEqual(json.loads(private.read_text(encoding="utf-8")), payload)
        self.assertEqual(stat.S_IMODE(self.private_result_dir.stat().st_mode), 0o700)
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

    def test_nonfinite_manifest_plan_or_budget_cost_is_rejected_before_post(self):
        for location in ("manifest", "plan", "budget"):
            for nonfinite in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(location=location, nonfinite=nonfinite):
                    self.write_approved()
                    manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
                    plan = json.loads(self.plan.read_text(encoding="utf-8"))
                    if location == "manifest":
                        manifest["domains"]["quotes"]["primary_candidates"][0]["catalog_expected_credits"] = nonfinite
                    elif location == "plan":
                        plan["cases"][0]["expected_cost"] = nonfinite
                    else:
                        manifest["execution_policy"]["total_budget_credits"] = nonfinite
                    plan["manifest_hash"] = pilot.manifest_hash(manifest)
                    manifest["execution_policy"]["approved_plan_hash"] = pilot.canonical_hash(plan)
                    self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
                    self.plan.write_text(json.dumps(plan), encoding="utf-8")
                    self.approval_digest.write_text(pilot.canonical_hash(plan) + "\n", encoding="utf-8")
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
        self.assertEqual(pilot.run(self.args(), FakeOpener({"success": True}))["outcome"], "receipt_missing")

    def test_quality_validator_passes_all_supported_structural_checks_without_raw_ledger_values(self):
        arguments = {"symbol": "AAPL", "trade_date": "2026-09-01", "period": "Q2"}
        spec = {
            "data_path": "data",
            "nonempty": True,
            "required_keys": ["symbol", "trade_date", "period", "price", "open", "high", "low", "close", "volume", "timestamp", "currency", "unit"],
            "finite_numeric_fields": ["price"],
            "identity": {"field": "symbol", "argument": "symbol", "mode": "exact"},
            "date": {"field": "trade_date", "argument": "trade_date", "mode": "exact"},
            "period": {"field": "period", "argument": "period", "mode": "exact"},
            "ohlc": True,
            "timestamp_fields": ["timestamp"],
            "financial_fields": ["period", "currency", "unit"],
        }
        self.write_approved(candidate(arguments=arguments, quality_validator_spec=spec))
        payload = {"success": True, "actual_cost": 1, "data": {"symbol": "AAPL", "trade_date": "2026-09-01", "period": "Q2", "price": 123.45, "open": 120, "high": 125, "low": 119, "close": 123, "volume": 10, "timestamp": "2026-09-01T20:00:00Z", "currency": "USD", "unit": "millions"}}
        self.assertEqual(pilot.run(self.args(), FakeOpener(payload))["outcome"], "success")
        terminal = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(terminal["quality_status"], "passed")
        self.assertIn("ohlc", terminal["quality_checks"])
        self.assertNotIn("123.45", self.ledger.read_text(encoding="utf-8"))
        self.assertNotIn("millions", self.ledger.read_text(encoding="utf-8"))

    def test_quality_request_bound_identity_allows_absent_response_identity(self):
        spec = {"data_path": "data", "nonempty": True, "identity": {"field": "symbol", "argument": "symbol", "mode": "request_bound"}}
        quality = pilot.validate_quality({"success": True, "data": {"price": 1}}, {"quality_validator_spec": spec}, {"symbol": "AAPL"})
        self.assertEqual(quality["status"], "passed")
        self.assertIn("identity", quality["checks"])

    def test_quality_decimal_fields_and_ohlc_accept_finite_numeric_strings_only(self):
        spec = {"data_path": "data", "finite_decimal_fields": ["price"], "ohlc": True}
        valid = {"success": True, "data": {"price": "123.45", "open": "120", "high": "125", "low": "119", "close": "123", "volume": "10"}}
        passed = pilot.validate_quality(valid, {"quality_validator_spec": spec}, {})
        self.assertEqual(passed["status"], "passed")
        self.assertIn("ohlc", passed["checks"])
        invalid = {"success": True, "data": {"price": "NaN", "open": "120", "high": "125", "low": "119", "close": "123", "volume": "10"}}
        failed = pilot.validate_quality(invalid, {"quality_validator_spec": spec}, {})
        self.assertEqual(failed["status"], "failed")
        self.assertIn("finite_decimal_fields_invalid", failed["failure_codes"])

    def test_quality_iso_date_exact_accepts_date_only_or_timestamp_and_rejects_other_dates(self):
        spec = {"data_path": "data", "nonempty": True, "date": {"field": "date", "argument": "startDate", "mode": "iso_date_exact"}}
        candidate = {"quality_validator_spec": spec}
        arguments = {"startDate": "2026-09-01"}
        for value in ("2026-09-01", "2026-09-01T00:00:00.000Z"):
            with self.subTest(value=value):
                self.assertEqual(pilot.validate_quality({"data": {"date": value}}, candidate, arguments)["status"], "passed")
        self.assertEqual(pilot.validate_quality({"data": {"date": "2026-09-02"}}, candidate, arguments)["status"], "failed")

    def test_quality_nonempty_rejects_an_empty_object(self):
        spec = {"data_path": "result.data", "nonempty": True}
        quality = pilot.validate_quality({"success": True, "result": {"data": {}}}, {"quality_validator_spec": spec}, {})
        self.assertEqual(quality["status"], "failed")
        self.assertEqual(quality["failure_codes"], ["data_empty"])

    def test_quality_failure_marks_case_failed_and_blocks_following_case(self):
        spec = {"data_path": "data", "required_keys": ["price"], "finite_numeric_fields": ["price"], "nonempty": True}
        first, second = candidate("quote-one", quality_validator_spec=spec), candidate("quote-two")
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], total=2, cases=cases)
        self.assertEqual(pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener({"success": True, "actual_cost": 1, "data": {"price": float("nan")}}))["outcome"], "failed")
        terminal = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(terminal["business_status"], "quality_failed")
        self.assertEqual(terminal["quality_status"], "failed")
        blocked = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "prior plan case"):
            pilot.run(self.args(case="case-two", idempotency_key="batch-two"), blocked)
        self.assertEqual(blocked.calls, [])

    def test_invalid_quality_spec_is_rejected_before_post(self):
        self.write_approved(candidate(quality_validator_spec={"data_path": "data", "unsupported": True}))
        opener = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "quality validator spec"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

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
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps(old) + "\n", encoding="utf-8")
        opener = FakeOpener({"success": True, "actual_cost": 1})
        self.assertEqual(pilot.run(self.args(), opener)["outcome"], "success")
        self.assertEqual(len(opener.calls), 1)

    def test_same_plan_is_rejected_on_second_attempt(self):
        self.assertEqual(pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 1}))["outcome"], "success")
        with self.assertRaisesRegex(pilot.PilotError, "do not resend"):
            pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 1}))

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
            pilot.run(self.args(case="case-one", idempotency_key="batch-one"), third_opener)
        self.assertEqual(len(first_opener.calls), 1)
        self.assertEqual(len(second_opener.calls), 1)
        self.assertEqual(dict((name.lower(), value) for name, value in first_opener.calls[0][0].header_items())["idempotency-key"], "batch-one")
        self.assertEqual(dict((name.lower(), value) for name, value in second_opener.calls[0][0].header_items())["idempotency-key"], "batch-two")
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

    def test_duplicate_frozen_idempotency_keys_are_rejected_before_post(self):
        first, second = candidate("quote-one"), candidate("quote-two")
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1", "idempotency_key": "same-key"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1", "idempotency_key": "same-key"},
        ]
        self.write_approved(item=[first, second], total=2, cases=cases)
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "idempotency keys must be unique"):
            pilot.run(self.args(case="case-one", idempotency_key="same-key"), opener)
        self.assertEqual(opener.calls, [])

    def test_prior_actual_cost_above_expected_blocks_second_case_budget(self):
        first, second = candidate("quote-one", 1), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        self.write_approved(item=[first, second], total=2.5, cases=cases)
        self.assertEqual(pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener({"success": True, "actual_cost": 2}))["outcome"], "budget_violation")
        opener = FakeOpener({"success": True, "actual_cost": 1})
        with self.assertRaisesRegex(pilot.PilotError, "prior plan case"):
            pilot.run(self.args(case="case-two", idempotency_key="batch-two"), opener)
        self.assertEqual(opener.calls, [])

    def test_invalid_or_over_case_receipt_stops_following_post(self):
        first, second = candidate("quote-one", 1), candidate("quote-two", 1)
        cases = [
            {"case_id": "case-one", "alias": first["alias"], "arguments": first["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
            {"case_id": "case-two", "alias": second["alias"], "arguments": second["call_parameters"], "expected_cost": 1, "approval_id": "approval-1"},
        ]
        for payload, outcome in (({"success": True, "actual_cost": 2}, "budget_violation"), ({"success": True}, "receipt_missing"), ({"success": True, "actual_cost": float("nan")}, "budget_violation")):
            with self.subTest(payload=payload):
                self.ledger.unlink(missing_ok=True)
                self.write_approved(item=[first, second], total=100, cases=cases)
                self.assertEqual(pilot.run(self.args(case="case-one", idempotency_key="batch-one"), FakeOpener(payload))["outcome"], outcome)
                opener = FakeOpener({"success": True, "actual_cost": 1})
                with self.assertRaisesRegex(pilot.PilotError, "prior plan case"):
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

    def test_cross_plan_uses_a_plan_scoped_ledger(self):
        self.assertEqual(pilot.run(self.args(), FakeOpener({"success": True, "actual_cost": 1}))["outcome"], "success")
        first_ledger = self.ledger
        self.write_approved(item=candidate(arguments={"symbol": "MSFT"}))
        opener = FakeOpener({"success": True, "actual_cost": 1})
        self.assertEqual(pilot.run(self.args(), opener)["outcome"], "success")
        self.assertNotEqual(first_ledger, self.ledger)
        self.assertEqual(len(opener.calls), 1)

    def test_corrupt_ledger_fails_closed(self):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text('{"record_type":"planned"', encoding="utf-8")
        opener = FakeOpener({"success": True})
        with self.assertRaisesRegex(pilot.PilotError, "truncated"):
            pilot.run(self.args(), opener)
        self.assertEqual(opener.calls, [])

    def test_unknown_ledger_state_fails_closed(self):
        bad = {"record_type": "mystery", "case_id": "old", "alias": "old", "tool_id": "old", "arguments_hash": "old", "manifest_hash": "old", "plan_hash": "old", "approval_id": "old", "idempotency_key": "old", "expected_credits": 1}
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
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
        second = threading.Thread(target=invoke, args=("pilot-1",))
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

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.response_contract import ResponseContractError, normalize_json_response, normalize_response, validate_response
from qveris_benchmark.run_backend import RunService

USAGE = {"receipt_id": "usage-1", "measurement_version": "v1", "cache_status": "miss", "request_id": "request-1", "issuer": "qveris_gateway", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}


def success(suite, data):
    return {"schema_version": "get-response/v1", "status": "success", "resolved_request": {"suite": suite, "accepted_variant_id": "variant-1"}, "data": data, "as_of": "2026-09-04T10:00:00Z", "source": "official", "clarification": None, "terminal_reason": None, "meta": {"usage": dict(USAGE)}}


def v2_success(suite, data, *, status="success", as_of="2026-09-04T10:00:00Z", as_of_status="known", missing_fields=()):
    return {"schema_version": "get-response/v2", "status": status, "resolved_request": {"suite": suite, "accepted_variant_id": "variant-1"}, "data": data, "as_of": as_of, "as_of_status": as_of_status, "source": "official", "clarification": None, "terminal_reason": None, "coverage": {"complete": status == "success", "missing_fields": list(missing_fields)}, "meta": {"usage": dict(USAGE)}}


class ResponseContractTests(unittest.TestCase):
    def test_financial_multiple_direct_fields_and_nil_are_scoreable(self):
        response = success("financial_statements", {"kind": "financial_statement", "facts": {"is_002": {"assertion_id": "is-002", "field": "consolidated_income_statement.营业收入", "value": "416161000000", "period": "FY2025", "currency": "USD", "unit": "currency", "nil": False}, "is_003": {"assertion_id": "is-003", "field": "research_and_development", "value": None, "period": "FY2025", "currency": "USD", "unit": "currency", "nil": True}}})
        self.assertIsNone(normalize_response(response, suite="financial_statements")["data"]["facts"]["is_003"]["value"])

    def test_historical_bars_are_period_keyed(self):
        response = success("historical_price", {"kind": "historical_price", "accepted_variant_id": "variant-1", "instrument": {"symbol": "AAPL", "market": "US"}, "interval": "daily", "adjustment": "as_reported", "bars": {"d20250102": {"period_key": "d20250102", "fields": {"close": {"value": "243.85", "unit": "USD_per_share", "nil": False}}}}})
        validate_response(response, suite="historical_price")
        response["data"]["bars"]["d20250102"]["period_key"] = "d20250103"
        with self.assertRaises(ResponseContractError): validate_response(response, suite="historical_price")
        response["data"]["bars"]["d20250102"]["period_key"] = "d20250102"; response["data"]["accepted_variant_id"] = "variant-2"
        with self.assertRaises(ResponseContractError): validate_response(response, suite="historical_price")

    def test_realtime_timestamp_and_non_numeric_are_rejected(self):
        response = success("realtime_quote", {"kind": "realtime_quote", "quote": {"instrument": {"symbol": "00700.HK", "market": "HK"}, "fields": {"last_price": {"value": "523.5", "unit": "HKD_per_share", "as_of": "2026-09-04T10:00:00Z", "nil": False}}}})
        validate_response(response, suite="realtime_quote")
        response["data"]["quote"]["fields"]["last_price"]["value"] = "NaN"
        with self.assertRaises(ResponseContractError): validate_response(response, suite="realtime_quote")

    def test_missing_raw_and_usage_errors_are_rejected(self):
        response = success("financial_statements", {"kind": "financial_statement", "facts": {"is_002": {"assertion_id": "is-002", "field": "revenue", "value": "1", "period": "FY2025", "currency": "USD", "unit": "currency", "nil": False}}, "provider_payload": {"raw": "forbidden"}})
        with self.assertRaises(ResponseContractError): validate_response(response, suite="financial_statements")
        response["data"].pop("provider_payload"); response["meta"]["usage"]["total_tokens"] = 4
        with self.assertRaises(ResponseContractError): validate_response(response, suite="financial_statements")

    def test_state_duplicate_key_and_official_shape(self):
        state = {"schema_version": "get-response/v1", "status": "needs_clarification", "data": None, "clarification": "Which market?", "terminal_reason": None, "meta": {"usage": dict(USAGE)}}
        validate_response(state)
        with self.assertRaises(ResponseContractError): normalize_json_response('{"schema_version":"get-response/v1","schema_version":"get-response/v1","status":"success"}', diagnostic=True)
        response = success("financial_statements", {"kind": "financial_statement", "facts": {"is_002": {"assertion_id": "is-002", "field": "revenue", "value": "1", "period": "FY2025", "currency": "USD", "unit": "currency", "nil": False}}})
        normalized = normalize_response(response, suite="financial_statements")
        self.assertEqual(normalized["resolved_request"]["accepted_variant_id"], "variant-1")
        self.assertEqual(RunService._project_response(normalized)[0]["status"], "success")

    def test_v2_runner_projection_accepts_strict_forms_and_rejects_old_shape(self):
        facts = success("financial_statements", {"kind": "financial_statement", "facts": {"is_002": {"assertion_id": "is-002", "field": "revenue", "value": "1", "period": "FY2025", "currency": "USD", "unit": "currency", "nil": False}}})
        bars = success("historical_price", {"kind": "historical_price", "accepted_variant_id": "variant-1", "instrument": {"symbol": "AAPL", "market": "US"}, "interval": "daily", "adjustment": "as_reported", "bars": {"d20250102": {"period_key": "d20250102", "fields": {"close": {"value": "1", "unit": "USD_per_share", "nil": False}}}}})
        quote = success("realtime_quote", {"kind": "realtime_quote", "quote": {"instrument": {"symbol": "AAPL", "market": "US"}, "fields": {"last_price": {"value": "1", "unit": "USD_per_share", "as_of": "2026-09-04T10:00:00Z", "nil": False}}}})
        state = {"schema_version": "get-response/v1", "status": "no_data", "data": None, "clarification": None, "terminal_reason": "not_reported", "meta": {"usage": dict(USAGE)}}
        for response in (facts, bars, quote, state):
            self.assertNotEqual(RunService._project_response(response, strict_response_contract=True)[0]["status"], "invalid_public_response")
        self.assertEqual(RunService._project_response({"schema_version": "get-response/v1", "status": "success", "data": {}}, strict_response_contract=True)[0]["status"], "invalid_public_response")
        self.assertEqual(RunService._project_response(facts, strict_response_contract=True, suite="historical_price")[0]["status"], "invalid_public_response")

    def test_v2_batch_quote_keeps_per_symbol_provider_times(self):
        response = v2_success("realtime_quote", {"kind": "batch_realtime_quote", "quotes": {
            "AAPL": {"instrument": {"symbol": "AAPL", "market": "US"}, "fields": {"last_price": {"value": "243.85", "unit": "USD_per_share", "as_of": "2026-09-04T10:00:00Z", "nil": False}}},
            "MSFT": {"instrument": {"symbol": "MSFT", "market": "US"}, "fields": {"last_price": {"value": "501.0", "unit": "USD_per_share", "as_of": "2026-09-04T10:01:00Z", "nil": False}}},
        }}, as_of=None, as_of_status="mixed")
        normalized = normalize_response(response, suite="realtime_quote")
        self.assertIsNone(normalized["as_of"])
        response["data"]["quotes"]["MSFT"]["fields"]["last_price"]["as_of"] = None
        with self.assertRaises(ResponseContractError): validate_response(response, suite="realtime_quote")

    def test_v2_historical_events_and_calendar_are_not_price_bars(self):
        event = v2_success("historical_price", {"kind": "historical_event", "instrument": {"symbol": "AAPL", "market": "US"}, "event_type": "corporate_actions", "events": {"d20250115": {"period_key": "d20250115", "description": "Provider corporate-action plan", "fields": {"dividend": {"value": "0.25", "unit": "USD_per_share", "nil": False}}}}})
        self.assertEqual(normalize_response(event, suite="historical_price")["data"]["kind"], "historical_event")
        self.assertEqual(normalize_response(event, suite="historical_price")["data"]["events"]["d20250115"]["description"], "Provider corporate-action plan")
        calendar = v2_success("historical_price", {"kind": "market_calendar", "venue": "US", "dates": ["2025-01-01", "2025-01-02"], "range": {"start_date": "2025-01-01", "end_date": "2025-01-02"}, "time_basis": "coverage_range"}, status="partial", as_of=None, as_of_status="unavailable", missing_fields=("as_of",))
        self.assertEqual(normalize_response(calendar, suite="historical_price")["data"]["kind"], "market_calendar")
        calendar["data"]["dates"].append("2025-01-02")
        with self.assertRaises(ResponseContractError): validate_response(calendar, suite="historical_price")

    def test_v2_intraday_bars_preserve_two_provider_timestamps_on_one_day(self):
        response = v2_success("historical_price", {"kind": "historical_price", "accepted_variant_id": "variant-1", "instrument": {"symbol": "AAPL", "market": "US"}, "interval": "5min", "adjustment": "as_reported", "time_basis": "provider_timestamp", "timezone": "America/New_York", "bars": {
            "2025-01-02T09:30:00": {"period": {"timestamp": "2025-01-02T09:30:00", "interval": "5min"}, "fields": {"close": {"value": "243.85", "unit": "USD_per_share", "nil": False}}},
            "2025-01-02T09:35:00": {"period": {"timestamp": "2025-01-02T09:35:00", "interval": "5min"}, "fields": {"close": {"value": "244.00", "unit": "USD_per_share", "nil": False}}},
        }})
        normalized = normalize_response(response, suite="historical_price")
        self.assertEqual(len(normalized["data"]["bars"]), 2)
        response["data"]["bars"]["2025-01-02T09:35:00"]["period"]["interval"] = "15min"
        with self.assertRaises(ResponseContractError): validate_response(response, suite="historical_price")

    def test_v2_enforces_partial_coverage_and_rejects_v1_data_shape(self):
        response = v2_success("historical_price", {"kind": "market_calendar", "venue": "US", "dates": ["2025-01-01"], "range": {"start_date": "2025-01-01", "end_date": "2025-01-01"}, "time_basis": "coverage_range"}, status="partial", as_of=None, as_of_status="unavailable", missing_fields=())
        with self.assertRaises(ResponseContractError): validate_response(response, suite="historical_price")
        old_data = v2_success("realtime_quote", {"kind": "realtime_quote", "quote": {"instrument": {"symbol": "AAPL", "market": "US"}, "fields": {"last_price": {"value": "1", "unit": "USD_per_share", "as_of": "2026-09-04T10:00:00Z", "nil": False}}}})
        with self.assertRaises(ResponseContractError): validate_response(old_data, suite="realtime_quote")

    def test_v2_financial_is_query_keyed_not_oracle_keyed(self):
        response = v2_success("financial_statements", {"kind": "financial_statement", "instrument": {"symbol": "AAPL", "market": "US"}, "statement_type": "income_statement", "presentation": "standardized", "facts": {"revenue": {"value": "416161000000", "period": "FY2025", "currency": "USD", "unit": "unknown", "nil": False}}})
        self.assertNotIn("assertion_id", normalize_response(response, suite="financial_statements")["data"]["facts"]["revenue"])

    def test_v2_market_status_accepts_provider_text_without_numeric_coercion(self):
        response = v2_success("realtime_quote", {"kind": "market_status", "instrument": {"symbol": "00700.HK", "market": "HKEX"}, "status": "HALTED"})
        self.assertEqual(normalize_response(response, suite="realtime_quote")["data"]["status"], "HALTED")


if __name__ == "__main__": unittest.main()

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.domain_routes_historical import SUPPORTED_KEYS, project, resolve
from qveris_benchmark.public_get import PublicGetAdapter
from qveris_benchmark.qveris_tool_gateway import QVerisToolGateway


class _Response:
    status = 200

    def __init__(self, body): self.body = body
    def read(self, _size): return self.body
    def __enter__(self): return self
    def __exit__(self, *_args): return False


class _Opener:
    def __init__(self, body): self.body, self.calls = body, []
    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        return _Response(self.body)


_IDENTITY = {"agent_variant_id": "test-agent", "agent_version": "v1", "get_variant_id": "public-get", "get_version": "v1", "model_identifier": "test-model", "model_version": "v1", "model_config_digest": "a" * 64}


def request(venue, operation, code, *, adjustment="unadjusted", interval="daily"):
    return {"kind": "historical", "security": {"asset_class": "equity", "venue": venue, "symbol": code}, "operation": operation, "adjustment": adjustment if operation in {"daily_bars", "intraday_bars"} else "not_applicable", "start_date": "2026-08-01", "end_date": "2026-08-31", "interval": interval}


class HistoricalDomainRouteTests(unittest.TestCase):
    def test_us_tiingo_is_one_fixed_range_route_and_crops_to_request(self):
        plan = resolve(request("US", "daily_bars", "AAPL"))
        self.assertEqual(plan.tool_id, "tiingo.daily.ticker.prices.list.v1")
        self.assertEqual(plan.params, {"ticker": "AAPL", "startDate": "2026-08-01", "endDate": "2026-08-31"})
        raw = {"result": {"data": [{"date": "2026-07-31T00:00:00.000Z", "open": 1, "high": 3, "low": 1, "close": 2, "volume": 4}, {"date": "2026-08-03T00:00:00.000Z", "open": 2, "high": 4, "low": 2, "close": 3, "volume": 5}]}}
        result = project(plan, raw)
        self.assertEqual(list(result.data["bars"]), ["d20260803"])
        self.assertEqual(result.as_of, "2026-08-03")

    def test_adjusted_tiingo_requires_adjusted_fields(self):
        plan = resolve(request("US", "daily_bars", "AAPL", adjustment="adjusted"))
        raw = {"result": {"data": [{"date": "2026-08-03", "open": 1, "high": 3, "low": 1, "close": 2, "volume": 4, "adjOpen": 2, "adjHigh": 6, "adjLow": 2, "adjClose": 4, "adjVolume": 8}]}}
        self.assertEqual(project(plan, raw).data["bars"]["d20260803"]["fields"]["close"]["value"], "4")
        del raw["result"]["data"][0]["adjClose"]
        with self.assertRaises(ValueError): project(plan, raw)

    def test_cn_daily_identity_and_hk_calendar_range_are_strict(self):
        plan = resolve(request("SSE", "daily_bars", "600519"))
        raw = [[{"thscode": "600519.SH", "time": "2026-08-03", "open": 1, "high": 3, "low": 1, "close": 2, "volume": 4}]]
        self.assertEqual(project(plan, raw).data["bars"]["d20260803"]["fields"]["close"]["value"], "2")
        raw[0][0]["thscode"] = "000001.SZ"
        with self.assertRaises(ValueError): project(plan, raw)
        calendar = resolve(request("HKEX", "trading_calendar", "00700"))
        result = project(calendar, {"time": ["2026-08-03", "2026-09-01"], "metadata": {"marketcode": "212200", "date_type": "0", "has_results": True}})
        self.assertEqual(result.data["dates"], ["2026-08-03"])
        self.assertEqual((result.status, result.as_of, result.schema_version, result.as_of_status), ("success", None, "get-response/v2", "unavailable"))

    def test_cn_daily_reaches_public_success_through_real_gateway_unwrap(self):
        raw = [[{"thscode": "300033.SZ", "time": "2026-08-03", "open": 1, "high": 3, "low": 1, "close": 2, "volume": 4, "amount": 5}]]
        opener = _Opener(json.dumps({"success": True, "result": {"data": raw}, "execution_id": "private", "actual_credits": 1}).encode())
        gateway = QVerisToolGateway(api_key="test-key", opener=opener)
        semantic = {"schema_version": "public-get.semantic/v1", "request": {"kind": "historical", "security": {"asset_class": "equity", "venue": "SZSE", "local_code": "300033"}, "operation": "daily_bars", "adjustment": "unadjusted", "start_date": "2026-08-03", "end_date": "2026-08-03"}}
        result = PublicGetAdapter(lambda *_args, **_kwargs: semantic, gateway, **_IDENTITY).run("daily", request_id="request-1", idempotency_key="key-1")
        self.assertEqual((result.public_response["status"], result.public_response["data"]["kind"], result.execution_evidence.tool_executions, len(opener.calls)), ("success", "historical_price", 1, 1))

    def test_hk_daily_requires_observed_identity_and_complete_pagination_count(self):
        plan = resolve(request("HKEX", "daily_bars", "00700"))
        self.assertEqual(plan.params["stockObject"], ["00700.HK"])
        raw = {"success": True, "_qveris_pagination": {"returned_count": 1, "total_count": 1}, "data": {"data": {"rows": [{"secucode": "00700", "secuabbr": "Tencent", "tradingday": "2026-08-03", "open": "1", "high": "3", "low": "1", "close": "2", "volume": "4", "amount": "5", "currency": "港元"}]}}}
        self.assertEqual(project(plan, raw).data["bars"]["d20260803"]["fields"]["close"]["value"], "2")
        raw["_qveris_pagination"]["total_count"] = 2
        with self.assertRaises(ValueError): project(plan, raw)

    def test_weekly_aggregation_is_deterministic_and_partial_for_one_day(self):
        plan = resolve(request("US", "daily_bars", "AAPL", interval="weekly"))
        self.assertEqual(plan.accepted_variant_id, "historical-weekly-bars-unadjusted-v1")
        raw = {"result": {"data": [{"date": "2026-08-03", "open": 2, "high": 4, "low": 1, "close": 3, "volume": 5}, {"date": "2026-08-04", "open": 3, "high": 5, "low": 2, "close": 4, "volume": 6}]}}
        result = project(plan, raw)
        self.assertEqual(result.data["bars"]["w20260803_20260809"]["fields"]["volume"]["value"], "11")
        self.assertEqual(result.status, "success")
        self.assertEqual(project(plan, {"result": {"data": raw["result"]["data"][:1]}}).status, "partial")

    def test_known_gap_or_rejected_cells_do_not_resolve_and_map_is_exact(self):
        self.assertIsNone(resolve(request("HKEX", "daily_bars", "00700", adjustment="adjusted")))
        self.assertIsNone(resolve(request("SZSE", "corporate_actions", "000001")))
        self.assertEqual(len(SUPPORTED_KEYS), 29)

    def test_global_fmp_eod_is_identity_strict_and_explicit_about_unknown_basis(self):
        plan = resolve(request("JP", "daily_bars", "7203"))
        self.assertEqual(plan.params, {"symbol": "7203.T", "from": "2026-08-01", "to": "2026-08-31"})
        raw = [{"symbol": "7203.T", "date": "2026-08-03", "open": "1", "high": "3", "low": "1", "close": "2", "volume": "4", "change": "1", "changePercent": "2", "vwap": "2"}]
        result = project(plan, raw)
        self.assertEqual((result.status, result.missing_fields, result.data["adjustment"]), ("partial", ("adjustment_basis",), "provider_basis_unknown"))
        raw[0]["symbol"] = "6758.T"
        with self.assertRaises(ValueError): project(plan, raw)

    def test_intraday_keeps_two_same_day_provider_timestamps_in_v2(self):
        plan = resolve(request("US", "intraday_bars", "AAPL", interval="5min"))
        raw = {"Meta Data": {"2. Symbol": "AAPL", "3. Last Refreshed": "2026-08-03 10:05:00", "4. Interval": "5min", "6. Time Zone": "US/Eastern"}, "Time Series (5min)": {"2026-08-03 10:00:00": {"1. open": "1", "2. high": "3", "3. low": "1", "4. close": "2", "5. volume": "4"}, "2026-08-03 10:05:00": {"1. open": "2", "2. high": "4", "3. low": "2", "4. close": "3", "5. volume": "5"}}}
        result = project(plan, raw)
        self.assertEqual((result.schema_version, result.data["timezone"], len(result.data["bars"])), ("get-response/v2", "US/Eastern", 2))
        self.assertIn("2026-08-03T10:05:00", result.data["bars"])

    def test_hk_corporate_action_exposes_only_dated_missing_amount_event(self):
        plan = resolve(request("HKEX", "corporate_actions", "00700"))
        self.assertEqual(plan.params["type"], "CD")
        row = {"symbol": "00700.HK", "type": "CD", "eventProgress": "done", "reportDate": "2026-08-01", "recordDate": "2026-08-02", "exDate": "2026-08-03", "paymentDate": "2026-08-04", "bookClosePeriodStart": "2026-08-05", "bookClosePeriodEnd": "2026-08-06", "plan": "provider plan"}
        result = project(plan, {"action": "ok", "code": "0", "data": [row], "msg": "ok"})
        event = result.data["events"]["d20260803"]
        self.assertEqual((result.status, result.missing_fields, event["fields"]["amount"]["nil"], event["description"]), ("partial", ("amount", "currency"), True, "provider plan"))


if __name__ == "__main__":
    unittest.main()

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.domain_routes_realtime import SUPPORTED_KEYS, project, resolve


def semantic(venue, operation, code):
    return {"kind": "market_quote", "security": {"asset_class": "equity", "venue": venue, "symbol": code}, "operation": operation}


class RealtimeDomainRoutesTests(unittest.TestCase):
    def test_every_declared_supported_key_resolves_to_its_exact_tool(self):
        codes = {"US": "AAPL", "SSE": "600519", "SZSE": "300750", "HKEX": "00700"}
        for (market, scenario), tool_id in SUPPORTED_KEYS.items():
            operation = scenario.removeprefix("realtime.equity.").removesuffix(".v1")
            if operation == "batch_quote_snapshot":
                request = {"kind": "market_quote", "operation": operation, "securities": [{"asset_class": "equity", "venue": market, "symbol": codes[market]}]}
            else:
                request = semantic(market, operation, codes[market])
            self.assertEqual(resolve(request).tool_id, tool_id, scenario)

    def test_us_l1_and_extended_routes_keep_provider_timestamps(self):
        l1 = resolve(semantic("US", "bid_ask_l1", "AAPL"))
        self.assertEqual(l1.params["function"], "REALTIME_BULK_BID_ASK_PRICES")
        projected = project(l1, {"data": [{"symbol": "AAPL", "timestamp": "2026-09-05T10:00:00Z", "bid_price": "200", "ask_price": "201", "bid_size": "1", "ask_size": "2"}]})
        self.assertEqual(projected.as_of, "2026-09-05T10:00:00Z")
        after = resolve(semantic("US", "extended_hours_price", "AAPL"))
        self.assertEqual(project(after, {"symbol": "AAPL", "timestamp": "2026-09-05T20:00:00Z", "price": "202"}).data["quote"]["fields"]["extended_hours_price"]["value"], "202")

    def test_cnfp_routes_use_requested_primary_and_do_not_require_duplicate_dates(self):
        plan = resolve(semantic("SSE", "quote_snapshot", "600519"))
        self.assertEqual(plan.tool_id, "cn_financial_pro.real_time_quotation.v1")
        self.assertEqual(plan.params, {"codes": "600519.SH", "indicators": "common"})
        raw = [{"thscode": "600519.SH", "time": "2026-09-05 10:00:00", "preClose": "1", "open": "1", "high": "3", "low": "1", "latest": "2", "volume": "10", "amount": "20"}]
        result = project(plan, raw)
        self.assertEqual(result.as_of, "2026-09-05 10:00:00")
        self.assertEqual(result.data["quote"]["fields"]["last_price"]["value"], "2")
        requested = semantic("SSE", "quote_snapshot", "600519"); requested["requested_fields"] = ["last_price"]
        self.assertEqual(set(project(resolve(requested), raw).data["quote"]["fields"]), {"last_price"})

    def test_cn_batch_is_explicitly_v2_and_preserves_each_timestamp(self):
        request = {"kind": "market_quote", "operation": "batch_quote_snapshot", "securities": [{"asset_class": "equity", "venue": "SZSE", "symbol": "300750"}, {"asset_class": "equity", "venue": "SZSE", "symbol": "000001"}]}
        plan = resolve(request)
        self.assertEqual(plan.context["response_version"], "v2")
        raw = [[{"thscode": "300750.SZ", "time": "2026-09-05T10:00:00Z", "preClose": 1, "open": 1, "high": 3, "low": 1, "latest": 2, "volume": 10, "amount": 20}, {"thscode": "000001.SZ", "time": "2026-09-05T10:01:00Z", "preClose": 1, "open": 1, "high": 3, "low": 1, "latest": 2, "volume": 10, "amount": 20}]]
        result = project(plan, raw)
        self.assertEqual(result.data["kind"], "batch_realtime_quote")
        self.assertIsNone(result.as_of)
        self.assertEqual(result.as_of_status, "mixed")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.missing_fields, ())
        self.assertEqual(result.data["quotes"]["000001.SZ"]["fields"]["last_price"]["as_of"], "2026-09-05T10:01:00Z")

    def test_hk_code_validation_and_optional_turnover_fields(self):
        plan = resolve(semantic("HKEX", "volume_turnover_snapshot", "00700"))
        raw = {"data": {"data": {"rows": [{"stockCode": "00700", "tradingTimestamp": "2026-09-05T10:00:00Z", "currency": "HKD", "latestPrice": "10", "turnoverVolumeLot": "11"}]}}}
        result = project(plan, raw)
        self.assertEqual(result.data["quote"]["fields"], {"volume": {"value": "11", "unit": "unknown", "as_of": "2026-09-05T10:00:00Z", "nil": False}})
        raw["data"]["data"]["rows"][0]["stockCode"] = "00005"
        with self.assertRaises(ValueError): project(plan, raw)

    def test_hk_status_preserves_provider_text_in_v2(self):
        plan = resolve(semantic("HKEX", "trading_status", "00700"))
        raw = {"data": {"data": {"rows": [{"stockCode": "00700", "tradingTimestamp": "2026-09-05T10:00:00Z", "latestPrice": "10", "tradeStatus": "交易中"}]}}}
        result = project(plan, raw)
        self.assertEqual(result.schema_version, "get-response/v2")
        self.assertEqual(result.data["status"], "交易中")

    def test_us_volume_is_partial_only_when_amount_is_required(self):
        raw = {"Global Quote": {"01. symbol": "AAPL", "02. open": "1", "03. high": "3", "04. low": "1", "05. price": "2", "06. volume": "10", "07. latest trading day": "2026-09-05", "08. previous close": "1", "09. change": "1", "10. change percent": "100%"}}
        default = project(resolve(semantic("US", "volume_turnover_snapshot", "AAPL")), raw)
        self.assertEqual((default.status, default.missing_fields), ("partial", ("amount",)))
        request = semantic("US", "volume_turnover_snapshot", "AAPL"); request["requested_fields"] = ["volume"]
        only_volume = project(resolve(request), raw)
        self.assertEqual((only_volume.status, only_volume.missing_fields), ("success", ()))

    def test_known_rejected_or_unverified_routes_are_not_resolved(self):
        self.assertIsNone(resolve(semantic("US", "latest_trade", "AAPL")))
        self.assertIsNone(resolve(semantic("SSE", "trading_status", "600519")))
        self.assertIsNone(resolve(semantic("HKEX", "extended_hours_price", "00700")))


if __name__ == "__main__":
    unittest.main()

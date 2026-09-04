import json
import pathlib
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from qveris_benchmark.provider_payload import (
    ALPHAVANTAGE_GLOBAL_QUOTE_V1,
    ALPHAVANTAGE_TIME_SERIES_DAILY_V1,
    CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1,
    EODHD_QUOTE_CSV_V1,
    FIU_US_QUOTE_SNAPSHOT_V1,
    FMP_EOD_V1,
    HANGSENG_A_SHARE_QUOTE_V1,
    ProviderPayloadParseError,
    parse_provider_payload,
)


EODHD_CSV = "\n".join(
    (
        "code,timestamp,gmtoffset,open,high,low,close,volume,previousClose,change,change_p",
        "AAPL.US,1725264000,-14400,220.00,225.00,219.00,224.00,12345,221.00,3.00,1.36",
    )
)

CAIDAZI_HEADERS = (
    "股票代码", "股票名称", "交易时间", "最新价", "涨跌额", "涨跌幅", "成交量", "成交额",
    "开盘以来成交笔数", "委托卖盘价", "委托卖盘量", "委托买盘价", "委托买盘量", "开盘价",
    "最高价", "最低价", "总市值", "流通市值", "换手率", "量比", "总股本", "市盈率(TTM)",
    "市净率", "涨停价", "跌停价", "是否停牌（0：否，1:是）",
)
CAIDAZI_VALUES = (
    "600519.SH", "合成公司", "2026-09-03 10:00:00", "1500.00", "10.00", "0.67%", "100万股", "1亿元",
    "-", "1501.00", "20股", "1499.00", "20股", "1490.00", "1510.00", "1480.00",
    "2亿元", "1亿元", "1.00%", "1.20", "100亿股", "20", "3", "1600.00", "1400.00", "0",
)
CAIDAZI_MARKDOWN = "\n".join(
    (
        "## 600519.SH - 实时行情数据",
        "",
        "|" + "|".join(CAIDAZI_HEADERS) + "|",
        "|" + "|".join("---" for _ in CAIDAZI_HEADERS) + "|",
        "|" + "|".join(CAIDAZI_VALUES) + "|",
    )
)

FIU_US_SNAPSHOT = {"body": [{"snapshot": {"symbol": "AAPL.US", "time": "2026-09-03T14:30:00Z", "open": 220.0, "high": 225.0, "low": 219.0, "last": 224.0}}]}
ALPHA_GLOBAL_QUOTE = {"Global Quote": {"01. symbol": "AAPL", "02. open": "220.00", "03. high": "225.00", "04. low": "219.00", "05. price": "224.00", "06. volume": "12345", "07. latest trading day": "2026-09-03", "08. previous close": "221.00", "09. change": "3.00", "10. change percent": "1.36%"}}
ALPHA_DAILY = {"Meta Data": {"2. Symbol": "AAPL", "3. Last Refreshed": "2026-09-03", "5. Time Zone": "US/Eastern"}, "Time Series (Daily)": {"2026-09-03": {"1. open": "220.00", "2. high": "225.00", "3. low": "219.00", "4. close": "224.00", "5. volume": "12345"}}}
FMP_EOD = [{"symbol": "AAPL", "date": "2024-01-03", "open": 180.0, "high": 185.0, "low": 179.0, "close": 184.0, "volume": 12345}]
HANGSENG_A_SHARE = {"data": {"data": {"rows": [{"stockCode": "600000", "stockName": "合成银行", "currency": "CNY", "tradingTimestamp": "2026-09-03 15:00:00", "tradeStatus": "交易中", "latestPrice": 10.4, "openPrice": 10.0, "highPrice": 10.5, "lowPrice": 9.9, "prevClosePrice": 10.1, "changeOfPrice": 0.3, "changePCT": 2.97, "turnoverVolumeLot": 100.0, "realTimeVolumeLot": 20.0, "selloutVolumeLot": 10.0, "buyinVolumeLot": 11.0, "sharesPerHand": "100", "turnoverValue": "1000000", "bidGrp": "10.3/100", "offerGrp": "10.4/100"}]}}}


class ProviderPayloadTests(unittest.TestCase):
    def assert_code(self, code, parser_id, raw, expected_symbol):
        with self.assertRaises(ProviderPayloadParseError) as context:
            parse_provider_payload(parser_id, raw, expected_symbol=expected_symbol)
        self.assertEqual(context.exception.code, code)

    def test_eodhd_csv_projects_only_the_approved_contract(self):
        result = parse_provider_payload(EODHD_QUOTE_CSV_V1, EODHD_CSV, expected_symbol="AAPL.US")
        self.assertEqual(result["symbol"], "AAPL.US")
        self.assertEqual(result["timestamp"], 1725264000)
        self.assertEqual(result["close"], Decimal("224.00"))

    def test_eodhd_rejects_schema_symbol_finite_volume_and_ohlc_drift(self):
        self.assert_code("EODHD_CSV_HEADER_INVALID", EODHD_QUOTE_CSV_V1, EODHD_CSV.replace("change_p", "changePercent"), "AAPL.US")
        self.assert_code("EODHD_CSV_ROW_INVALID", EODHD_QUOTE_CSV_V1, EODHD_CSV.rsplit(",", 1)[0], "AAPL.US")
        self.assert_code("EODHD_SYMBOL_MISMATCH", EODHD_QUOTE_CSV_V1, EODHD_CSV, "MSFT.US")
        self.assert_code("EODHD_DECIMAL_INVALID", EODHD_QUOTE_CSV_V1, EODHD_CSV.replace(",3.00,1.36", ",NaN,1.36"), "AAPL.US")
        self.assert_code("EODHD_VOLUME_NEGATIVE", EODHD_QUOTE_CSV_V1, EODHD_CSV.replace(",12345,221.00", ",-1,221.00"), "AAPL.US")
        self.assert_code("EODHD_OHLC_INVALID", EODHD_QUOTE_CSV_V1, EODHD_CSV.replace(",225.00,219.00,224.00", ",223.00,219.00,224.00"), "AAPL.US")
        self.assert_code("EODHD_SYMBOL_FORMAT_INVALID", EODHD_QUOTE_CSV_V1, EODHD_CSV.replace("AAPL.US", "=AAPL.US"), "AAPL.US")
        self.assert_code("EODHD_SYMBOL_FORMAT_INVALID", EODHD_QUOTE_CSV_V1, EODHD_CSV, "=AAPL.US")
        self.assert_code("EODHD_CONTROL_CHARACTER", EODHD_QUOTE_CSV_V1, EODHD_CSV + "\u202e", "AAPL.US")
        self.assert_code("EODHD_PAYLOAD_TOO_LARGE", EODHD_QUOTE_CSV_V1, EODHD_CSV + " " * (16 * 1024), "AAPL.US")

    def test_caidazi_markdown_keeps_decimal_lexical_and_units_without_conversion(self):
        result = parse_provider_payload(CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN, expected_symbol="600519.SH")
        self.assertEqual(result["symbol"], "600519.SH")
        self.assertEqual(result["fields"]["成交额"], {"decimal": "1", "unit": "亿元"})
        self.assertIsNone(result["fields"]["开盘以来成交笔数"])
        self.assertFalse(result["halted"])
        self.assertEqual(
            parse_provider_payload(CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN + "\n", expected_symbol="600519.SH"),
            result,
        )

    def test_caidazi_allows_only_the_observed_non_core_missing_sentinels(self):
        raw = CAIDAZI_MARKDOWN.replace(
            "|2亿元|1亿元|1.00%|1.20|100亿股|20|3|",
            "|-|-|-|-|-|-|-|",
        )
        result = parse_provider_payload(CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, raw, expected_symbol="600519.SH")
        for field in ("总市值", "流通市值", "换手率", "量比", "总股本", "市盈率(TTM)", "市净率"):
            self.assertIsNone(result["fields"][field])

    def test_caidazi_rejects_untrusted_shape_symbols_units_and_market_invariants(self):
        self.assert_code("CAIDAZI_TITLE_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("## 600519.SH", "## 000001.SZ"), "600519.SH")
        sz_markdown = CAIDAZI_MARKDOWN.replace("600519.SH", "300750.SZ")
        self.assertEqual(parse_provider_payload(CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, sz_markdown, expected_symbol="300750.SZ")["symbol"], "300750.SZ")
        self.assert_code("CAIDAZI_SYMBOL_FORMAT_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN, None)
        self.assert_code("CAIDAZI_UNIT_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("100万股", "100箱", 1), "600519.SH")
        self.assert_code("CAIDAZI_BID_ASK_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("1499.00", "1502.00"), "600519.SH")
        self.assert_code("CAIDAZI_OHLC_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("1510.00", "1499.00"), "600519.SH")
        self.assert_code("CAIDAZI_HALT_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN[:-2] + "2|", "600519.SH")
        self.assert_code("CAIDAZI_DECIMAL_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("|1500.00|", "|-|", 1), "600519.SH")
        self.assert_code("CAIDAZI_ROW_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("|0|", "|"), "600519.SH")
        self.assert_code("CAIDAZI_LINE_COUNT_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN + "\n\n", "600519.SH")
        self.assert_code("CAIDAZI_LINE_COUNT_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, "\n" + CAIDAZI_MARKDOWN, "600519.SH")
        self.assert_code("CAIDAZI_NAME_INVALID", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN.replace("合成公司", "<b>公司</b>"), "600519.SH")
        self.assert_code("CAIDAZI_CONTROL_CHARACTER", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN + "\u202e", "600519.SH")
        self.assert_code("CAIDAZI_PAYLOAD_TOO_LARGE", CAIDAZI_A_SHARE_QUOTE_MARKDOWN_V1, CAIDAZI_MARKDOWN + " " * (16 * 1024), "600519.SH")

    def test_doc6_structured_parsers_project_synthetic_canonical_data(self):
        fiu = parse_provider_payload(FIU_US_QUOTE_SNAPSHOT_V1, FIU_US_SNAPSHOT, expected_symbol="AAPL.US")
        alpha_quote = parse_provider_payload(ALPHAVANTAGE_GLOBAL_QUOTE_V1, ALPHA_GLOBAL_QUOTE, expected_symbol="AAPL")
        alpha_daily = parse_provider_payload(ALPHAVANTAGE_TIME_SERIES_DAILY_V1, ALPHA_DAILY, expected_symbol="AAPL")
        fmp = parse_provider_payload(FMP_EOD_V1, FMP_EOD, expected_symbol="AAPL")
        hangseng = parse_provider_payload(HANGSENG_A_SHARE_QUOTE_V1, HANGSENG_A_SHARE, expected_symbol="合成银行")
        self.assertEqual((fiu["symbol"], fiu["close"]), ("AAPL.US", Decimal("224.0")))
        self.assertEqual((alpha_quote["trade_date"], alpha_quote["change_percent"]), ("2026-09-03", "1.36"))
        json.dumps(alpha_quote, allow_nan=False)
        self.assertEqual((alpha_daily["timezone"], alpha_daily["bars"][0]["volume"]), ("US/Eastern", Decimal("12345")))
        self.assertEqual((fmp["trade_date"], fmp["close"]), ("2024-01-03", Decimal("184.0")))
        self.assertEqual(hangseng["raw_l1"], {"bidGrp": "10.3/100", "offerGrp": "10.4/100"})
        self.assertEqual(hangseng["raw_turnover_value"], "1000000")

    def test_doc6_structured_parsers_reject_record_count_and_unknown_raw_field_types(self):
        self.assert_code("FIU_US_SNAPSHOT_SHAPE_INVALID", FIU_US_QUOTE_SNAPSHOT_V1, {"body": []}, "AAPL.US")
        self.assert_code("ALPHAVANTAGE_GLOBAL_QUOTE_PERCENT_INVALID", ALPHAVANTAGE_GLOBAL_QUOTE_V1, {"Global Quote": {**ALPHA_GLOBAL_QUOTE["Global Quote"], "10. change percent": "1.36"}}, "AAPL")
        self.assert_code("ALPHAVANTAGE_DAILY_RECORD_COUNT", ALPHAVANTAGE_TIME_SERIES_DAILY_V1, {**ALPHA_DAILY, "Time Series (Daily)": {}}, "AAPL")
        self.assert_code("FMP_EOD_RECORD_COUNT", FMP_EOD_V1, FMP_EOD * 2, "AAPL")
        malformed = {"data": {"data": {"rows": [{**HANGSENG_A_SHARE["data"]["data"]["rows"][0], "turnoverValue": 1}]}}}
        self.assert_code("HANGSENG_A_SHARE_RAW_FIELD_INVALID", HANGSENG_A_SHARE_QUOTE_V1, malformed, "合成银行")


if __name__ == "__main__":
    unittest.main()

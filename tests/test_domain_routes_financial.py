from qveris_benchmark.domain_routes_financial import SEMANTIC_FIELD_ALIASES, SUPPORTED_KEYS, alpha_content_pointer, project, resolve, resolve_fmp
from qveris_benchmark.public_get import PublicGetAdapter
from qveris_benchmark.response_contract import validate_response


def request(venue="US", statement_type="income", presentation="standardized", year=2024, period="FY", fields=None):
    return {"kind": "financial_statement", "security": {"asset_class": "equity", "venue": venue, "symbol": "AAPL" if venue == "US" else "000001.SZ" if venue == "SZSE" else "600519.SH" if venue == "SSE" else "00700.HK"}, "statement": {"type": statement_type, "presentation": presentation, "period": {"kind": "specified_period", "fiscal_year": year, "fiscal_period": period}, "fields": fields or ["revenue"]}}


def test_alpha_default_selects_provider_fy_annual_row_and_marks_missing_fields_partial():
    plan = resolve(request(fields=["revenue", "net_income"]))
    assert plan and plan.tool_id == "alphavantage.income_statement.retrieve.v1.7aca3c4a"
    raw = {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": "2024-09-28", "reportedCurrency": "USD", "totalRevenue": "10"}]}
    projected = project(plan, raw)
    assert projected.status == "partial"
    assert projected.data["facts"] == {"revenue": {"value": "10", "period": "FY2024", "currency": "USD", "unit": "unknown", "nil": False}}
    assert projected.missing_fields == ("net_income",)


def test_alpha_fy_selection_uses_provider_fiscal_year_then_period_end_year_and_rejects_duplicates():
    plan = resolve(request(fields=["revenue"]))
    for ending in ("2024-09-28", "2024-09-30"):
        projected = project(plan, {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": ending, "reportedCurrency": "USD", "totalRevenue": "10"}]})
        assert projected.status == "success"
        assert projected.as_of == ending
    explicit_fiscal_year = project(plan, {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": "2025-01-31", "fiscalYear": "FY2024", "reportedCurrency": "USD", "totalRevenue": "10"}]})
    assert explicit_fiscal_year.status == "success"
    duplicate = project(plan, {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": "2024-09-28", "reportedCurrency": "USD", "totalRevenue": "10"}, {"fiscalDateEnding": "2024-09-30", "reportedCurrency": "USD", "totalRevenue": "11"}]})
    assert duplicate.data["facts"] == {}


def test_alpha_does_not_treat_a_wrong_report_year_as_the_requested_period():
    plan = resolve(request())
    projected = project(plan, {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": "2025-09-27", "reportedCurrency": "USD", "totalRevenue": "10"}]})
    assert projected.status == "partial"
    assert projected.data["facts"] == {}
    assert projected.missing_fields == ("revenue",)


def test_fmp_is_explicit_not_a_silent_alpha_fallback_and_selects_provider_period():
    semantic = request(period="Q2", fields=["revenue"])
    plan = resolve_fmp(semantic)
    assert resolve(semantic) == plan
    raw = [{"symbol": "AAPL", "date": "2024-03-30", "fiscalYear": "2024", "period": "Q2", "reportedCurrency": "USD", "revenue": 1}]
    projected = project(plan, raw)
    assert projected.status == "success"
    assert projected.data["facts"] == {"revenue": {"value": "1", "period": "Q22024", "currency": "USD", "unit": "unknown", "nil": False}}


def test_us_alpha_balance_uses_the_confirmed_downloaded_content_contract():
    plan = resolve(request("US", "balance", fields=["total_assets"]))
    assert plan and plan.tool_id == "alphavantage.balance_sheet.retrieve.v1.467a92c0"
    cash = resolve(request("US", "cash_flow", fields=["net_cash_from_operating"]))
    assert cash and cash.tool_id == "alphavantage.cash_flow.retrieve.v1.7aca3c4a"


def test_global_fmp_candidates_are_explicit_and_never_default_routes():
    semantic = request("JP", "income", fields=["revenue"])
    semantic["security"]["symbol"] = "7203.T"
    assert resolve(semantic).accepted_variant_id == "financial-fmp-global-income-v1"
    plan = resolve_fmp(semantic)
    assert plan and plan.params["limit"] == 5


def test_global_balance_projection_preserves_provider_currency_and_unknown_unit():
    semantic = request("GB", "balance", fields=["total_assets"])
    semantic["security"]["symbol"] = "VOD.L"
    plan = resolve(semantic)
    assert plan and plan.tool_id == "financialmodelingprep.stable.balancesheetstatement.retrieve.v1.bce203b1"
    assert plan.params == {"symbol": "VOD.L", "period": "annual", "limit": 5}
    raw = [{"symbol": "VOD.L", "date": "2024-03-31", "fiscalYear": "2024", "period": "FY", "reportedCurrency": "EUR", "totalAssets": 9}]
    projected = project(plan, raw)
    assert projected.data["facts"]["total_assets"] == {"value": "9", "period": "FY2024", "currency": "EUR", "unit": "unknown", "nil": False}


def test_global_fmp_income_replays_a_non_us_symbol_with_provider_string_fiscal_year():
    semantic = request("JP", "income", fields=["revenue"])
    semantic["security"]["symbol"] = "7203.T"
    plan = resolve(semantic)
    raw = [
        {"symbol": "7203.T", "date": "2025-03-31", "fiscalYear": "2025", "period": "FY", "reportedCurrency": "JPY", "revenue": 5},
        {"symbol": "7203.T", "date": "2024-03-31", "fiscalYear": "2024", "period": "FY", "reportedCurrency": "JPY", "revenue": 4},
        {"symbol": "7203.T", "date": "2023-03-31", "fiscalYear": "2023", "period": "FY", "reportedCurrency": "JPY", "revenue": 3},
        {"symbol": "7203.T", "date": "2022-03-31", "fiscalYear": "2022", "period": "FY", "reportedCurrency": "JPY", "revenue": 2},
        {"symbol": "7203.T", "date": "2021-03-31", "fiscalYear": "2021", "period": "FY", "reportedCurrency": "JPY", "revenue": 1},
    ]
    projected = project(plan, raw)
    assert projected.status == "success"
    assert projected.data["facts"]["revenue"] == {"value": "4", "period": "FY2024", "currency": "JPY", "unit": "unknown", "nil": False}


def test_cn_and_hk_routes_use_frozen_parameter_shapes_and_project_hk_balance_fields():
    szse = resolve(request("SZSE", "balance", fields=["total_assets"]))
    assert szse and szse.params == {"codes": "000001.SZ", "year": "2024", "period": "1231", "type": "1"}
    hk = resolve(request("HKEX", "cash_flow", fields=["net_cash_from_operating"]))
    assert hk and hk.params == {"symbol": "00700.HK", "startDate": "2024-12-31", "endDate": "2024-12-31", "reportType": "F", "sort": "asc"}
    balance = resolve(request("HKEX", "balance", fields=["total_assets"]))
    projected = project(balance, {"data": [{"symbol": "00700.HK", "reportDate": "2024-12-31", "reportType": "F", "fiscalYear": "2024/FY", "currency": "HKD", "totalAssets": 3}]})
    assert projected.status == "success"
    assert projected.data["facts"]["total_assets"]["currency"] == "HKD"
    assert ("HKEX", "financial.balance_sheet.standard.specified_period.v1") in SUPPORTED_KEYS


def test_szse_projection_uses_the_provider_row_period_not_the_requested_period():
    plan = resolve(request("SZSE", "balance", fields=["total_assets"]))
    projected = project(plan, [{"thscode": "000001.SZ", "time": "20241231", "ths_total_assets_stock": 4}])
    assert projected.status == "success"
    assert projected.as_of == "20241231"
    assert projected.data["facts"]["total_assets"]["period"] == "FY2024"


def test_alpha_pointer_is_only_a_syntactically_safe_pointer_not_an_inline_statement():
    pointer = "https://www.alphavantage.co/query?function=INCOME_STATEMENT"
    assert alpha_content_pointer({"result": {"full_content_file_url": pointer}}) == pointer
    assert alpha_content_pointer({"result": {"full_content_file_url": "http://www.alphavantage.co/x"}}) is None


def test_native_alpha_financial_fixture_normalizes_as_a_v2_public_response():
    native_request = request(fields=["revenue"])
    native_request["security"]["local_code"] = native_request["security"].pop("symbol")
    semantic = {"schema_version": "public-get.semantic/v1", "request": native_request}
    raw = {"symbol": "AAPL", "annualReports": [{"fiscalDateEnding": "2024-09-28", "reportedCurrency": "USD", "totalRevenue": "10"}]}
    adapter = PublicGetAdapter(lambda _query, **_kwargs: semantic, lambda _tool, _params, **_kwargs: raw, agent_variant_id="agent", agent_version="v1", get_variant_id="get", get_version="v1", model_identifier="model", model_version="v1", model_config_digest="a" * 64)
    result = adapter.run("AAPL FY2024 revenue", request_id="request-1", idempotency_key="private")
    assert result.public_response["schema_version"] == "get-response/v2"
    assert result.public_response["status"] == "success"
    assert result.public_response["as_of"] == "2024-09-28"
    validate_response(result.public_response, suite="financial_statements")


def test_latest_filed_uses_provider_filing_date_and_never_downgrades_to_report_date():
    semantic = request(fields=["revenue"])
    semantic["statement"]["period"] = {"kind": "latest", "basis": "filed", "frequency": "annual"}
    plan = resolve(semantic)
    assert plan and plan.params == {"symbol": "AAPL", "period": "annual", "limit": 5}
    raw = [
        {"symbol": "AAPL", "date": "2023-09-30", "filingDate": "2023-11-01", "fiscalYear": "2023", "period": "FY", "reportedCurrency": "USD", "revenue": 1},
        {"symbol": "AAPL", "date": "2024-09-28", "filingDate": "2024-10-31", "fiscalYear": "2024", "period": "FY", "reportedCurrency": "USD", "revenue": 2},
    ]
    projected = project(plan, raw)
    assert projected.status == "success"
    assert projected.as_of == "2024-10-31"
    assert projected.data["facts"]["revenue"]["period"] == "FY2024"
    missing_filing_date = project(plan, [{key: value for key, value in raw[-1].items() if key != "filingDate"}])
    assert missing_filing_date.data["facts"] == {}


def test_native_latest_filed_fixture_reaches_the_v2_public_contract():
    native_request = request(fields=["revenue"])
    native_request["security"]["local_code"] = native_request["security"].pop("symbol")
    native_request["statement"]["period"] = {"kind": "latest", "basis": "filed", "frequency": "annual"}
    raw = [{"symbol": "AAPL", "date": "2024-09-28", "filingDate": "2024-10-31", "fiscalYear": "2024", "period": "FY", "reportedCurrency": "USD", "revenue": 2}]
    adapter = PublicGetAdapter(lambda _query, **_kwargs: {"schema_version": "public-get.semantic/v1", "request": native_request}, lambda _tool, _params, **_kwargs: raw, agent_variant_id="agent", agent_version="v1", get_variant_id="get", get_version="v1", model_identifier="model", model_version="v1", model_config_digest="a" * 64)
    result = adapter.run("latest filed AAPL revenue", request_id="request-1", idempotency_key="private")
    assert result.public_response["status"] == "success"
    assert result.public_response["as_of"] == "2024-10-31"
    validate_response(result.public_response, suite="financial_statements")


def test_cash_flow_model_terms_have_only_explicit_canonical_aliases():
    assert SEMANTIC_FIELD_ALIASES == {"operating_cash_flow": "net_cash_from_operating", "investing_cash_flow": "net_cash_from_investing", "financing_cash_flow": "net_cash_from_financing"}

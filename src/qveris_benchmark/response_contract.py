"""Strict provider-free public GET response contract for deterministic scoring."""
from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any

SCHEMA_VERSION = "get-response/v1"
V2_SCHEMA_VERSION = "get-response/v2"
SUITES = frozenset({"financial_statements", "historical_price", "realtime_quote"})
STATUSES = frozenset({"success", "partial", "needs_clarification", "unsupported", "no_data", "error"})
_SUCCESS = frozenset({"schema_version", "status", "resolved_request", "data", "as_of", "source", "clarification", "terminal_reason", "meta"})
_STATE = frozenset({"schema_version", "status", "data", "clarification", "terminal_reason", "meta"})
_SENSITIVE = frozenset({"authorization", "token", "secret", "api_key", "password", "cookie", "credential", "access_token", "private_key", "raw_response", "provider_payload", "provider_response", "receipt", "execution_id"})
_ASSERTION = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
_MARKET = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_BAR_KEY = re.compile(r"^(?:d[0-9]{8}|w[0-9]{8}_[0-9]{8}|m[0-9]{6})$")
_UNIT = re.compile(r"^[A-Za-z][A-Za-z0-9_./-]{0,63}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})?$")
_AS_OF_STATUS = frozenset({"known", "mixed", "unavailable"})
_INTRADAY_INTERVALS = frozenset({"5min", "15min", "30min", "60min"})


class ResponseContractError(ValueError):
    """Raised when a response cannot be safely and deterministically scored."""


def _fail(message: str) -> None:
    raise ResponseContractError(message)


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value): _fail(path + " must be an object")
    return value


def _string(value: Any, path: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or (pattern and pattern.fullmatch(value) is None): _fail(path + " is invalid")
    return value


def _field_label(value: Any, path: str) -> str:
    if type(value) is not str or not value or len(value) > 256 or any(ord(character) < 32 or ord(character) == 127 for character in value): _fail(path + " is invalid")
    return value


def _exact(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    if set(value) != expected: _fail(path + " keys are invalid")


def _with_optional_usage(value: Mapping[str, Any], required: frozenset[str], path: str) -> None:
    _exact(value, required if "meta" in value else required - {"meta"}, path)


def _safe(value: Any, path: str = "", *, allow_arrays: bool = False) -> None:
    if isinstance(value, list):
        if not allow_arrays: _fail("arrays are not allowed in scoreable public response")
        for index, child in enumerate(value): _safe(child, path + str(index) + ".", allow_arrays=True)
        return
    if not isinstance(value, Mapping): return
    for key, child in value.items():
        if path == "meta." and key == "usage": continue  # fixed usage projection is checked below
        name = key.lower().replace("-", "_")
        compact = "".join(char for char in name if char.isalnum())
        if name in _SENSITIVE or any(part in _SENSITIVE for part in name.split("_")) or any(secret.replace("_", "") in compact for secret in _SENSITIVE): _fail("sensitive or raw-provider field is forbidden: " + path + key)
        _safe(child, path + key + ".", allow_arrays=allow_arrays)


def _meta(value: Any) -> dict[str, Any]:
    meta = _obj(value, "meta"); _exact(meta, frozenset({"usage"}), "meta")
    usage = _obj(meta["usage"], "meta.usage")
    fields = frozenset({"receipt_id", "measurement_version", "cache_status", "request_id", "issuer", "input_tokens", "output_tokens", "total_tokens"})
    _exact(usage, fields, "meta.usage")
    result = {key: _string(usage[key], "meta.usage." + key) for key in ("receipt_id", "measurement_version", "cache_status", "request_id", "issuer")}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if type(usage[key]) is not int or isinstance(usage[key], bool) or usage[key] < 0: _fail("meta.usage." + key + " is invalid")
        result[key] = usage[key]
    if result["input_tokens"] + result["output_tokens"] != result["total_tokens"]: _fail("meta.usage total does not equal input plus output")
    return {"usage": result}


def _value(value: Any, nil: Any, path: str) -> str | None:
    if type(nil) is not bool: _fail(path + ".nil must be boolean")
    if nil:
        if value is not None: _fail(path + ".value must be null when nil")
        return None
    return _string(value, path + ".value", _NUMBER)


def _instrument(value: Any, path: str) -> dict[str, str]:
    item = _obj(value, path); _exact(item, frozenset({"symbol", "market"}), path)
    return {"symbol": _string(item["symbol"], path + ".symbol", _SYMBOL), "market": _string(item["market"], path + ".market", _MARKET)}


def _financial(value: Any) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "facts"}), "data")
    if data["kind"] != "financial_statement": _fail("financial data.kind is invalid")
    facts = _obj(data["facts"], "data.facts")
    if not facts: _fail("data.facts must not be empty")
    expected = frozenset({"assertion_id", "field", "value", "period", "currency", "unit", "nil"}); normalized = {}
    for key, value in facts.items():
        _string(key, "data.facts key", _KEY); fact = _obj(value, "data.facts." + key); _exact(fact, expected, "data.facts." + key)
        assertion_id = _string(fact["assertion_id"], "data.facts." + key + ".assertion_id", _ASSERTION)
        if assertion_id.replace("-", "_") != key: _fail("data.facts key must equal normalized assertion_id")
        normalized[key] = {"assertion_id": assertion_id, "field": _field_label(fact["field"], "data.facts." + key + ".field"), "value": _value(fact["value"], fact["nil"], "data.facts." + key), "period": _string(fact["period"], "data.facts." + key + ".period"), "currency": _string(fact["currency"], "data.facts." + key + ".currency", re.compile(r"^[A-Z]{3}$")), "unit": _string(fact["unit"], "data.facts." + key + ".unit", _UNIT), "nil": fact["nil"]}
    return {"kind": "financial_statement", "facts": normalized}


def _historical(value: Any, accepted_variant_id: str) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "accepted_variant_id", "instrument", "interval", "adjustment", "bars"}), "data")
    if data["kind"] != "historical_price": _fail("historical data.kind is invalid")
    if _string(data["accepted_variant_id"], "data.accepted_variant_id", _ASSERTION) != accepted_variant_id: _fail("historical accepted_variant_id must equal resolved_request")
    bars = _obj(data["bars"], "data.bars")
    if not bars: _fail("data.bars must not be empty")
    normalized = {}
    for key, value in bars.items():
        _string(key, "data.bars key", _BAR_KEY); bar = _obj(value, "data.bars." + key); _exact(bar, frozenset({"period_key", "fields"}), "data.bars." + key)
        if bar["period_key"] != key: _fail("data.bars key must equal period_key")
        fields = _obj(bar["fields"], "data.bars." + key + ".fields")
        if not fields: _fail("data.bars fields must not be empty")
        normalized_fields = {}
        for field, entry in fields.items():
            _string(field, "bar field", _KEY); quote = _obj(entry, "bar field"); _exact(quote, frozenset({"value", "unit", "nil"}), "bar field")
            normalized_fields[field] = {"value": _value(quote["value"], quote["nil"], "bar field"), "unit": _string(quote["unit"], "bar unit", _UNIT), "nil": quote["nil"]}
        normalized[key] = {"period_key": key, "fields": normalized_fields}
    return {"kind": "historical_price", "accepted_variant_id": accepted_variant_id, "instrument": _instrument(data["instrument"], "data.instrument"), "interval": _string(data["interval"], "data.interval"), "adjustment": _string(data["adjustment"], "data.adjustment"), "bars": normalized}


def _realtime(value: Any, as_of: str) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "quote"}), "data")
    if data["kind"] != "realtime_quote": _fail("realtime data.kind is invalid")
    quote = _obj(data["quote"], "data.quote"); _exact(quote, frozenset({"instrument", "fields"}), "data.quote")
    fields = _obj(quote["fields"], "data.quote.fields")
    if not fields: _fail("data.quote.fields must not be empty")
    normalized = {}
    for field, value in fields.items():
        _string(field, "quote field", _KEY); entry = _obj(value, "quote field"); _exact(entry, frozenset({"value", "unit", "as_of", "nil"}), "quote field")
        if _string(entry["as_of"], "quote field.as_of") != as_of: _fail("quote field as_of must equal envelope as_of")
        normalized[field] = {"value": _value(entry["value"], entry["nil"], "quote field"), "unit": _string(entry["unit"], "quote field.unit", _UNIT), "as_of": as_of, "nil": entry["nil"]}
    return {"kind": "realtime_quote", "quote": {"instrument": _instrument(quote["instrument"], "data.quote.instrument"), "fields": normalized}}


def _quote_fields(value: Any, path: str, *, as_of: str | None, as_of_status: str) -> dict[str, Any]:
    fields = _obj(value, path)
    if not fields: _fail(path + " must not be empty")
    normalized = {}
    for field, entry in fields.items():
        _string(field, path + " field", _KEY); quote = _obj(entry, path + "." + field)
        _exact(quote, frozenset({"value", "unit", "as_of", "nil"}), path + "." + field)
        field_as_of = quote["as_of"]
        if as_of_status == "known":
            if _string(field_as_of, path + "." + field + ".as_of") != as_of: _fail(path + " field as_of must equal envelope as_of")
        elif as_of_status == "mixed":
            _string(field_as_of, path + "." + field + ".as_of")
        elif field_as_of is not None:
            _fail(path + " field as_of must be null when unavailable")
        normalized[field] = {"value": _value(quote["value"], quote["nil"], path + "." + field), "unit": _string(quote["unit"], path + "." + field + ".unit", _UNIT), "as_of": field_as_of, "nil": quote["nil"]}
    return normalized


def _v2_batch_realtime(value: Any, *, as_of: str | None, as_of_status: str) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "quotes"}), "data")
    if data["kind"] != "batch_realtime_quote": _fail("batch realtime data.kind is invalid")
    quotes = _obj(data["quotes"], "data.quotes")
    if not quotes: _fail("data.quotes must not be empty")
    normalized = {}
    for key, quote in quotes.items():
        _string(key, "data.quotes key", _SYMBOL); item = _obj(quote, "data.quotes." + key)
        _exact(item, frozenset({"instrument", "fields"}), "data.quotes." + key)
        instrument = _instrument(item["instrument"], "data.quotes." + key + ".instrument")
        if instrument["symbol"] != key: _fail("data.quotes key must equal instrument symbol")
        normalized[key] = {"instrument": instrument, "fields": _quote_fields(item["fields"], "data.quotes." + key + ".fields", as_of=as_of, as_of_status=as_of_status)}
    return {"kind": "batch_realtime_quote", "quotes": normalized}


def _v2_event_fields(value: Any, path: str) -> dict[str, Any]:
    fields = _obj(value, path)
    if not fields: _fail(path + " must not be empty")
    normalized = {}
    for field, entry in fields.items():
        _string(field, path + " field", _KEY); item = _obj(entry, path + "." + field)
        _exact(item, frozenset({"value", "unit", "nil"}), path + "." + field)
        normalized[field] = {"value": _value(item["value"], item["nil"], path + "." + field), "unit": _string(item["unit"], path + "." + field + ".unit", _UNIT), "nil": item["nil"]}
    return normalized


def _v2_historical_event(value: Any) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "instrument", "event_type", "events"}), "data")
    if data["kind"] != "historical_event": _fail("historical event data.kind is invalid")
    event_type = _string(data["event_type"], "data.event_type", _KEY)
    if event_type not in {"corporate_actions", "adjustment_factors"}: _fail("data.event_type is invalid")
    events = _obj(data["events"], "data.events")
    if not events: _fail("data.events must not be empty")
    normalized = {}
    for key, event in events.items():
        _string(key, "data.events key", _BAR_KEY); item = _obj(event, "data.events." + key)
        _exact(item, frozenset({"period_key", "fields"}) | (frozenset({"description"}) if "description" in item else frozenset()), "data.events." + key)
        if item["period_key"] != key: _fail("data.events key must equal period_key")
        normalized[key] = {"period_key": key, "fields": _v2_event_fields(item["fields"], "data.events." + key + ".fields")}
        if "description" in item: normalized[key]["description"] = _field_label(item["description"], "data.events." + key + ".description")
    return {"kind": "historical_event", "instrument": _instrument(data["instrument"], "data.instrument"), "event_type": event_type, "events": normalized}


def _v2_intraday_timestamp(value: Any, path: str) -> str:
    timestamp = _string(value, path, _TIMESTAMP)
    try:
        from datetime import datetime
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResponseContractError(path + " is invalid") from exc
    return timestamp


def _v2_intraday_historical(value: Any, accepted_variant_id: str) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "accepted_variant_id", "instrument", "interval", "adjustment", "time_basis", "timezone", "bars"}), "data")
    if data["kind"] != "historical_price" or data["time_basis"] != "provider_timestamp": _fail("v2 intraday kind or time_basis is invalid")
    if _string(data["accepted_variant_id"], "data.accepted_variant_id", _ASSERTION) != accepted_variant_id: _fail("historical accepted_variant_id must equal resolved_request")
    interval = _string(data["interval"], "data.interval")
    if interval not in _INTRADAY_INTERVALS: _fail("v2 intraday interval is invalid")
    timezone = _field_label(data["timezone"], "data.timezone")
    bars = _obj(data["bars"], "data.bars")
    if not bars: _fail("data.bars must not be empty")
    normalized = {}
    for key, bar in bars.items():
        timestamp = _v2_intraday_timestamp(key, "data.bars key")
        item = _obj(bar, "data.bars." + key); _exact(item, frozenset({"period", "fields"}), "data.bars." + key)
        period = _obj(item["period"], "data.bars." + key + ".period"); _exact(period, frozenset({"timestamp", "interval"}), "data.bars." + key + ".period")
        if _v2_intraday_timestamp(period["timestamp"], "data.bars." + key + ".period.timestamp") != timestamp or period["interval"] != interval: _fail("data.bars period must bind key and interval")
        normalized[timestamp] = {"period": {"timestamp": timestamp, "interval": interval}, "fields": _v2_event_fields(item["fields"], "data.bars." + key + ".fields")}
    return {"kind": "historical_price", "accepted_variant_id": accepted_variant_id, "instrument": _instrument(data["instrument"], "data.instrument"), "interval": interval, "adjustment": _string(data["adjustment"], "data.adjustment"), "time_basis": "provider_timestamp", "timezone": timezone, "bars": normalized}


def _v2_calendar(value: Any) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "venue", "dates", "range", "time_basis"}), "data")
    if data["kind"] != "market_calendar" or data["time_basis"] != "coverage_range": _fail("market calendar kind or time_basis is invalid")
    venue = _string(data["venue"], "data.venue", _MARKET)
    values = data["dates"]
    if not isinstance(values, list) or not values: _fail("data.dates must be a non-empty array")
    dates = [_string(item, "data.dates", _DATE) for item in values]
    if len(dates) != len(set(dates)): _fail("data.dates must be unique")
    period = _obj(data["range"], "data.range"); _exact(period, frozenset({"start_date", "end_date"}), "data.range")
    start, end = _string(period["start_date"], "data.range.start_date", _DATE), _string(period["end_date"], "data.range.end_date", _DATE)
    if start > end or any(item < start or item > end for item in dates): _fail("data dates are outside range")
    return {"kind": "market_calendar", "venue": venue, "dates": dates, "range": {"start_date": start, "end_date": end}, "time_basis": "coverage_range"}


def _v2_financial(value: Any) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "instrument", "statement_type", "presentation", "facts"}), "data")
    if data["kind"] != "financial_statement": _fail("v2 financial data.kind is invalid")
    statement_type = _string(data["statement_type"], "data.statement_type", _KEY)
    if statement_type not in {"income_statement", "balance_sheet", "cash_flow"}: _fail("data.statement_type is invalid")
    presentation = _string(data["presentation"], "data.presentation", _KEY)
    if presentation not in {"standardized", "as_reported"}: _fail("data.presentation is invalid")
    facts = _obj(data["facts"], "data.facts")
    if not facts: _fail("data.facts must not be empty")
    normalized = {}
    for key, fact in facts.items():
        _string(key, "data.facts key", _KEY); item = _obj(fact, "data.facts." + key)
        _exact(item, frozenset({"value", "period", "currency", "unit", "nil"}), "data.facts." + key)
        currency = item["currency"]
        if currency != "unknown": _string(currency, "data.facts." + key + ".currency", re.compile(r"^[A-Z]{3}$"))
        normalized[key] = {"value": _value(item["value"], item["nil"], "data.facts." + key), "period": _string(item["period"], "data.facts." + key + ".period"), "currency": currency, "unit": _string(item["unit"], "data.facts." + key + ".unit", _UNIT), "nil": item["nil"]}
    return {"kind": "financial_statement", "instrument": _instrument(data["instrument"], "data.instrument"), "statement_type": statement_type, "presentation": presentation, "facts": normalized}


def _v2_market_status(value: Any) -> dict[str, Any]:
    data = _obj(value, "data"); _exact(data, frozenset({"kind", "instrument", "status"}), "data")
    if data["kind"] != "market_status": _fail("market status data.kind is invalid")
    return {"kind": "market_status", "instrument": _instrument(data["instrument"], "data.instrument"), "status": _field_label(data["status"], "data.status")}


def _coverage(value: Any, status: str) -> dict[str, Any]:
    coverage = _obj(value, "coverage"); _exact(coverage, frozenset({"complete", "missing_fields"}), "coverage")
    complete = coverage["complete"]
    missing = coverage["missing_fields"]
    if type(complete) is not bool or not isinstance(missing, list) or not all(type(item) is str and _KEY.fullmatch(item) for item in missing) or len(missing) != len(set(missing)): _fail("coverage is invalid")
    if complete != (status == "success") or bool(missing) != (status == "partial"): _fail("coverage does not match status")
    return {"complete": complete, "missing_fields": list(missing)}


def _v2_success(source: Mapping[str, Any], suite: str | None) -> dict[str, Any]:
    _with_optional_usage(source, _SUCCESS | frozenset({"as_of_status", "coverage"}), "response")
    if source["clarification"] is not None or source["terminal_reason"] is not None: _fail("successful envelope is invalid")
    request = _obj(source["resolved_request"], "resolved_request"); _exact(request, frozenset({"suite", "accepted_variant_id"}), "resolved_request")
    request_suite = _string(request["suite"], "resolved_request.suite")
    if request_suite not in SUITES or (suite is not None and suite != request_suite): _fail("resolved_request.suite is invalid")
    as_of_status = _string(source["as_of_status"], "as_of_status")
    if as_of_status not in _AS_OF_STATUS: _fail("as_of_status is invalid")
    as_of = source["as_of"]
    if as_of_status == "known": as_of = _string(as_of, "as_of")
    elif as_of is not None: _fail("as_of must be null when not known")
    status = source["status"]
    data_object = _obj(source["data"], "data")
    if request_suite == "realtime_quote":
        data = _v2_batch_realtime(data_object, as_of=as_of, as_of_status=as_of_status) if data_object.get("kind") == "batch_realtime_quote" else _v2_market_status(data_object)
    elif request_suite == "historical_price":
        data = _v2_intraday_historical(data_object, _string(request["accepted_variant_id"], "resolved_request.accepted_variant_id", _ASSERTION)) if data_object.get("kind") == "historical_price" else _v2_historical_event(data_object) if data_object.get("kind") == "historical_event" else _v2_calendar(data_object)
    else:
        data = _v2_financial(data_object)
    accepted_variant_id = _string(request["accepted_variant_id"], "resolved_request.accepted_variant_id", _ASSERTION)
    return {"schema_version": V2_SCHEMA_VERSION, "status": status, "resolved_request": {"suite": request_suite, "accepted_variant_id": accepted_variant_id}, "data": data, "as_of": as_of, "as_of_status": as_of_status, "source": _string(source["source"], "source"), "clarification": None, "terminal_reason": None, "coverage": _coverage(source["coverage"], status)}


def normalize_response(response: Any, *, suite: str | None = None, diagnostic: bool = False) -> dict[str, Any]:
    source = _obj(response, "response")
    schema_version = source.get("schema_version")
    if diagnostic:
        if schema_version not in {SCHEMA_VERSION, V2_SCHEMA_VERSION} or source.get("status") not in STATUSES: _fail("diagnostic envelope is invalid")
        _safe(source, allow_arrays=schema_version == V2_SCHEMA_VERSION); return json.loads(json.dumps(source, ensure_ascii=False, allow_nan=False, sort_keys=True))
    if suite is not None and suite not in SUITES: _fail("suite is invalid")
    if schema_version not in {SCHEMA_VERSION, V2_SCHEMA_VERSION}: _fail("schema_version is invalid")
    _safe(source, allow_arrays=schema_version == V2_SCHEMA_VERSION); status = source.get("status")
    if status in {"success", "partial"}:
        if schema_version == V2_SCHEMA_VERSION:
            result = _v2_success(source, suite)
            if "meta" in source: result["meta"] = _meta(source["meta"])
            return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
        _with_optional_usage(source, _SUCCESS, "response")
        if source["schema_version"] != SCHEMA_VERSION or source["clarification"] is not None or source["terminal_reason"] is not None: _fail("successful envelope is invalid")
        request = _obj(source["resolved_request"], "resolved_request"); _exact(request, frozenset({"suite", "accepted_variant_id"}), "resolved_request")
        request_suite = _string(request["suite"], "resolved_request.suite")
        if request_suite not in SUITES or (suite is not None and suite != request_suite): _fail("resolved_request.suite is invalid")
        as_of = _string(source["as_of"], "as_of")
        accepted_variant_id = _string(request["accepted_variant_id"], "resolved_request.accepted_variant_id", _ASSERTION)
        data = _financial(source["data"]) if request_suite == "financial_statements" else _historical(source["data"], accepted_variant_id) if request_suite == "historical_price" else _realtime(source["data"], as_of)
        result = {"schema_version": SCHEMA_VERSION, "status": status, "resolved_request": {"suite": request_suite, "accepted_variant_id": accepted_variant_id}, "data": data, "as_of": as_of, "source": _string(source["source"], "source"), "clarification": None, "terminal_reason": None}
    elif status == "needs_clarification":
        _with_optional_usage(source, _STATE, "response")
        if source["schema_version"] not in {SCHEMA_VERSION, V2_SCHEMA_VERSION} or source["data"] is not None or source["terminal_reason"] is not None: _fail("clarification response is invalid")
        result = {"schema_version": schema_version, "status": status, "data": None, "clarification": _string(source["clarification"], "clarification"), "terminal_reason": None}
    elif status in {"unsupported", "no_data", "error"}:
        _with_optional_usage(source, _STATE, "response")
        if source["schema_version"] not in {SCHEMA_VERSION, V2_SCHEMA_VERSION} or source["data"] is not None or source["clarification"] is not None: _fail("terminal response is invalid")
        result = {"schema_version": schema_version, "status": status, "data": None, "clarification": None, "terminal_reason": _string(source["terminal_reason"], "terminal_reason")}
    else: _fail("status is invalid")
    if "meta" in source:
        result["meta"] = _meta(source["meta"])
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))


def validate_response(response: Any, *, suite: str | None = None, diagnostic: bool = False) -> None:
    normalize_response(response, suite=suite, diagnostic=diagnostic)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result: _fail("duplicate JSON object key: " + key)
        result[key] = value
    return result


def normalize_json_response(payload: str, *, suite: str | None = None, diagnostic: bool = False) -> dict[str, Any]:
    if type(payload) is not str: _fail("response JSON must be a string")
    try: value = json.loads(payload, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ResponseContractError) as exc: raise ResponseContractError("response JSON is invalid") from exc
    return normalize_response(value, suite=suite, diagnostic=diagnostic)


__all__ = ["ResponseContractError", "SCHEMA_VERSION", "STATUSES", "SUITES", "V2_SCHEMA_VERSION", "normalize_json_response", "normalize_response", "validate_response"]

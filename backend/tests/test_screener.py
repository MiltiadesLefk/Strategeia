"""Screener: the filter engine, the per-symbol fields, a run over fake data, saved
screens and the endpoints. No network: a fake provider serves synthetic bars."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_data_provider
from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    FinancialsData,
    FinancialYear,
)
from app.data_providers.universe import UniverseEntry
from app.main import app
from app.schemas.screener_schemas import SavedScreenCreate, ScreenerRunRequest
from app.services import screener_engine as engine
from app.services import screener_service as svc
from app.services import screener_store
from app.services.screener_engine import FilterError

# ---------------------------------------------------------------- fixtures


def frame(closes, volume=1_000, last_volume=None):
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    volumes = np.full(n, float(volume))
    if last_volume is not None:
        volumes[-1] = last_volume
    return pd.DataFrame(
        {
            "date": pd.bdate_range(end="2026-09-30", periods=n),
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": volumes,
        }
    )


def uptrend(n=260, start=50.0, step=0.4):
    return list(start + step * np.arange(n))


def downtrend(n=260, start=150.0, step=0.4):
    return list(start - step * np.arange(n))


BARS = {
    "UPP": frame(uptrend(), last_volume=3_000),  # rising, with a 3x volume day
    "DWN": frame(downtrend()),
    "NEW": frame(uptrend(30)),  # too little history for the chart fields
    "FLT": frame([100.0] * 260),
}
OVERVIEWS = {
    "UPP": CompanyOverview("UPP", "Upp", 5e9, 12.0, None, None, None, None),
    "DWN": CompanyOverview("DWN", "Dwn", 1e9, -5.0, None, None, None, None),  # losses: no usable P/E
}
FINANCIALS = {
    "UPP": FinancialsData("UPP", [FinancialYear(2024, 100.0, 10.0), FinancialYear(2025, 120.0, 12.0)]),
}
UNIVERSE = [
    UniverseEntry("UPP", "Upp Corp", "Technology"),
    UniverseEntry("DWN", "Dwn Corp", "Energy"),
    UniverseEntry("NEW", "New Corp", "Technology"),
    UniverseEntry("FLT", "Flat Corp", "Utilities"),
    UniverseEntry("GONE", "Gone Corp", "Utilities"),  # no provider has it
]


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.ohlcv_calls: list[str] = []
        self.overview_calls: list[str] = []
        self.financials_calls: list[str] = []

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        self.ohlcv_calls.append(symbol)
        if symbol not in BARS:
            raise AllProvidersFailedError(f"no data for {symbol}")
        return BARS[symbol]

    def get_company_overview(self, symbol):
        self.overview_calls.append(symbol)
        if symbol not in OVERVIEWS:
            raise AllProvidersFailedError("none")
        return OVERVIEWS[symbol]

    def get_financials(self, symbol):
        self.financials_calls.append(symbol)
        if symbol not in FINANCIALS:
            raise AllProvidersFailedError("none")
        return FINANCIALS[symbol]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "load_universe", lambda: list(UNIVERSE))
    monkeypatch.setattr(screener_store, "saved_file", lambda: tmp_path / "screener_saved.json")


def rule(field, op, value=None, value2=None):
    return {"field": field, "op": op, "value": value, "value2": value2}


def run(provider=None, **body):
    return svc.run_screen(provider or FakeProvider(), ScreenerRunRequest(**body))


# ---------------------------------------------------------------- engine: operators


ROWS = [
    {"symbol": "A", "price": 10.0, "sector": "Technology", "trend": "Bullish"},
    {"symbol": "B", "price": 20.0, "sector": "Energy", "trend": "Bearish"},
    {"symbol": "C", "price": 30.0, "sector": "Technology", "trend": None},
    {"symbol": "D", "price": None, "sector": "Utilities", "trend": "Neutral"},
]


def matched(*rules):
    criteria = [engine.validate_criterion(r["field"], r["op"], r.get("value"), r.get("value2")) for r in rules]
    rows, skipped = engine.apply_filters(ROWS, criteria)
    return [r["symbol"] for r in rows], skipped


@pytest.mark.parametrize(
    "op, value, expected",
    [
        ("gt", 20, ["C"]),
        ("gte", 20, ["B", "C"]),
        ("lt", 20, ["A"]),
        ("lte", 20, ["A", "B"]),
        ("eq", 20, ["B"]),
        ("neq", 20, ["A", "C"]),
    ],
)
def test_number_operators(op, value, expected):
    symbols, skipped = matched(rule("price", op, value))
    assert symbols == expected
    assert skipped == 1  # D has no price: dropped for it, never matched on a guess


def test_between_is_inclusive_and_needs_both_bounds():
    assert matched(rule("price", "between", 10, 20))[0] == ["A", "B"]
    with pytest.raises(FilterError, match="upper bound"):
        engine.validate_criterion("price", "between", 10, None)
    with pytest.raises(FilterError, match="lower bound"):
        engine.validate_criterion("price", "between", 30, 10)


def test_text_operators_are_case_insensitive():
    assert matched(rule("sector", "eq", "technology"))[0] == ["A", "C"]
    assert matched(rule("sector", "neq", "technology"))[0] == ["B", "D"]
    assert matched(rule("sector", "contains", "TECH"))[0] == ["A", "C"]
    assert matched(rule("trend", "eq", "bullish")) == (["A"], 1)  # C has no trend


def test_every_rule_must_hold():
    assert matched(rule("price", "gte", 10), rule("sector", "eq", "Technology"))[0] == ["A", "C"]
    assert matched(rule("price", "gt", 10), rule("sector", "eq", "Technology"))[0] == ["C"]


def test_a_missing_value_is_counted_only_when_the_row_otherwise_passes():
    # D is missing price; it also fails the sector rule, so it is a plain non-match, not a "skipped".
    symbols, skipped = matched(rule("price", "gt", 0), rule("sector", "eq", "Utilities"))
    assert symbols == [] and skipped == 1
    symbols, skipped = matched(rule("price", "gt", 0), rule("sector", "eq", "Energy"))
    assert symbols == ["B"] and skipped == 0


def test_no_rules_keep_everything():
    rows, skipped = engine.apply_filters(ROWS, [])
    assert len(rows) == 4 and skipped == 0


# ---------------------------------------------------------------- engine: validation


def test_unknown_field_and_wrong_operator_are_rejected():
    with pytest.raises(FilterError, match="Unknown field"):
        engine.validate_criterion("nonsense", "gt", 1)
    with pytest.raises(FilterError, match="cannot be used"):
        engine.validate_criterion("sector", "gt", "x")
    with pytest.raises(FilterError, match="cannot be used"):
        engine.validate_criterion("price", "contains", 1)
    with pytest.raises(FilterError, match="cannot be used"):
        engine.validate_criterion("price", "bogus", 1)


def test_values_must_be_the_right_kind():
    with pytest.raises(FilterError, match="needs a number"):
        engine.validate_criterion("price", "gt", "abc")
    with pytest.raises(FilterError, match="needs a number"):
        engine.validate_criterion("price", "gt", None)
    with pytest.raises(FilterError, match="finite"):
        engine.validate_criterion("price", "gt", float("inf"))
    with pytest.raises(FilterError, match="needs a value"):
        engine.validate_criterion("sector", "eq", "  ")
    assert engine.validate_criterion("price", "gt", "5.5").value == 5.5  # a numeric string is accepted


def test_sort_and_columns_are_validated():
    assert engine.validate_sort("symbol") == "symbol" and engine.validate_sort(None) is None
    with pytest.raises(FilterError):
        engine.validate_sort("nonsense")
    with pytest.raises(FilterError):
        engine.validate_columns(["price", "nonsense"])


def test_catalogue_is_complete_and_consistent():
    names = [f.name for f in engine.FIELD_CATALOGUE]
    assert len(names) == len(set(names))
    for required in (
        "price", "change_pct", "volume_ratio", "trend", "momentum", "rsi14", "scanner_score",
        "pct_from_ema20", "week52_position_pct", "pe_ratio", "market_cap", "revenue_growth_pct", "sector",
    ):  # fmt: skip
        assert required in engine.FIELDS
    assert all(f.kind in ("number", "text") and f.description for f in engine.FIELD_CATALOGUE)


def test_sorting_puts_missing_values_last_in_both_directions():
    rows = [{"symbol": "A", "price": 2.0}, {"symbol": "B", "price": None}, {"symbol": "C", "price": 3.0}, {"symbol": "D", "price": 2.0}]
    assert [r["symbol"] for r in engine.sort_rows(rows, "price", True)] == ["C", "A", "D", "B"]
    assert [r["symbol"] for r in engine.sort_rows(rows, "price", False)] == ["A", "D", "C", "B"]
    assert [r["symbol"] for r in engine.sort_rows(rows, None, True)] == ["A", "B", "C", "D"]


# ---------------------------------------------------------------- per-symbol fields


def test_bar_fields_for_a_rising_stock():
    values = svc.compute_bar_fields("UPP", BARS["UPP"])
    assert values["price"] == pytest.approx(BARS["UPP"]["close"].iloc[-1])
    assert values["change_pct"] == pytest.approx((values["price"] / BARS["UPP"]["close"].iloc[-2] - 1) * 100)
    assert values["volume_ratio"] == pytest.approx(3.0)
    assert values["trend"] == "Bullish"
    assert values["pct_from_ema20"] > 0
    assert 0 < values["week52_position_pct"] <= 100
    assert values["week52_position_pct"] > 90  # at the top of a straight climb
    assert values["scanner_score"] is not None and values["signal"] in ("potential_setup", "watching", "no_signal")


def test_bar_fields_for_a_falling_stock():
    values = svc.compute_bar_fields("DWN", BARS["DWN"])
    assert values["trend"] == "Bearish" and values["change_pct"] < 0
    assert values["week52_position_pct"] < 10


def test_short_history_leaves_chart_and_52w_fields_blank():
    values = svc.compute_bar_fields("NEW", BARS["NEW"])
    assert values["price"] is not None and values["change_pct"] is not None
    assert values["trend"] is None and values["rsi14"] is None and values["scanner_score"] is None
    assert values["week52_position_pct"] is None
    assert values["volume_ratio"] == pytest.approx(1.0)


def test_flat_prices_have_no_52w_position():
    values = svc.compute_bar_fields("FLT", frame([100.0] * 260).assign(high=100.0, low=100.0))
    assert values["week52_position_pct"] is None


# ---------------------------------------------------------------- running a screen


def test_run_filters_sorts_and_reports_what_it_scanned():
    result = run(filters=[rule("trend", "eq", "Bullish")], sort_field="price", sort_dir="desc")
    assert [r.symbol for r in result.rows] == ["UPP"]
    assert result.scanned == 5 and result.universe_size == 5
    assert result.missing == ["GONE"]
    assert result.matched == 1
    # NEW has no trend (too little history): dropped because of the missing field, and counted.
    assert result.skipped_missing_data == 1


def test_run_sort_limit_and_columns():
    result = run(sort_field="price", sort_dir="asc", limit=2, columns=["pe_ratio"])
    prices = [r.values["price"] for r in result.rows]
    assert len(result.rows) == 2 and prices == sorted(prices)
    assert result.matched == 4 and "pe_ratio" in result.columns


def test_fundamentals_are_only_fetched_when_used():
    plain = FakeProvider()
    run(plain, filters=[rule("price", "gt", 0)])
    assert plain.overview_calls == [] and plain.financials_calls == []

    used = FakeProvider()
    result = run(used, filters=[rule("pe_ratio", "lt", 20), rule("revenue_growth_pct", "gt", 10)])
    assert "UPP" in used.overview_calls and "UPP" in used.financials_calls
    assert [r.symbol for r in result.rows] == ["UPP"]
    row = result.rows[0]
    assert row.values["pe_ratio"] == 12.0 and row.values["revenue_growth_pct"] == pytest.approx(20.0)
    assert "market_cap" not in row.values  # fetched alongside P/E, but not a column anyone asked for


def test_overview_fields_come_together_from_one_fetch():
    result = run(filters=[rule("market_cap", "gt", 1e9)])
    assert [r.symbol for r in result.rows] == ["UPP"]  # DWN is exactly 1e9, not above
    assert result.rows[0].values["market_cap"] == 5e9


def test_a_loss_making_company_has_no_pe_rather_than_a_negative_one():
    result = run(filters=[rule("pe_ratio", "lt", 100)])
    assert [r.symbol for r in result.rows] == ["UPP"]


def test_scan_cap_limits_how_many_symbols_are_read():
    provider = FakeProvider()
    result = run(provider, scan_cap=2)
    assert result.scanned == 2 and result.universe_size == 5
    assert provider.ohlcv_calls.count("UPP") == 1 and "FLT" not in provider.ohlcv_calls


def test_explicit_symbols_replace_the_universe():
    result = run(symbols=["dwn", "UPP", "dwn"], sort_field="symbol", sort_dir="asc")
    assert [r.symbol for r in result.rows] == ["DWN", "UPP"] and result.universe_size == 2
    assert result.rows[0].values["sector"] == "Energy"
    unknown = run(symbols=["ZZZ"])
    assert unknown.missing == ["ZZZ"] and unknown.rows == []


def test_run_rejects_a_bad_rule():
    with pytest.raises(FilterError):
        run(filters=[rule("nonsense", "gt", 1)])
    with pytest.raises(FilterError):
        run(sort_field="nonsense")


def test_request_caps():
    with pytest.raises(ValueError):
        ScreenerRunRequest(symbols=[f"S{i}" for i in range(201)])
    with pytest.raises(ValueError):
        ScreenerRunRequest(scan_cap=201)
    with pytest.raises(ValueError):
        ScreenerRunRequest(limit=0)
    with pytest.raises(ValueError):
        ScreenerRunRequest(filters=[rule("price", "gt", 1)] * 13)


# ---------------------------------------------------------------- saved screens


def spec(name="Rising tech", **extra):
    base = dict(name=name, filters=[rule("sector", "eq", "Technology")], sort_field="price", sort_dir="desc", limit=20)
    return SavedScreenCreate(**{**base, **extra})


def test_saved_screen_crud_round_trip(tmp_path):
    assert screener_store.list_saved() == []
    first = screener_store.create_saved(spec("Zeta"))
    screener_store.create_saved(spec("alpha"))
    names = [s.name for s in screener_store.list_saved()]
    assert names == ["alpha", "Zeta"]  # case-insensitive order
    assert first.id and first.filters[0].field == "sector"
    assert screener_store.delete_saved(first.id) is True
    assert screener_store.delete_saved(first.id) is False
    assert [s.name for s in screener_store.list_saved()] == ["alpha"]
    on_disk = json.loads((tmp_path / "screener_saved.json").read_text(encoding="utf-8"))
    assert on_disk["version"] == 1 and len(on_disk["screens"]) == 1


def test_duplicate_names_and_the_cap_are_refused(monkeypatch):
    screener_store.create_saved(spec("One"))
    with pytest.raises(screener_store.DuplicateNameError):
        screener_store.create_saved(spec("  one "))
    monkeypatch.setattr(screener_store, "MAX_SAVED_SCREENS", 1)
    with pytest.raises(screener_store.TooManyScreensError):
        screener_store.create_saved(spec("Two"))


def test_a_damaged_file_reads_as_empty_and_the_next_save_repairs_it(tmp_path):
    (tmp_path / "screener_saved.json").write_text("{not json", encoding="utf-8")
    assert screener_store.list_saved() == []
    screener_store.create_saved(spec())
    assert len(screener_store.list_saved()) == 1


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def client():
    provider = FakeProvider()
    app.dependency_overrides[get_data_provider] = lambda: provider
    yield TestClient(app), provider
    app.dependency_overrides.pop(get_data_provider, None)


def test_fields_endpoint_lists_the_catalogue(client):
    http, _ = client
    body = http.get("/api/screener/fields").json()
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["price"]["operators"] == list(engine.NUMBER_OPERATORS)
    assert by_name["sector"]["operators"] == list(engine.TEXT_OPERATORS)
    assert by_name["pe_ratio"]["costly"] is True and by_name["price"]["costly"] is False
    assert body["max_symbols"] == 200 and body["default_scan_cap"] == 60


def test_run_endpoint_filters(client):
    http, _ = client
    resp = http.post("/api/screener/run", json={"filters": [rule("trend", "eq", "Bullish")]})
    assert resp.status_code == 200
    body = resp.json()
    assert [r["symbol"] for r in body["rows"]] == ["UPP"]
    assert body["scanned"] == 5 and body["universe_size"] == 5 and body["missing"] == ["GONE"]


def test_run_endpoint_rejects_bad_input(client):
    http, _ = client
    assert http.post("/api/screener/run", json={"filters": [rule("nonsense", "gt", 1)]}).status_code == 422
    assert http.post("/api/screener/run", json={"filters": [rule("price", "between", 1)]}).status_code == 422
    assert http.post("/api/screener/run", json={"symbols": ["bad$"]}).status_code == 422
    assert http.post("/api/screener/run", json={"symbols": [f"S{i}" for i in range(201)]}).status_code == 422
    assert http.post("/api/screener/run", json={"scan_cap": 500}).status_code == 422
    assert http.post("/api/screener/run", json={"sort_dir": "sideways"}).status_code == 422


def test_run_endpoint_does_not_save_anything(client, tmp_path):
    http, _ = client
    http.post("/api/screener/run", json={})
    assert not (tmp_path / "screener_saved.json").exists()


def test_saved_endpoints_round_trip(client):
    http, _ = client
    assert http.get("/api/screener/saved").json() == []
    created = http.post("/api/screener/saved", json={"name": "Mine", "filters": [rule("price", "gt", 10)], "limit": 25})
    assert created.status_code == 201
    saved = created.json()
    assert saved["name"] == "Mine" and saved["limit"] == 25 and saved["created_at"].endswith("Z")
    assert http.post("/api/screener/saved", json={"name": "mine"}).status_code == 409
    assert http.post("/api/screener/saved", json={"name": "Bad", "filters": [rule("nonsense", "gt", 1)]}).status_code == 422
    assert http.post("/api/screener/saved", json={"name": ""}).status_code == 422
    assert [s["name"] for s in http.get("/api/screener/saved").json()] == ["Mine"]
    deleted = http.delete(f"/api/screener/saved/{saved['id']}")
    assert deleted.status_code == 200 and deleted.json() == {"deleted": saved["id"]}
    assert http.delete(f"/api/screener/saved/{saved['id']}").status_code == 404
    assert http.get("/api/screener/saved").json() == []

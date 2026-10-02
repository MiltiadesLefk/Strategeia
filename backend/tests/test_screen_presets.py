"""Screen presets: each criterion on fixture data, the match rules (required criteria, minimum
matches), missing data read as "cannot tell" (never as a pass), the bounded run, and the endpoints.
No network: fake data providers."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import screen_presets as sp
from app.analysis.screen_presets import SymbolData, evaluate, get_preset
from app.analysis.trend import analyze_chart
from app.api.deps import get_app_settings, get_data_provider
from app.config import AppSettings
from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    FinancialsData,
    FinancialYear,
    InsiderActivity,
    QuoteData,
)
from app.main import app
from app.markets import to_market_time
from app.services.screen_preset_service import PRESET_DEFAULT_LIMIT, PRESET_MAX_LIMIT, run_preset
from app.timeutil import utcnow_naive

TODAY = to_market_time(utcnow_naive()).date()


def overview(pe=12.0, eps=5.0, low=100.0, high=200.0):
    return CompanyOverview("X", "X Corp", 1e10, pe, 1e9, eps, low, high)


def years(*rows):
    """(revenue, net_income) per year, oldest first."""
    return [FinancialYear(2020 + i, r, n) for i, (r, n) in enumerate(rows)]


def chart(kind):
    t = np.arange(260, dtype=float)
    # compounding, so the 10-day rate of change clears the 'strong momentum' bar
    closes = 100 * 1.01**t if kind == "up" else 1000 * 0.99**t
    frame = pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1, "close": closes, "volume": 1e6})
    return analyze_chart(frame)


def insider(buys=3, sells=0, buy_value=400_000.0, sell_value=0.0):
    return InsiderActivity("X", 90, buys, sells, buy_value, sell_value)


def status(preset_name, **fields):
    data = SymbolData(symbol="X", today=TODAY, **fields)
    return evaluate(get_preset(preset_name), data)


def ok_by_id(evaluation):
    return {r.id: r.ok for r in evaluation.results}


# ---------------------------------------------------------------------- value


def test_value_matches_on_a_low_pe_plus_one_more_criterion():
    result = status("value", overview=overview(pe=10.0, eps=4.0, low=100, high=200), price=120.0, insider=None)
    assert result.status == "match"
    assert ok_by_id(result) == {"pe_low": True, "profitable": True, "low_in_range": True, "insider_buying": None}


def test_value_needs_the_required_pe_and_two_matches():
    assert status("value", overview=overview(pe=25.0), price=110.0, insider=insider()).status == "no_match"
    only_pe = status("value", overview=overview(pe=10.0, eps=-1.0), price=190.0, insider=insider(buys=0, buy_value=0))
    assert only_pe.status == "no_match"  # P/E alone is one match; the preset asks for two
    assert status("value", overview=overview(pe=-5.0)).status == "no_match"  # a negative P/E is not cheap


def test_a_missing_required_datum_is_unjudged_never_a_match():
    result = status("value", overview=None, insider=insider())
    assert result.status == "unjudged"
    assert ok_by_id(result)["pe_low"] is None


# --------------------------------------------------------------------- growth


def test_growth_uses_year_over_year_rates_and_trend():
    fast = years((100, 10), (110, 12), (140, 20))  # revenue +27.3%, income +66.7%, accelerating
    result = status("growth", financial_years=fast, chart=chart("up"))
    assert result.status == "match"
    assert ok_by_id(result) == {"revenue_growth": True, "earnings_growth": True, "accelerating": True, "strong_uptrend": True}

    slow = years((100, 10), (105, 10), (108, 10))
    assert status("growth", financial_years=slow, chart=chart("up")).status == "no_match"  # required revenue growth fails


def test_growth_ignores_percentages_off_a_non_positive_base():
    result = status("growth", financial_years=years((100, -5), (130, 4)), chart=chart("up"))
    assert ok_by_id(result)["earnings_growth"] is None  # a profit off a loss is not "growth"
    assert ok_by_id(result)["revenue_growth"] is True and ok_by_id(result)["accelerating"] is None


# -------------------------------------------------------------------- quality


def test_quality_needs_three_years_and_steady_profits():
    steady = years((100, 10), (110, 12), (125, 15))
    assert status("quality", financial_years=steady, chart=chart("up")).status == "match"

    lossy = years((100, 10), (110, -2), (125, 15))
    assert status("quality", financial_years=lossy, chart=chart("up")).status == "no_match"  # required: profitable every year

    short_history = years((100, 10), (110, 12))
    result = status("quality", financial_years=short_history, chart=chart("up"))
    assert result.status == "unjudged"  # two years cannot answer "every year" at all
    assert "needs 3 reported years" in next(r.detail for r in result.results if r.id == "profitable_every_year")


# ---------------------------------------------------------------- short ideas


def test_short_ideas_require_a_downtrend_and_one_fundamental_weakness():
    weak = years((100, 10), (120, 12), (110, 8))
    result = status("short_ideas", chart=chart("down"), financial_years=weak, insider=insider(buys=0, buy_value=0, sells=4, sell_value=2e6))
    assert result.status == "match"
    ok = ok_by_id(result)
    assert ok["revenue_declining"] and ok["growth_slowing"] and ok["margin_compression"] and ok["insider_selling"]

    assert status("short_ideas", chart=chart("up"), financial_years=weak).status == "no_match"
    assert status("short_ideas", chart=None, financial_years=weak).status == "unjudged"


# ----------------------------------------------------------- special situations


def test_special_situations_use_earnings_proximity_and_the_days_move():
    soon = status("special_situations", earnings_date=TODAY + timedelta(days=5), insider=insider(), change_pct_24h=1.0, volume_ratio=1.0)
    assert soon.status == "match" and ok_by_id(soon)["earnings_soon"] and ok_by_id(soon)["insider_buying"]

    far = status("special_situations", earnings_date=TODAY + timedelta(days=60), insider=None, change_pct_24h=-6.0, volume_ratio=3.0)
    assert far.status == "match" and not ok_by_id(far)["earnings_soon"]  # the big move and the volume spike carry it

    assert status("special_situations", earnings_date=None, insider=None, change_pct_24h=0.5, volume_ratio=1.0).status == "no_match"


def test_a_past_earnings_date_is_not_soon():
    assert not ok_by_id(status("special_situations", earnings_date=TODAY - timedelta(days=1)))["earnings_soon"]


def test_every_preset_is_declarative_and_lists_what_it_cannot_answer():
    names = [p.name for p in sp.PRESETS]
    assert names == ["value", "growth", "quality", "short_ideas", "special_situations"]
    for preset in sp.PRESETS:
        assert preset.criteria and preset.unavailable, preset.name  # the source skill's other criteria are named, with reasons
        assert all(u.reason for u in preset.unavailable)
        assert 1 <= preset.min_matches <= len(preset.criteria)
        assert preset.needs and set(preset.needs) <= set(sp.ALL_NEEDS)


# ---------------------------------------------------------------- the run


class ScreenData:
    """Per-symbol fixtures; counts calls so the bound and the fetch-only-what's-needed rule can be asserted."""

    name = "fake"

    def __init__(self, table):
        self.table = table
        self.calls: list[tuple[str, str]] = []

    def get_company_overview(self, symbol):
        self.calls.append((symbol, "overview"))
        entry = self.table[symbol]
        if entry.get("overview") is None:
            raise AllProvidersFailedError("no overview")
        return entry["overview"]

    def get_quote(self, symbol):
        self.calls.append((symbol, "quote"))
        return QuoteData(symbol, self.table[symbol].get("price", 120.0), 0.5, 1e6, 1e6)

    def get_insider_activity(self, symbol):
        self.calls.append((symbol, "insider"))
        return self.table[symbol].get("insider")

    def get_financials(self, symbol):
        self.calls.append((symbol, "financials"))
        return FinancialsData(symbol, self.table[symbol]["years"])

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        self.calls.append((symbol, "ohlcv"))
        raise AssertionError("not needed by the value screen")

    def get_earnings_date(self, symbol):
        self.calls.append((symbol, "earnings"))
        return None


def test_the_run_matches_bounds_itself_and_fetches_only_what_the_preset_needs():
    table = {
        "AAA": {"overview": overview(pe=9.0, low=100, high=200), "price": 110.0},
        "BBB": {"overview": overview(pe=40.0), "price": 190.0},
        "CCC": {"overview": None},  # overview fails: the required P/E cannot be answered
        "DDD": {"overview": overview(pe=8.0)},  # beyond the limit below
    }
    data = ScreenData(table)
    response = run_preset(get_preset("value"), ["AAA", "BBB", "CCC", "DDD"], data, limit=3)

    assert [m.symbol for m in response.matches] == ["AAA"]
    assert response.unjudged == ["CCC"]
    assert response.universe_size == 4 and response.checked == 3 and response.limit == 3
    assert "first 3 of 4" in response.note and "other 1 were not checked" in response.note
    assert not any(symbol == "DDD" for symbol, _ in data.calls)
    kinds = {kind for _, kind in data.calls}
    assert kinds == {"overview", "quote", "insider"}  # no financials, no price history for the value screen
    match = response.matches[0]
    assert match.price == 110.0 and next(c for c in match.criteria if c.id == "pe_low").detail.startswith("P/E 9.0")


def test_the_limit_is_clamped_to_the_maximum():
    data = ScreenData({"A": {"overview": overview()}})
    response = run_preset(get_preset("value"), ["A"], data, limit=10_000)
    assert response.limit == PRESET_MAX_LIMIT and response.checked == 1


# ------------------------------------------------------------------ endpoints


@pytest.fixture
def client():
    data = ScreenData(
        {
            "AAPL": {"overview": overview(pe=9.0), "price": 110.0, "insider": insider()},
            "MSFT": {"overview": overview(pe=50.0), "price": 190.0},
            "NVDA": {"overview": overview(pe=8.0), "price": 130.0},
        }
    )
    app.dependency_overrides[get_data_provider] = lambda: data
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(scan_universe_size=3)
    yield TestClient(app), data
    app.dependency_overrides.clear()


def test_the_preset_list_endpoint_describes_every_preset(client):
    http, _ = client
    body = http.get("/api/scan/presets").json()
    assert [p["name"] for p in body] == ["value", "growth", "quality", "short_ideas", "special_situations"]
    value = body[0]
    assert value["min_matches"] == 2 and any(c["required"] for c in value["criteria"])
    assert value["unavailable"] and "reason" in value["unavailable"][0]


def test_the_run_endpoint_screens_the_effective_watchlist_and_validates_input(client, monkeypatch):
    http, data = client
    monkeypatch.setattr("app.api.routers.screen_presets.get_default_watchlist", lambda n: ["AAPL", "MSFT", "NVDA"][:n])
    body = http.get("/api/scan/presets/value?limit=2").json()
    assert body["preset"]["name"] == "value" and body["checked"] == 2 and body["universe_size"] == 3
    assert [m["symbol"] for m in body["matches"]] == ["AAPL"]  # MSFT fails the required P/E; NVDA was beyond the limit

    assert http.get("/api/scan/presets/nonsense").status_code == 404
    assert http.get("/api/scan/presets/value?limit=0").status_code == 422
    assert http.get(f"/api/scan/presets/value?limit={PRESET_MAX_LIMIT + 1}").status_code == 422
    assert PRESET_DEFAULT_LIMIT <= PRESET_MAX_LIMIT

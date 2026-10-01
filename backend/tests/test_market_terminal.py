"""Market Terminal: sector heatmap, macro panel and recap, on fake provider data.

No network: a fake provider serves synthetic closes per symbol, and the
universe is monkeypatched to a handful of entries."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_data_provider, get_llm_provider
from app.data_providers.base import AllProvidersFailedError, CompanyOverview
from app.data_providers.universe import UniverseEntry
from app.llm_providers.base import LLMResult
from app.main import app
from app.services import market_terminal_service as svc


def _frame(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-09-30", periods=len(closes))
    return pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes, "close": closes, "volume": 1_000})


def _ramp(last: float, prev: float, n: int = 260) -> list[float]:
    """n closes: flat at `prev`, then `last` on the final bar."""
    return [prev] * (n - 1) + [last]


class FakeProvider:
    name = "fake"

    def __init__(self, closes: dict[str, list[float]], caps: dict[str, float] | None = None):
        self.closes = closes
        self.caps = caps or {}
        self.ohlcv_calls: list[str] = []

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        self.ohlcv_calls.append(symbol)
        if symbol not in self.closes:
            raise AllProvidersFailedError(f"no data for {symbol}")
        return _frame(self.closes[symbol])

    def get_company_overview(self, symbol):
        if symbol not in self.caps:
            raise AllProvidersFailedError("no overview")
        return CompanyOverview(symbol, symbol, self.caps[symbol], None, None, None, None, None)


UNIVERSE = [
    UniverseEntry("AAA", "Alpha", "Technology"),
    UniverseEntry("BBB", "Beta", "Technology"),
    UniverseEntry("CCC", "Gamma", "Energy"),
    UniverseEntry("DDD", "Delta", "Energy"),
    UniverseEntry("EEE", "Epsilon", "Utilities"),
]


@pytest.fixture(autouse=True)
def _universe(monkeypatch):
    monkeypatch.setattr(svc, "load_universe", lambda: list(UNIVERSE))


def _provider() -> FakeProvider:
    return FakeProvider(
        {
            "AAA": _ramp(110, 100),  # +10%
            "BBB": _ramp(95, 100),  # -5%
            "CCC": _ramp(101, 100),  # +1%
            "DDD": _ramp(100, 100),  # flat
            # EEE: no data
            "XLK": _ramp(102, 100),
            "XLE": _ramp(99, 100),
        },
        caps={"AAA": 3e12, "BBB": 1e12, "CCC": 2e11},
    )


def test_pct_change_windows_and_missing_history():
    closes = [100.0] * 20 + [110.0, 121.0]
    assert svc.pct_change(closes, 1) == pytest.approx(10.0)
    assert svc.pct_change(closes, 2) == pytest.approx(21.0)
    assert svc.pct_change(closes, 5) == pytest.approx(21.0)
    assert svc.pct_change(closes, 21) == pytest.approx(21.0)
    assert svc.pct_change([100.0, 101.0], 5) is None
    assert svc.pct_change([0.0, 1.0], 1) is None


def test_heatmap_groups_by_sector_and_weights_by_market_cap():
    result = svc.get_heatmap(_provider(), "1d", 10)
    sectors = {s.sector: s for s in result.sectors}
    assert set(sectors) == {"Technology", "Energy"}  # Utilities: no data
    assert result.weighting == "market_cap"
    tech = sectors["Technology"]
    assert [t.symbol for t in tech.tiles] == ["AAA", "BBB"]  # biggest first
    assert tech.avg_change_pct == pytest.approx(2.5)
    assert tech.tiles[0].weight == 3e12
    # DDD has no cap: it gets the median of the known caps, not zero.
    energy = {t.symbol: t for t in sectors["Energy"].tiles}
    assert energy["DDD"].market_cap is None
    assert energy["DDD"].weight == pytest.approx(1e12)
    assert result.missing == ["EEE"]
    assert result.sampled == 5 and result.universe_size == 5


def test_heatmap_equal_weights_when_caps_mostly_unknown():
    provider = _provider()
    provider.caps = {"AAA": 3e12}
    result = svc.get_heatmap(provider, "1d", 10)
    assert result.weighting == "equal"
    assert all(t.weight == 1.0 for s in result.sectors for t in s.tiles)


def test_heatmap_window_and_sample_limit():
    aaa = [100.0] * 200 + [100.0, 100.0, 100.0, 100.0, 100.0, 143.0]  # +43% over 5 bars
    provider = FakeProvider({"AAA": aaa, "BBB": _ramp(95, 100)})
    five_day = svc.get_heatmap(provider, "5d", 2)
    tile = five_day.sectors[0].tiles[0]
    assert tile.symbol == "AAA" and tile.change_pct == pytest.approx(43.0)
    svc.reset_terminal_cache()
    provider.ohlcv_calls.clear()
    limited = svc.get_heatmap(provider, "1d", 1)
    assert limited.sampled == 1 and limited.universe_size == 5 and limited.requested_limit == 1
    assert "BBB" not in provider.ohlcv_calls  # only the first symbol was read


def test_heatmap_etf_row_marks_unavailable_etfs():
    result = svc.get_heatmap(_provider(), "1d", 10)
    etfs = {e.symbol: e.change_pct for e in result.sector_etfs}
    assert len(etfs) == 11
    assert etfs["XLK"] == pytest.approx(2.0)
    assert etfs["XLE"] == pytest.approx(-1.0)
    assert etfs["XLU"] is None


def test_results_are_cached_between_calls():
    provider = _provider()
    svc.get_heatmap(provider, "1d", 10)
    calls = len(provider.ohlcv_calls)
    svc.get_heatmap(provider, "1d", 10)
    assert len(provider.ohlcv_calls) == calls


def test_breadth_highs_lows_and_movers():
    up_high = [100.0] * 258 + [101.0, 120.0]  # closes at its 52-week high
    down_low = [100.0] * 258 + [99.0, 80.0]  # closes at its 52-week low
    provider = FakeProvider({"AAA": up_high, "BBB": down_low, "CCC": _ramp(100.01, 100.0), "DDD": _ramp(100, 100)})
    moves, missing, _ = svc.load_moves(provider, 10, want_cap=False)
    breadth = svc.compute_breadth(moves)
    assert (breadth.advancers, breadth.decliners, breadth.unchanged) == (1, 1, 2)
    # A flat series closes at both its high and its low, so only the strict
    # movers are asserted exactly.
    assert breadth.new_highs >= 1 and breadth.new_lows >= 1
    assert breadth.total == 4 and missing == ["EEE"]
    up, down = svc.top_movers(moves)
    assert up[0].symbol == "AAA" and [m.symbol for m in down] == ["BBB"]


def test_short_history_is_not_judged_for_highs():
    assert svc.extremes_flags([100.0] * 50) == (None, None)


def test_yield_curve_spread_and_inversion():
    def tile(tid, value):
        return svc.MacroTile(id=tid, label=tid, group="yield", symbol=tid, unit="percent", available=value is not None, value=value)

    normal = svc.build_yield_curve({"y3m": tile("y3m", 4.0), "y10y": tile("y10y", 4.5)})
    assert normal.spread_10y_3m == pytest.approx(0.5) and normal.inverted is False
    inverted = svc.build_yield_curve({"y3m": tile("y3m", 5.0), "y10y": tile("y10y", 4.5)})
    assert inverted.spread_10y_3m == pytest.approx(-0.5) and inverted.inverted is True
    unknown = svc.build_yield_curve({"y3m": tile("y3m", None), "y10y": tile("y10y", 4.5)})
    assert unknown.spread_10y_3m is None and unknown.inverted is None
    assert next(p for p in unknown.points if p.label == "2Y").value is None  # never approximated


def test_vix_regime_uses_the_scoring_threshold():
    assert svc.vix_regime(24.9) == "calm"
    assert svc.vix_regime(25.0) == "elevated"
    assert svc.vix_regime(None) is None


def test_macro_panel_marks_missing_series_unavailable():
    provider = FakeProvider({"^GSPC": [100.0, 101.0, 102.0], "^VIX": [20.0, 30.0], "^IRX": [4.0, 4.0], "^TNX": [4.4, 4.5]})
    macro = svc.get_macro(provider)
    by_id = {t.id: t for t in macro.tiles}
    assert by_id["spx"].available and by_id["spx"].value == 102.0
    assert by_id["spx"].change_pct == pytest.approx(100 * 1 / 101, rel=1e-3)
    assert by_id["spx"].sparkline == [100.0, 101.0, 102.0] and by_id["spx"].as_of == "2026-09-30"
    assert not by_id["ndx"].available and by_id["ndx"].value is None and by_id["ndx"].sparkline == []
    assert macro.vix_value == 30.0 and macro.vix_regime == "elevated"
    assert macro.yield_curve.spread_10y_3m == pytest.approx(0.5)


class SpyLLM:
    name = "spy"

    def __init__(self, text="AI recap text."):
        self.calls: list[str] = []
        self.text = text

    def is_configured(self):
        return True

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return LLMResult(text=self.text, provider="spy", latency_ms=1)


class NoLLM:
    name = "none"

    def is_configured(self):
        return False

    def generate(self, prompt, **kwargs):
        raise AssertionError("must not be called")


def test_recap_is_rule_based_without_llm_and_never_calls_it():
    recap = svc.get_recap(_provider(), NoLLM(), ai=True, limit=10)
    assert recap.ai_paragraph is None and recap.ai_provider is None
    assert recap.breadth.advancers == 2 and recap.breadth.decliners == 1
    assert recap.top_gainers[0].symbol == "AAA" and recap.top_losers[0].symbol == "BBB"
    assert recap.sector_leaders[0].sector == "Technology"
    assert "Top gainer: AAA" in recap.summary


def test_recap_ai_paragraph_only_when_requested_and_configured():
    llm = SpyLLM()
    provider = _provider()
    plain = svc.get_recap(provider, llm, ai=False, limit=10)
    assert plain.ai_paragraph is None and llm.calls == []
    with_ai = svc.get_recap(provider, llm, ai=True, limit=10)
    assert with_ai.ai_paragraph == "AI recap text." and with_ai.ai_provider == "spy"
    assert len(llm.calls) == 1
    assert "Do not add any number" in llm.calls[0] and "AAA" in llm.calls[0]
    svc.get_recap(provider, llm, ai=True, limit=10)  # cached: no second call
    assert len(llm.calls) == 1


def test_failed_ai_call_is_not_cached():
    provider = _provider()
    assert svc.get_recap(provider, SpyLLM(text=""), ai=True, limit=10).ai_paragraph is None
    assert svc.get_recap(provider, SpyLLM(), ai=True, limit=10).ai_paragraph == "AI recap text."


def test_recap_with_no_data_says_so():
    recap = svc.get_recap(FakeProvider({}), NoLLM(), limit=10)
    assert recap.breadth.total == 0
    assert "No price data" in recap.summary


def test_endpoints_are_read_only_and_shaped():
    provider = _provider()
    app.dependency_overrides[get_data_provider] = lambda: provider
    app.dependency_overrides[get_llm_provider] = lambda: NoLLM()
    try:
        client = TestClient(app)
        heat = client.get("/api/terminal/heatmap?window=5d&limit=10")
        assert heat.status_code == 200
        body = heat.json()
        assert body["window"] == "5d" and body["as_of"].endswith("Z")
        assert client.get("/api/terminal/heatmap?window=2y").status_code == 422
        assert client.get("/api/terminal/heatmap?limit=0").status_code == 422
        assert client.get("/api/terminal/macro").status_code == 200
        assert client.get("/api/terminal/recap?ai=true").json()["ai_paragraph"] is None
        for path in ("heatmap", "macro", "recap"):
            assert client.post(f"/api/terminal/{path}").status_code == 405
    finally:
        app.dependency_overrides.clear()

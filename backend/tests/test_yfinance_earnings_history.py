"""YFinanceProvider.get_earnings_history — no network.

The one thing this needs to get right: yfinance's get_earnings_dates() frame
mixes past (reported) and future (upcoming) rows in one call, distinguished
only by whether "Reported EPS" is NaN — there's no separate "is this past"
flag to key off, so a subtle filter mistake here would either leak an
upcoming estimate into "history" or silently drop real history.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data_providers.cache import clear_cache
from app.data_providers.yfinance_provider import YFinanceProvider


@pytest.fixture(autouse=True)
def _isolate_cache():
    clear_cache()
    yield
    clear_cache()


def _fake_dates_df(rows: list[dict]) -> pd.DataFrame:
    index = pd.DatetimeIndex([r["ts"] for r in rows])
    return pd.DataFrame(
        {
            "EPS Estimate": [r.get("est") for r in rows],
            "Reported EPS": [r.get("actual") for r in rows],
            "Surprise(%)": [r.get("surprise") for r in rows],
        },
        index=index,
    )


def _patch_ticker(monkeypatch, df: pd.DataFrame):
    class FakeTicker:
        def __init__(self, symbol):
            pass

        def get_earnings_dates(self, limit):
            return df

    monkeypatch.setattr("app.data_providers.yfinance_provider.yf.Ticker", FakeTicker)


def test_only_reported_quarters_are_returned_as_history(monkeypatch):
    df = _fake_dates_df(
        [
            {"ts": "2026-10-29", "est": 1.98, "actual": None, "surprise": None},  # upcoming
            {"ts": "2026-07-30", "est": 1.89, "actual": 2.02, "surprise": 6.74},  # reported
            {"ts": "2026-04-30", "est": 1.94, "actual": 2.01, "surprise": 3.46},  # reported
        ]
    )
    _patch_ticker(monkeypatch, df)

    history = YFinanceProvider().get_earnings_history("AAPL", limit=8)

    assert len(history) == 2
    assert all(e.eps_actual is not None for e in history)


def test_history_is_sorted_most_recent_first(monkeypatch):
    df = _fake_dates_df(
        [
            {"ts": "2026-01-29", "est": 2.67, "actual": 2.84, "surprise": 6.25},
            {"ts": "2026-07-30", "est": 1.89, "actual": 2.02, "surprise": 6.74},
            {"ts": "2025-10-30", "est": 1.77, "actual": 1.85, "surprise": 4.52},
        ]
    )
    _patch_ticker(monkeypatch, df)

    history = YFinanceProvider().get_earnings_history("AAPL", limit=8)

    dates = [e.date.isoformat() for e in history]
    assert dates == sorted(dates, reverse=True)


def test_respects_the_limit_after_filtering_to_reported_quarters(monkeypatch):
    rows = [{"ts": f"2020-{m:02d}-01", "est": 1.0, "actual": 1.0, "surprise": 0.0} for m in range(1, 13)]
    df = _fake_dates_df(rows)
    _patch_ticker(monkeypatch, df)

    history = YFinanceProvider().get_earnings_history("AAPL", limit=3)

    assert len(history) == 3


def test_missing_estimate_or_surprise_becomes_none_not_zero(monkeypatch):
    df = _fake_dates_df([{"ts": "2026-01-29", "est": None, "actual": 2.84, "surprise": None}])
    _patch_ticker(monkeypatch, df)

    history = YFinanceProvider().get_earnings_history("AAPL", limit=8)

    assert history[0].eps_estimate is None
    assert history[0].surprise_pct is None
    assert history[0].eps_actual == pytest.approx(2.84)


def test_empty_frame_returns_empty_list(monkeypatch):
    _patch_ticker(monkeypatch, pd.DataFrame())
    assert YFinanceProvider().get_earnings_history("AAPL") == []


def test_ticker_failure_returns_empty_list_not_an_exception(monkeypatch):
    class ExplodingTicker:
        def __init__(self, symbol):
            raise RuntimeError("network down")

    monkeypatch.setattr("app.data_providers.yfinance_provider.yf.Ticker", ExplodingTicker)
    assert YFinanceProvider().get_earnings_history("AAPL") == []

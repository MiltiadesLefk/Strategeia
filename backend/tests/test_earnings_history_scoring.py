"""Earnings surprise track record and historical earnings-day move.

Both are backward-looking fact used forward: a beat/miss pattern is real
history, not a forecast of the next print, and the historical move % is
computed purely from past OHLCV with no guessing about the future at all.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.analysis.earnings_history_scoring import (
    CONSISTENCY_THRESHOLD,
    MIN_QUARTERS_FOR_TRACK_RECORD,
    SURPRISE_TRACK_RECORD_CAP,
    historical_earnings_move_pct,
    score_earnings_surprise_track_record,
)
from app.data_providers.base import EarningsHistoryEntry


def _entry(surprise_pct: float | None, days_ago: int = 90) -> EarningsHistoryEntry:
    return EarningsHistoryEntry(
        date=date.today() - timedelta(days=days_ago),
        eps_estimate=1.0,
        eps_actual=1.0 + (surprise_pct or 0) / 100,
        surprise_pct=surprise_pct,
    )


# --------------------------------------------------------- surprise track record


def test_consistent_beats_support_a_long():
    history = [_entry(5.0), _entry(4.0), _entry(6.0), _entry(3.0)]
    score, reasons = score_earnings_surprise_track_record("long", history)
    assert score == SURPRISE_TRACK_RECORD_CAP
    assert "beat consensus" in reasons[0]


def test_consistent_beats_contradict_a_short():
    history = [_entry(5.0), _entry(4.0), _entry(6.0), _entry(3.0)]
    score, _ = score_earnings_surprise_track_record("short", history)
    assert score == -SURPRISE_TRACK_RECORD_CAP


def test_consistent_misses_support_a_short():
    history = [_entry(-5.0), _entry(-4.0), _entry(-6.0), _entry(-3.0)]
    score, reasons = score_earnings_surprise_track_record("short", history)
    assert score == SURPRISE_TRACK_RECORD_CAP
    assert "missed consensus" in reasons[0]


def test_mixed_record_contributes_nothing():
    """Not consistent enough to be a pattern rather than noise."""
    history = [_entry(5.0), _entry(-4.0), _entry(3.0), _entry(-2.0)]
    assert score_earnings_surprise_track_record("long", history) == (0, [])


def test_below_minimum_sample_contributes_nothing():
    history = [_entry(5.0)] * (MIN_QUARTERS_FOR_TRACK_RECORD - 1)
    assert score_earnings_surprise_track_record("long", history) == (0, [])


def test_meets_minimum_sample_with_full_consistency():
    history = [_entry(5.0)] * MIN_QUARTERS_FOR_TRACK_RECORD
    score, _ = score_earnings_surprise_track_record("long", history)
    assert score == SURPRISE_TRACK_RECORD_CAP


def test_tiny_surprises_do_not_count_as_beats_or_misses():
    """A $2.00 print against a $1.99 estimate is not a meaningful beat."""
    history = [_entry(0.1), _entry(0.2), _entry(-0.1), _entry(0.05)]
    assert score_earnings_surprise_track_record("long", history) == (0, [])


def test_missing_surprise_data_is_excluded_from_the_sample():
    history = [_entry(5.0), _entry(None), _entry(6.0), _entry(4.0), _entry(3.0)]
    # 4 usable quarters, all beats -> still scores despite one gap
    score, reasons = score_earnings_surprise_track_record("long", history)
    assert score == SURPRISE_TRACK_RECORD_CAP
    assert "4/4" in reasons[0]


def test_no_direction_contributes_nothing():
    history = [_entry(5.0)] * MIN_QUARTERS_FOR_TRACK_RECORD
    assert score_earnings_surprise_track_record(None, history) == (0, [])


def test_empty_history_contributes_nothing():
    assert score_earnings_surprise_track_record("long", []) == (0, [])


def test_consistency_threshold_boundary():
    """Exactly at CONSISTENCY_THRESHOLD should count; just under should not."""
    n = 8
    beats_needed = int(n * CONSISTENCY_THRESHOLD)
    exactly_at = [_entry(5.0)] * beats_needed + [_entry(-5.0)] * (n - beats_needed)
    score, _ = score_earnings_surprise_track_record("long", exactly_at)
    assert score == SURPRISE_TRACK_RECORD_CAP

    one_fewer = [_entry(5.0)] * (beats_needed - 1) + [_entry(-5.0)] * (n - beats_needed + 1)
    score2, _ = score_earnings_surprise_track_record("long", one_fewer)
    assert score2 == 0


# --------------------------------------------------------- historical move %


def _ohlcv(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d, tz="America/New_York"), "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
         for d, c in rows]
    )


def test_historical_move_reads_the_bar_after_a_past_report_date():
    ohlcv = _ohlcv([("2026-01-05", 100.0), ("2026-01-06", 100.0), ("2026-01-07", 110.0), ("2026-01-08", 111.0)])
    history = [EarningsHistoryEntry(date=date(2026, 1, 6), eps_estimate=1.0, eps_actual=1.0, surprise_pct=0.0)]
    result = historical_earnings_move_pct(ohlcv, history)
    assert result == pytest.approx(10.0)  # 100 -> 110, the bar after the report


def test_historical_move_is_the_median_across_events():
    ohlcv = _ohlcv(
        [
            ("2026-01-05", 100.0), ("2026-01-06", 100.0), ("2026-01-07", 104.0),
            ("2026-04-05", 100.0), ("2026-04-06", 100.0), ("2026-04-07", 112.0),
            ("2026-07-05", 100.0), ("2026-07-06", 100.0), ("2026-07-07", 108.0),
        ]
    )
    history = [
        EarningsHistoryEntry(date=date(2026, 1, 6), eps_estimate=1.0, eps_actual=1.0, surprise_pct=0.0),
        EarningsHistoryEntry(date=date(2026, 4, 6), eps_estimate=1.0, eps_actual=1.0, surprise_pct=0.0),
        EarningsHistoryEntry(date=date(2026, 7, 6), eps_estimate=1.0, eps_actual=1.0, surprise_pct=0.0),
    ]
    assert historical_earnings_move_pct(ohlcv, history) == pytest.approx(8.0)  # median of 4%, 12%, 8%


def test_historical_move_handles_before_open_and_after_close_reporters_alike():
    """A report AT market open on day N and one AFTER close on day N-1 both
    resolve to 'the first bar strictly after the report date' being the
    reaction bar, without needing to know which kind of reporter this is."""
    ohlcv = _ohlcv([("2026-01-05", 100.0), ("2026-01-06", 100.0), ("2026-01-07", 105.0)])
    history = [EarningsHistoryEntry(date=date(2026, 1, 6), eps_estimate=1.0, eps_actual=1.0, surprise_pct=0.0)]
    assert historical_earnings_move_pct(ohlcv, history) == pytest.approx(5.0)


def test_historical_move_skips_events_with_no_bar_data_nearby():
    ohlcv = _ohlcv([("2026-01-05", 100.0), ("2026-01-06", 100.0)])
    history = [EarningsHistoryEntry(date=date(2020, 1, 1), eps_estimate=1.0, eps_actual=1.0, surprise_pct=0.0)]
    assert historical_earnings_move_pct(ohlcv, history) is None


def test_historical_move_none_for_empty_inputs():
    assert historical_earnings_move_pct(pd.DataFrame(), []) is None
    assert historical_earnings_move_pct(_ohlcv([("2026-01-05", 100.0)]), []) is None

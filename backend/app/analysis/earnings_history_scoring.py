from __future__ import annotations

from datetime import timedelta

import pandas as pd

from app.data_providers.base import EarningsHistoryEntry

# Same shape as fundamental_scoring's revenue-growth check: a real historical
# pattern, direction-aware, small and capped. Requires a minimum sample —
# fewer than this many reported quarters is too small a sample for "beats
# consistently" to mean anything rather than a coin-flip streak.
SURPRISE_TRACK_RECORD_CAP = 1
MIN_QUARTERS_FOR_TRACK_RECORD = 4
# Consensus beats/misses by less than this are noise — a company reporting
# $2.00 against a $1.99 estimate has not meaningfully beaten anything.
MEANINGFUL_SURPRISE_PCT = 1.0
# Fraction of sampled quarters that must agree for the pattern to count as
# "consistent" rather than a mixed bag.
CONSISTENCY_THRESHOLD = 0.75

# How many trading days after a report date to look for the market's
# reaction. Most US companies report either before the open or after the
# close, so the NEXT bar (not the report-date bar itself, which may still be
# mid-reaction intraday on a same-day report) is the cleanest single-bar read
# of "how much did this actually move."
REACTION_LOOKAHEAD_DAYS = 3


def score_earnings_surprise_track_record(
    direction: str | None, history: list[EarningsHistoryEntry]
) -> tuple[int, list[str]]:
    """Scores a consistent history of beating (or missing) consensus EPS —
    real, reported fact, not a forecast of the next print. A company that has
    beaten estimates in most of its recent quarters is a different quality of
    business than one that hasn't, independent of any specific upcoming date;
    this is scored generally, the same way revenue growth is, not only when a
    report is imminent.

    Requires MIN_QUARTERS_FOR_TRACK_RECORD real (surprise_pct not None)
    quarters and CONSISTENCY_THRESHOLD agreement among them — a single beat
    or a small sample contributes 0 rather than being read as a pattern.
    """
    if direction is None:
        return 0, []

    usable = [e for e in history if e.surprise_pct is not None]
    if len(usable) < MIN_QUARTERS_FOR_TRACK_RECORD:
        return 0, []

    beats = sum(1 for e in usable if e.surprise_pct >= MEANINGFUL_SURPRISE_PCT)
    misses = sum(1 for e in usable if e.surprise_pct <= -MEANINGFUL_SURPRISE_PCT)

    sign = -1 if direction == "short" else 1
    if beats / len(usable) >= CONSISTENCY_THRESHOLD:
        avg = sum(e.surprise_pct for e in usable) / len(usable)
        return sign * SURPRISE_TRACK_RECORD_CAP, [
            f"beat consensus EPS in {beats}/{len(usable)} of the last reported quarters (avg +{avg:.1f}%)"
        ]
    if misses / len(usable) >= CONSISTENCY_THRESHOLD:
        avg = sum(e.surprise_pct for e in usable) / len(usable)
        return -sign * SURPRISE_TRACK_RECORD_CAP, [
            f"missed consensus EPS in {misses}/{len(usable)} of the last reported quarters (avg {avg:.1f}%)"
        ]
    return 0, []


def historical_earnings_move_pct(ohlcv: pd.DataFrame, history: list[EarningsHistoryEntry]) -> float | None:
    """Median absolute % move this stock has actually made around its last
    reported earnings dates, from real OHLCV — no forward guessing involved
    at all. Feeds the same role compute_expected_move_pct does (a magnitude
    to size a stop against) but from realized history instead of options
    pricing, so the two can be shown side by side: "options imply a 6% move;
    this name has actually moved a median 4.5% around its last 8 reports."

    For each past date, compares the close on the LAST trading day at or
    before it to the close on the first trading day within
    REACTION_LOOKAHEAD_DAYS after it — covering both before-open and
    after-close reporters without needing to know which this company is.
    """
    if ohlcv.empty or not history:
        return None

    # yfinance's OHLCV index is exchange-tz-aware; `entry.date` is a naive
    # date. Converting through UTC and dropping the offset (same fix already
    # proven in PaperTradingEngine._bars_after_entry) makes both comparable —
    # a bare comparison here raises TypeError on tz-aware vs naive.
    dates = pd.to_datetime(ohlcv["date"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    closes = ohlcv["close"].to_numpy()
    moves: list[float] = []

    for entry in history:
        report_at = pd.Timestamp(entry.date)
        before_mask = dates <= report_at
        if not before_mask.any():
            continue
        before_idx = int(before_mask.to_numpy().nonzero()[0][-1])

        after_mask = (dates > report_at) & (dates <= report_at + timedelta(days=REACTION_LOOKAHEAD_DAYS))
        if not after_mask.any():
            continue
        after_idx = int(after_mask.to_numpy().nonzero()[0][0])

        before_close = closes[before_idx]
        after_close = closes[after_idx]
        if before_close <= 0:
            continue
        moves.append(abs(after_close - before_close) / before_close * 100)

    if not moves:
        return None
    moves.sort()
    mid = len(moves) // 2
    return moves[mid] if len(moves) % 2 else (moves[mid - 1] + moves[mid]) / 2

"""The missed-trades ledger: classification of the plans that were not executed (the simulator is retired).

Every price series here is hand-built so each number can be checked by hand, and
nothing touches the network or the real database. The headline fixture is a
perfectly steady uptrend (close = 100 + t, high/low half a point either side): it
has no pivots, so the stop falls back to 3% under the entry and the target to 1.5R,
which makes the rebuilt trade easy to compute by hand:

    long   entry 199.00, stop 199 x 0.97 = 193.03, risk 5.97, TP1 199 + 1.5 x 5.97 = 207.955
    short  entry 201.00, stop 201 x 1.03 = 207.03, risk 6.03, TP1 201 - 1.5 x 6.03 = 191.955
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.markets import is_us_trading_day
from app.portfolio.missed_trades import (
    CATEGORY_AI_VETO,
    CATEGORY_HELD_BY_AI,
    CATEGORY_LOW_CONFIDENCE,
    CATEGORY_NEUTRAL_TREND,
    CATEGORY_NOT_EXECUTED,
    CATEGORY_UNCLASSIFIED,
    DETAIL_AWAITING_MANUAL,
    DETAIL_DUPLICATE_POSITION,
    DETAIL_INSUFFICIENT_CASH,
    DETAIL_MARKET_CLOSED_REDO,
    DETAIL_OTHER_REFUSAL,
    DETAIL_POSITION_CAP,
    DETAIL_SECTOR_CAP,
    DETAIL_STALE_PRICE,
    classify_plan,
)
from app.portfolio.models import TradePlanRecord

# ------------------------------------------------------------------ fixtures

# Zero slippage and commission unless a test says otherwise, so R is exactly the
# hand-computed price ratio.

TREND_BARS = 100  # history length: the decision is taken right after bar index 99
LONG_ENTRY, LONG_STOP, LONG_TP1, LONG_RISK = 199.0, 193.03, 207.955, 5.97
SHORT_ENTRY, SHORT_STOP, SHORT_TP1, SHORT_RISK = 201.0, 207.03, 191.955, 6.03

LONG_REASON = "Confidence too low (25%, needs 30%+) despite a bullish trend."
SHORT_REASON = "Confidence too low (19%, needs 30%+) despite a bearish trend."


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def trading_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if is_us_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


ALL_DAYS = trading_days(date(2025, 3, 3), TREND_BARS + 40)
HISTORY_DAYS = ALL_DAYS[:TREND_BARS]
LAST_HISTORY_DAY = HISTORY_DAYS[-1]
# After the close and the 30-minute settle (17:00/18:00 ET): that day's bar is final.
DECISION_AFTER_CLOSE = datetime.combine(LAST_HISTORY_DAY, time(22, 0))
# 11:00 ET on the same day: its bar is still forming.
DECISION_INTRADAY = datetime.combine(LAST_HISTORY_DAY, time(15, 0))
# Long after every bar used below.
NOW = datetime.combine(ALL_DAYS[-1] + timedelta(days=3), time(12, 0))


def trend_frame(sign: int, start: float) -> pd.DataFrame:
    t = np.arange(TREND_BARS)
    closes = (start + sign * t).astype(float)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(HISTORY_DAYS),
            "open": closes,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": 1e6,
        }
    )


def future_frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Bars after the history, one per trading day: (open, high, low, close)."""
    days = ALL_DAYS[TREND_BARS : TREND_BARS + len(rows)]
    return pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": 1e6,
        }
    )


def long_bars(future: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.concat([trend_frame(1, 100.0), future_frame(future)], ignore_index=True)


def short_bars(future: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.concat([trend_frame(-1, 300.0), future_frame(future)], ignore_index=True)


def no_trade_plan(session: Session, reason: str = LONG_REASON, *, created_at: datetime = DECISION_AFTER_CLOSE, **kw) -> TradePlanRecord:
    values = dict(symbol="TEST", status="no_trade", reason=reason, confidence_score=25, created_at=created_at)
    values.update(kw)
    plan = TradePlanRecord(**values)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


# ------------------------------------------------------------ classification

REAL_VETO_PASS = "AI Trading Overlay would not take this trade at 80% conviction — no trade taken against a rule-based long."
REAL_VETO_STANCE = (
    "AI Trading Overlay reads this bearish at 78% conviction against a rule-based long — no trade taken on a flat disagreement."
)
REAL_NO_TREND = "No clear trend (EMA20/EMA50 not aligned) — not enough information to size a trade."


@pytest.mark.parametrize(
    ("status", "reason", "note", "category", "detail", "simulate"),
    [
        ("no_trade", REAL_NO_TREND, None, CATEGORY_NEUTRAL_TREND, None, False),
        ("no_trade", REAL_VETO_PASS, None, CATEGORY_AI_VETO, None, True),
        ("no_trade", REAL_VETO_STANCE, None, CATEGORY_AI_VETO, None, True),
        ("no_trade", LONG_REASON, None, CATEGORY_LOW_CONFIDENCE, None, True),
        ("no_trade", "something a later version writes", None, CATEGORY_UNCLASSIFIED, None, False),
        (
            "pending", None,
            "Auto-execute held: AI Trading Overlay would not take this trade (62% confidence) against a rule-based long — left pending for manual review.",
            CATEGORY_HELD_BY_AI, None, True,
        ),
        (
            "pending", None,
            "Auto-execute held: AI Trading Overlay called this bearish against a rule-based long — left pending for manual review.",
            CATEGORY_HELD_BY_AI, None, True,
        ),
        (
            "pending", None, "Auto-execute skipped: AAPL already has an open position (id=3)",
            CATEGORY_NOT_EXECUTED, DETAIL_DUPLICATE_POSITION, False,
        ),
        (
            "pending", None,
            "Auto-execute skipped: Already at the 5-position cap (Settings -> Max Concurrent Positions) — close a position before opening another.",
            CATEGORY_NOT_EXECUTED, DETAIL_POSITION_CAP, True,
        ),
        (
            "pending", None,
            "Auto-execute skipped: Account can't afford 1 share of AAPL at $547.00 (available cash: $1.00). Increase paper starting cash or risk % in Settings.",
            CATEGORY_NOT_EXECUTED, DETAIL_INSUFFICIENT_CASH, True,
        ),
        (
            "pending", None,
            "Auto-execute skipped: AAPL has moved 9.7% (plan $547.00 vs market $600.00), past the 2% limit — regenerate the trade plan so the stop, size and R:R match the current price.",
            CATEGORY_NOT_EXECUTED, DETAIL_STALE_PRICE, True,
        ),
        (
            "pending", None,
            "Auto-execute skipped: Already holding 2 Information Technology position(s) (AAPL, MSFT) — at the 2-per-sector cap (Settings -> Max Positions Per Sector). Concentrated positions in one sector are one correlated bet, not independent risk.",
            CATEGORY_NOT_EXECUTED, DETAIL_SECTOR_CAP, True,
        ),
        ("pending", None, "Auto-execute skipped: some new refusal", CATEGORY_NOT_EXECUTED, DETAIL_OTHER_REFUSAL, True),
        (
            "pending", None,
            "Market closed (weekend), so this plan was not executed. It will be redone from fresh data at the next open, Mon 28 Sep 09:45 ET, and only the fresh plan can execute.",
            CATEGORY_NOT_EXECUTED, DETAIL_MARKET_CLOSED_REDO, False,
        ),
        ("pending", None, None, CATEGORY_NOT_EXECUTED, DETAIL_AWAITING_MANUAL, True),
        ("pending", None, "", CATEGORY_NOT_EXECUTED, DETAIL_AWAITING_MANUAL, True),
        ("pending", None, "an unrecognised note", CATEGORY_UNCLASSIFIED, None, False),
    ],
)
def test_classification_of_every_string_the_live_code_writes(status, reason, note, category, detail, simulate):
    plan = TradePlanRecord(symbol="X", status=status, reason=reason, auto_execute_note=note, confidence_score=10)
    result = classify_plan(plan)
    assert result is not None
    assert (result.category, result.detail, result.simulate) == (category, detail, simulate)


@pytest.mark.parametrize("status", ["executed", "discarded"])
def test_executed_and_superseded_plans_are_not_missed_trades(status):
    assert classify_plan(TradePlanRecord(symbol="X", status=status, reason="Confidence too low (1%)", confidence_score=1)) is None


# The strings above were copied from the live code. These run the real generator, so a
# reworded message fails here instead of letting plans drift into "unclassified".

AAPL, MSFT = "AAPL", "MSFT"  # both Information Technology in the bundled universe



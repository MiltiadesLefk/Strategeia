"""The missed-trades ledger: classification, the rebuilt trade, the exit walk, caching and refresh.

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
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import AppSettings
from app.markets import is_us_trading_day
from app.portfolio import missed_trades
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.missed_trade_models import (
    OUTCOME_NO_DATA,
    OUTCOME_NOT_SIMULATABLE,
    OUTCOME_OPEN,
    OUTCOME_RESOLVED,
    MissedTradeOutcome,
)
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
    refresh_missed_trades,
    simulate_missed_trade,
)
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.llm_providers.null_provider import NullLLMProvider
from app.services import trade_plan_service
from app.data_providers.base import QuoteData
from test_trade_plan_service import (
    FakeConfiguredLLMProvider,
    FakeFlatDataProvider,
    FakeUptrendDataProvider,
    _default_settings,
)

# ------------------------------------------------------------------ fixtures

# Zero slippage and commission unless a test says otherwise, so R is exactly the
# hand-computed price ratio.
CLEAN = AppSettings(slippage_bps=0.0, commission_per_trade=0.0, max_holding_days=20)

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


def run(plan: TradePlanRecord, bars: pd.DataFrame, settings: AppSettings = CLEAN, now: datetime = NOW):
    classification = classify_plan(plan)
    assert classification is not None and classification.simulate
    return simulate_missed_trade(plan, classification, bars, settings, now)


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


def _generate(session, provider, settings, llm=None, *, symbol=AAPL, account=100_000.0, clock=None):
    response = trade_plan_service.generate_trade_plan(
        symbol, account, 1.0, provider, llm or NullLLMProvider(), session, settings=settings, clock=clock
    )
    return session.get(TradePlanRecord, response.id)


def _classified(session, provider, settings, llm=None, **kw):
    plan = _generate(session, provider, settings, llm, **kw)
    result = classify_plan(plan)
    assert result is not None, (plan.status, plan.reason, plan.auto_execute_note)
    return result


def test_real_neutral_trend_decision(session):
    result = _classified(session, FakeFlatDataProvider(), _default_settings(ai_trading_overlay_enabled=False))
    assert (result.category, result.simulate) == (CATEGORY_NEUTRAL_TREND, False)


def test_real_low_confidence_decision(session):
    class WeakBullish(FakeFlatDataProvider):
        def get_ohlcv(self, symbol, period="6mo", interval="1d"):
            closes = [100.0 + i * 0.05 for i in range(150)]
            return pd.DataFrame(
                {"open": closes, "high": [c + 0.1 for c in closes], "low": [c - 0.1 for c in closes], "close": closes, "volume": 1e6}
            )

        def get_quote(self, symbol):
            return QuoteData(symbol=symbol, price=107.0, change_pct_24h=0.1, volume=1e6, avg_volume_20d=1e6)

    plan = _generate(session, WeakBullish(), _default_settings(ai_trading_overlay_enabled=False))
    assert plan.status == "no_trade"
    result = classify_plan(plan)
    assert (result.category, result.simulate) == (CATEGORY_LOW_CONFIDENCE, True)
    # The direction the reason names is the one the evaluation had.
    assert missed_trades._direction_from_text(plan) == "long"


@pytest.mark.parametrize(
    "llm_json",
    [
        '{"stance": "bearish", "confidence": 78, "reasoning": "Looks exhausted."}',
        '{"stance": "neutral", "trade_verdict": "pass", "confidence": 80, "reasoning": "I would not take it."}',
    ],
)
def test_real_ai_veto_decision(session, llm_json):
    plan = _generate(session, FakeUptrendDataProvider(), _default_settings(), FakeConfiguredLLMProvider(llm_json))
    assert plan.status == "no_trade"
    result = classify_plan(plan)
    assert (result.category, result.simulate) == (CATEGORY_AI_VETO, True)
    assert missed_trades._direction_from_text(plan) == "long"


def test_real_held_by_ai_plan(session):
    settings = _default_settings(ai_overlay_objection_action="hold", ai_overlay_scores_confidence=False)
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 62, "reasoning": "Looks exhausted."}')
    result = _classified(session, FakeUptrendDataProvider(), settings, llm)
    assert (result.category, result.simulate) == (CATEGORY_HELD_BY_AI, True)


def test_real_insufficient_cash_refusal(session):
    result = _classified(
        session, FakeUptrendDataProvider(), _default_settings(ai_trading_overlay_enabled=False), account=1.0
    )
    assert (result.category, result.detail) == (CATEGORY_NOT_EXECUTED, DETAIL_INSUFFICIENT_CASH)


def test_real_duplicate_cap_and_sector_refusals(session):
    base = dict(ai_trading_overlay_enabled=False)
    # the first plan opens a position; later symbols then meet the duplicate check, the cap and the sector limit
    first = _generate(session, FakeUptrendDataProvider(), _default_settings(**base), symbol=AAPL)
    assert first.status == "executed"
    dup = classify_plan(_generate(session, FakeUptrendDataProvider(), _default_settings(**base), symbol=AAPL))
    assert (dup.category, dup.detail, dup.simulate) == (CATEGORY_NOT_EXECUTED, DETAIL_DUPLICATE_POSITION, False)
    capped = classify_plan(
        _generate(session, FakeUptrendDataProvider(), _default_settings(max_concurrent_positions=1, **base), symbol="NVDA")
    )
    assert (capped.category, capped.detail) == (CATEGORY_NOT_EXECUTED, DETAIL_POSITION_CAP)
    sector = classify_plan(
        _generate(session, FakeUptrendDataProvider(), _default_settings(max_positions_per_sector=1, **base), symbol=MSFT)
    )
    assert (sector.category, sector.detail) == (CATEGORY_NOT_EXECUTED, DETAIL_SECTOR_CAP)


def test_real_stale_price_refusal(session):
    class Drifted(FakeUptrendDataProvider):
        def get_quote(self, symbol):
            return QuoteData(symbol=symbol, price=600.0, change_pct_24h=1.0, volume=5e6, avg_volume_20d=1e6)

    result = _classified(session, Drifted(), _default_settings(ai_trading_overlay_enabled=False))
    assert (result.category, result.detail) == (CATEGORY_NOT_EXECUTED, DETAIL_STALE_PRICE)


def test_real_plan_left_pending_when_auto_execute_is_off(session):
    settings = _default_settings(ai_trading_overlay_enabled=False, auto_execute_trade_plans=False)
    result = _classified(session, FakeUptrendDataProvider(), settings)
    assert (result.category, result.detail) == (CATEGORY_NOT_EXECUTED, DETAIL_AWAITING_MANUAL)


def test_real_off_hours_plan_awaiting_its_redo(session):
    sunday = datetime(2026, 9, 27, 16, 28)
    result = _classified(
        session, FakeUptrendDataProvider(), _default_settings(ai_trading_overlay_enabled=False), clock=lambda: sunday
    )
    assert (result.category, result.detail, result.simulate) == (CATEGORY_NOT_EXECUTED, DETAIL_MARKET_CLOSED_REDO, False)


# ------------------------------------------------- the rebuilt trade (hand-computed)


def test_long_decision_is_rebuilt_with_the_live_rules():
    result = run(no_trade_plan_obj(), long_bars([]))
    assert result.direction == "long" and result.trade_source == "reconstructed"
    assert result.direction_source == "reason"
    assert result.entry == pytest.approx(LONG_ENTRY)
    assert result.stop == pytest.approx(LONG_STOP)
    assert result.tp1 == pytest.approx(LONG_TP1)
    assert result.entry_bar_date == LAST_HISTORY_DAY
    assert result.bars_known == TREND_BARS


def test_short_decision_is_rebuilt_with_the_live_rules():
    result = run(no_trade_plan_obj(SHORT_REASON), short_bars([]))
    assert result.direction == "short"
    assert (result.entry, result.stop, result.tp1) == (
        pytest.approx(SHORT_ENTRY), pytest.approx(SHORT_STOP), pytest.approx(SHORT_TP1)
    )


def test_the_rebuild_matches_the_live_functions_on_the_known_bars():
    """The levels are whatever the live path's own functions return for the bars known then."""
    from app.analysis.indicators import latest_atr
    from app.analysis.trend import analyze_chart
    from app.risk.position_sizing import derive_targets
    from app.services.trade_plan_service import ATR_PERIOD, _derive_entry_and_stop

    bars = trend_frame(1, 100.0)
    chart = analyze_chart(bars)
    entry, stop = _derive_entry_and_stop("long", chart.price, chart.support, chart.resistance, latest_atr(bars, ATR_PERIOD))
    targets = derive_targets(entry, stop, "long", chart.support, chart.resistance)
    result = run(no_trade_plan_obj(), long_bars([]))
    assert (result.entry, result.stop, result.tp1) == (entry, stop, targets.tp1)


def no_trade_plan_obj(reason: str = LONG_REASON, created_at: datetime = DECISION_AFTER_CLOSE, **kw) -> TradePlanRecord:
    values = dict(id=1, symbol="TEST", status="no_trade", reason=reason, confidence_score=25, created_at=created_at)
    values.update(kw)
    return TradePlanRecord(**values)


def test_direction_falls_back_to_the_trend_when_the_text_names_none():
    plan = no_trade_plan_obj("AI Trading Overlay objected.")
    up = run(plan, long_bars([]))
    assert (up.direction, up.direction_source) == ("long", "trend")
    down = run(plan, short_bars([]))
    assert (down.direction, down.direction_source) == ("short", "trend")


def test_no_trend_and_no_direction_cannot_be_simulated():
    flat = pd.DataFrame(
        {"date": pd.to_datetime(HISTORY_DAYS), "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1e6}
    )
    result = run(no_trade_plan_obj("AI Trading Overlay objected."), flat)
    assert result.status == OUTCOME_NOT_SIMULATABLE
    assert "No trend" in result.note


def test_too_little_history_is_no_data_not_a_guess():
    result = run(no_trade_plan_obj(), trend_frame(1, 100.0).head(30))
    assert result.status == OUTCOME_NO_DATA


def test_a_written_plan_keeps_its_own_entry_stop_and_target():
    plan = no_trade_plan_obj(
        status="pending", reason=None, direction="long", entry=199.0, stop=190.0, tp1=205.0, tp2=210.0,
        suggested_shares=10, auto_execute_note="Auto-execute held: AI Trading Overlay called this bearish against a rule-based long",
    )
    # Never touches stop 190 or target 205 -> still open, at the plan's own levels.
    result = run(plan, long_bars([(199.5, 200.5, 198.5, 200.0)]))
    assert result.trade_source == "plan" and result.direction_source == "plan"
    assert (result.entry, result.stop, result.tp1) == (199.0, 190.0, 205.0)
    assert result.status == OUTCOME_OPEN


# ------------------------------------------------------------------ no look-ahead


def test_bars_after_the_decision_never_change_the_rebuilt_trade():
    quiet = run(no_trade_plan_obj(), long_bars([(199.5, 200.5, 198.5, 200.0)]))
    wild = run(
        no_trade_plan_obj(),
        long_bars([(500.0, 9000.0, 1.0, 4000.0)] + [(1.0, 1.0, 1.0, 1.0)] * 30),
    )
    for name in ("direction", "entry", "stop", "tp1", "bars_known", "entry_bar_date"):
        assert getattr(quiet, name) == getattr(wild, name), name
    assert wild.entry == pytest.approx(LONG_ENTRY)


def test_a_decision_made_mid_session_enters_at_the_previous_close_and_skips_its_own_day():
    """11:00 ET on the last history day: that day's bar is still forming. The entry is the
    close before it (198), and a crash recorded in that very bar must not stop the trade out,
    just as the live engine never lets the entry bar trigger an exit."""
    bars = trend_frame(1, 100.0)
    bars.loc[bars.index[-1], ["low", "close"]] = [1.0, 50.0]  # the day's final bar, from the future
    result = run(no_trade_plan_obj(created_at=DECISION_INTRADAY), pd.concat([bars, future_frame([(199.5, 200.5, 198.5, 199.0)])], ignore_index=True))
    assert result.entry == pytest.approx(198.0)
    assert result.stop == pytest.approx(198 * 0.97)
    assert result.tp1 == pytest.approx(198 + 1.5 * (198 - 198 * 0.97))
    assert result.entry_bar_date == HISTORY_DAYS[-2]
    assert result.status == OUTCOME_OPEN and result.bars_walked == 1


# ------------------------------------------------------------------- the exit walk


def test_long_target_hit_is_one_and_a_half_r():
    result = run(
        no_trade_plan_obj(),
        long_bars([(199.5, 200.5, 198.5, 200.0), (200.0, 203.0, 199.0, 202.0), (202.0, 208.5, 201.0, 208.0)]),
    )
    assert result.status == OUTCOME_RESOLVED and result.exit_reason == "tp1_hit"
    assert result.exit_price == pytest.approx(LONG_TP1)
    assert result.r_multiple == pytest.approx(1.5)
    assert result.bars_walked == 3 and result.exit_date == ALL_DAYS[TREND_BARS + 2]


def test_long_stop_through_a_gap_fills_at_the_open():
    result = run(no_trade_plan_obj(), long_bars([(199.0, 200.0, 198.0, 199.5), (190.0, 192.0, 188.0, 191.0)]))
    assert result.exit_reason == "stop_hit"
    assert result.exit_price == pytest.approx(190.0)  # the open, worse than the 193.03 stop
    assert result.r_multiple == pytest.approx((190.0 - LONG_ENTRY) / LONG_RISK)


def test_short_target_hit_is_one_and_a_half_r():
    result = run(no_trade_plan_obj(SHORT_REASON), short_bars([(201.0, 202.0, 200.0, 201.0), (200.0, 201.0, 190.0, 192.0)]))
    assert result.exit_reason == "tp1_hit" and result.direction == "short"
    assert result.exit_price == pytest.approx(SHORT_TP1)
    assert result.r_multiple == pytest.approx(1.5)


def test_short_stop_through_a_gap_fills_at_the_open():
    result = run(no_trade_plan_obj(SHORT_REASON), short_bars([(210.0, 211.0, 209.0, 210.5)]))
    assert result.exit_reason == "stop_hit"
    assert result.exit_price == pytest.approx(210.0)
    assert result.r_multiple == pytest.approx(-(210.0 - SHORT_ENTRY) / SHORT_RISK)


def test_a_bar_holding_both_levels_takes_the_stop():
    result = run(no_trade_plan_obj(), long_bars([(199.0, 209.0, 192.0, 200.0)]))
    assert result.exit_reason == "stop_hit"
    assert result.r_multiple == pytest.approx(-1.0)
    assert "stop was taken" in result.note
    assert result.resolution == "daily"  # no hourly refinement, recorded on every outcome


def test_slippage_is_charged_on_the_entry_like_the_paper_account():
    settings = AppSettings(slippage_bps=10.0, commission_per_trade=0.0, max_holding_days=20)
    result = run(no_trade_plan_obj(), long_bars([(200.0, 208.5, 199.0, 208.0)]), settings)
    fill = 199.0 * 1.001
    assert result.fill_price == pytest.approx(fill)
    assert result.r_multiple == pytest.approx((LONG_TP1 - fill) / (fill - LONG_STOP))


def test_commission_is_charged_at_both_ends_over_the_money_risked():
    settings = AppSettings(slippage_bps=0.0, commission_per_trade=1.0, max_holding_days=20)
    plan = no_trade_plan_obj(suggested_shares=100)
    result = run(plan, long_bars([(200.0, 208.5, 199.0, 208.0)]), settings)
    assert result.r_multiple == pytest.approx(1.5 - 2.0 / (100 * LONG_RISK))


def test_the_holding_limit_closes_at_the_close_of_the_last_bar():
    settings = AppSettings(slippage_bps=0.0, commission_per_trade=0.0, max_holding_days=3)
    result = run(
        no_trade_plan_obj(),
        long_bars([(199.5, 200.5, 198.5, 199.5), (199.5, 200.5, 198.5, 200.0), (199.5, 200.6, 198.5, 200.1), (199.5, 200.5, 198.5, 200.0)]),
        settings,
    )
    assert result.exit_reason == "time_exit" and result.bars_walked == 3
    assert result.exit_price == pytest.approx(200.1)
    assert result.r_multiple == pytest.approx((200.1 - LONG_ENTRY) / LONG_RISK)


def test_a_trade_with_no_exit_yet_is_open_and_marked_not_final():
    result = run(no_trade_plan_obj(), long_bars([(199.5, 200.5, 198.5, 200.0), (200.0, 201.0, 199.5, 201.0)]))
    assert result.status == OUTCOME_OPEN and result.exit_reason is None
    assert result.mark_price == pytest.approx(201.0) and result.bars_walked == 2
    assert result.r_multiple == pytest.approx((201.0 - LONG_ENTRY) / LONG_RISK)


def test_a_decision_with_no_full_day_after_it_is_open_without_a_mark():
    result = run(no_trade_plan_obj(), long_bars([]))
    assert result.status == OUTCOME_OPEN and result.r_multiple is None and result.bars_walked == 0


def test_a_bar_that_is_not_final_yet_is_never_walked():
    """`now` on the last bar's own session: that bar is still forming and must not decide anything."""
    bars = long_bars([(199.5, 200.5, 198.5, 200.0), (200.0, 209.0, 199.0, 208.0)])
    first_future, second_future = ALL_DAYS[TREND_BARS], ALL_DAYS[TREND_BARS + 1]
    now = datetime.combine(second_future, time(15, 0))  # 11:00 ET on the second future day
    result = run(no_trade_plan_obj(), bars, now=now)
    assert result.status == OUTCOME_OPEN and result.bars_walked == 1
    assert result.mark_price == pytest.approx(200.0)
    assert first_future < second_future


def test_the_exit_walk_agrees_with_the_paper_engines_own_mark_to_market(session):
    """The same position, bars and clock through the real engine's exit sweep: same fill, reason and R."""
    future = [(199.5, 200.5, 198.5, 200.0), (200.0, 203.0, 199.0, 202.0), (192.0, 194.0, 188.0, 190.0)]
    bars = long_bars(future)
    settings = AppSettings(slippage_bps=10.0, commission_per_trade=0.0, max_holding_days=20)
    result = run(no_trade_plan_obj(), bars, settings)

    class Provider:
        name = "fake"

        def get_ohlcv(self, symbol, period="3mo", interval="1d"):
            return bars

    fill = 199.0 * 1.001
    position = PaperPosition(
        symbol="TEST", direction="long", entry_price=fill, stop_loss=LONG_STOP, tp1=LONG_TP1, tp2=216.0, shares=100,
        opened_at=DECISION_AFTER_CLOSE,
    )
    session.add(position)
    session.commit()
    engine = PaperTradingEngine(
        session, Provider(), 100_000.0, slippage_bps=10.0, clock=lambda: NOW, max_holding_days=20, intraday_exits=False
    )
    [closed] = engine.mark_to_market(snapshot=False)
    assert result.exit_reason == closed.close_reason == "stop_hit"
    assert result.exit_price == pytest.approx(closed.close_price)
    assert result.r_multiple == pytest.approx(closed.realized_r)


# ------------------------------------------------------------- refresh and caching


class Loader:
    """A bars source that counts calls and can be told to fail."""

    def __init__(self, bars: pd.DataFrame | None, fail: bool = False):
        self.bars, self.fail, self.calls = bars, fail, []

    def __call__(self, symbol: str, start: date) -> pd.DataFrame:
        self.calls.append((symbol, start))
        if self.fail:
            raise RuntimeError("provider down")
        return self.bars


def rows_by_plan(session) -> dict[int, MissedTradeOutcome]:
    return {r.plan_id: r for r in session.exec(select(MissedTradeOutcome)).all()}


def test_refresh_stores_one_row_per_simulatable_plan(session):
    veto = no_trade_plan(session, REAL_VETO_PASS)
    low = no_trade_plan(session, LONG_REASON, symbol="LOW")
    neutral = no_trade_plan(session, REAL_NO_TREND, symbol="FLAT")
    executed = no_trade_plan(session, LONG_REASON, status="executed", symbol="DONE")
    loader = Loader(long_bars([(200.0, 208.5, 199.0, 208.0)]))

    summary = refresh_missed_trades(session, CLEAN, loader, now=NOW)

    rows = rows_by_plan(session)
    assert set(rows) == {veto.id, low.id}  # neutral is counted by the report, never simulated; executed isn't a miss
    assert (summary.considered, summary.computed, summary.resolved) == (2, 2, 2)
    assert rows[veto.id].category == CATEGORY_AI_VETO and rows[veto.id].status == OUTCOME_RESOLVED
    assert rows[veto.id].r_multiple == pytest.approx(1.5) and rows[veto.id].resolution == "daily"
    assert neutral.id not in rows and executed.id not in rows
    # one price-history load per symbol, reaching back a year before the decision
    assert sorted(s for s, _ in loader.calls) == ["LOW", "TEST"]
    assert all(start == DECISION_AFTER_CLOSE.date() - timedelta(days=375) for _, start in loader.calls)


def test_a_resolved_outcome_is_never_recomputed_and_a_rerun_changes_nothing(session):
    plan = no_trade_plan(session)
    loader = Loader(long_bars([(200.0, 208.5, 199.0, 208.0)]))
    refresh_missed_trades(session, CLEAN, loader, now=NOW)
    before = rows_by_plan(session)[plan.id].model_dump()

    later = Loader(long_bars([(200.0, 9999.0, 1.0, 5.0)]), fail=True)  # would change or fail it if consulted
    summary = refresh_missed_trades(session, CLEAN, later, now=NOW + timedelta(days=1))

    assert later.calls == []  # not even loaded
    assert (summary.considered, summary.computed, summary.skipped_resolved) == (0, 0, 1)
    assert rows_by_plan(session)[plan.id].model_dump() == before
    assert len(session.exec(select(MissedTradeOutcome)).all()) == 1


def test_an_open_outcome_is_recomputed_until_it_resolves(session):
    plan = no_trade_plan(session)
    refresh_missed_trades(session, CLEAN, Loader(long_bars([(199.5, 200.5, 198.5, 200.0)])), now=NOW)
    row = rows_by_plan(session)[plan.id]
    assert row.status == OUTCOME_OPEN and row.r_multiple == pytest.approx((200.0 - LONG_ENTRY) / LONG_RISK)

    summary = refresh_missed_trades(
        session, CLEAN, Loader(long_bars([(199.5, 200.5, 198.5, 200.0), (200.0, 208.5, 199.0, 208.0)])), now=NOW
    )
    row = rows_by_plan(session)[plan.id]
    assert (summary.computed, summary.resolved) == (1, 1)
    assert row.status == OUTCOME_RESOLVED and row.exit_reason == "tp1_hit" and row.r_multiple == pytest.approx(1.5)
    assert len(session.exec(select(MissedTradeOutcome)).all()) == 1  # updated in place, plan_id is unique


def test_a_failed_price_load_is_recorded_as_no_data_and_retried(session):
    plan = no_trade_plan(session)
    summary = refresh_missed_trades(session, CLEAN, Loader(None, fail=True), now=NOW)
    row = rows_by_plan(session)[plan.id]
    assert row.status == OUTCOME_NO_DATA and "provider down" in row.note and row.r_multiple is None
    assert summary.failed_symbols == ["TEST"]

    refresh_missed_trades(session, CLEAN, Loader(long_bars([(200.0, 208.5, 199.0, 208.0)])), now=NOW)
    assert rows_by_plan(session)[plan.id].status == OUTCOME_RESOLVED


def test_a_failing_symbol_does_not_stop_the_others(session):
    bad = no_trade_plan(session, symbol="BAD")
    good = no_trade_plan(session, symbol="GOOD")
    good_bars = long_bars([(200.0, 208.5, 199.0, 208.0)])

    def loader(symbol, start):
        if symbol == "BAD":
            raise RuntimeError("no such symbol")
        return good_bars

    refresh_missed_trades(session, CLEAN, loader, now=NOW)
    rows = rows_by_plan(session)
    assert rows[bad.id].status == OUTCOME_NO_DATA and rows[good.id].status == OUTCOME_RESOLVED


def test_a_refresh_computes_a_bounded_number_newest_first(session):
    plans = [
        no_trade_plan(session, symbol=f"S{i}", created_at=DECISION_AFTER_CLOSE + timedelta(minutes=i)) for i in range(5)
    ]
    summary = refresh_missed_trades(session, CLEAN, Loader(long_bars([(200.0, 208.5, 199.0, 208.0)])), now=NOW, max_plans=3)
    assert (summary.considered, summary.computed, summary.remaining) == (5, 3, 2)
    assert set(rows_by_plan(session)) == {p.id for p in plans[2:]}  # the newest three first
    again = refresh_missed_trades(session, CLEAN, Loader(long_bars([(200.0, 208.5, 199.0, 208.0)])), now=NOW, max_plans=3)
    assert (again.computed, again.remaining, again.skipped_resolved) == (2, 0, 3)
    assert len(rows_by_plan(session)) == len(plans)


def test_a_refresh_already_running_does_nothing(session):
    no_trade_plan(session)
    assert missed_trades._refresh_lock.acquire(blocking=False)
    try:
        summary = refresh_missed_trades(session, CLEAN, Loader(long_bars([])), now=NOW)
    finally:
        missed_trades._refresh_lock.release()
    assert summary.busy and summary.computed == 0
    assert session.exec(select(MissedTradeOutcome)).all() == []


def test_a_not_simulatable_decision_is_final(session):
    flat = pd.DataFrame(
        {"date": pd.to_datetime(HISTORY_DAYS), "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1e6}
    )
    plan = no_trade_plan(session, "AI Trading Overlay objected.")
    refresh_missed_trades(session, CLEAN, Loader(flat), now=NOW)
    assert rows_by_plan(session)[plan.id].status == OUTCOME_NOT_SIMULATABLE
    again = Loader(flat)
    refresh_missed_trades(session, CLEAN, again, now=NOW)
    assert again.calls == []

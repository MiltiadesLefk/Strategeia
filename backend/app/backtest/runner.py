"""The day loop: replay the live strategy over past trading days.

What runs is the live code, unchanged: `generate_trade_plan` makes every decision
(scoring, direction, stop, targets, size, the engine's open checks) and
`PaperTradingEngine` fills, marks and closes positions. The runner only swaps the
world underneath them:

* data   -> BacktestDataProvider (local history, cut off at the simulated moment)
* clock  -> the simulated moment from `with as_of(...)`
* database -> a fresh in-memory SQLite, thrown away at the end (the real
  strategeia.db and settings.json are never opened)
* LLM    -> none, Telegram -> blank, archive -> skipped (it does not write while simulated)

One trading day t, in order:

1. Decision, simulated clock 09:45 ET (same moment the live market-open pass
   uses). Bars known: through t-1. The quote is t's open. Every symbol without an
   open position gets a full evaluation, with the same slot logic as the live
   auto-scan (no auto-execute once max_concurrent_positions is reached).
2. Exits and the day's equity, simulated clock 16:30 ET (13:30 on an early close),
   once t's bar is final. `mark_to_market` walks the bars since each entry with the
   engine's own rules (entry bar skipped, stop before TP1, gap fills, time exit);
   the equity point is the one snapshot it records, priced at that day's close.

Entries fill at t's open plus slippage. The engine skips the entry day's own bar
in the exit scan (its pre-entry range must not stop a position that did not exist
yet) and the backtest has no hourly bars, so a stop or target touched later on the
entry day is not seen until the next day's bar: results are slightly optimistic
for that reason.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import delete
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.backtest.calendar import close_moment, decision_moment, trading_days
from app.backtest.coverage import describe_coverage
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.dated_sources import FactBackedSources
from app.backtest.params import BENCHMARK_SYMBOLS, BacktestInputError, BacktestParams, settings_snapshot
from app.config import AppSettings
from app.knowledge.point_in_time import as_of, current_as_of
from app.llm_providers.null_provider import NullLLMProvider
from app.markets import to_market_time
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services.trade_plan_service import generate_trade_plan

logger = logging.getLogger(__name__)

# The strategy reads a year of daily bars (EMA50, pivots) and two years of weekly
# bars, so a symbol is not evaluated until it has this many bars behind the
# decision day (about one trading year). Younger listings join the run when they
# reach it.
MIN_WARMUP_BARS = 252
SOURCE_NAME = "backtest"
# How many distinct evaluation errors a run keeps for the summary.
MAX_ERROR_SAMPLES = 5


# A replacement for the live decision on one (symbol, day): called as
# policy(symbol, day, provider, session, settings, allow_auto_execute=...) inside the
# simulated 09:45 moment, it returns what generate_trade_plan returns (a no-trade
# response, or a plan that has been through the engine's open checks).
EntryPolicy = Callable[..., Any]


@dataclass
class BacktestResult:
    status: str  # "done" or "cancelled"
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)


def validate_run(params: BacktestParams, book: PriceBook, today: date | None = None) -> None:
    """Refuse a run that cannot give a meaningful answer, with a message that says what to do."""
    params.validate_dates()
    missing = [s for s in BENCHMARK_SYMBOLS if s not in book]
    if missing:
        raise BacktestInputError(
            f"price history for {', '.join(missing)} is not stored. The weekly/market confirmation and the VIX "
            "check need it: run scripts/preload_history.py --benchmarks first"
        )
    if not any(s in book for s in params.symbols):
        raise BacktestInputError(
            "none of the requested symbols has stored price history: run scripts/preload_history.py for them first"
        )
    for benchmark in BENCHMARK_SYMBOLS:
        series = book.series[benchmark]
        if series.days[-1].astype(object) < params.end:
            raise BacktestInputError(
                f"{benchmark} history ends {series.days[-1]}, before the requested end {params.end}"
            )


def _market_date(moment: datetime) -> date:
    return to_market_time(moment).date()


def _max_drawdown_pct(starting_cash: float, equity: list[dict[str, Any]]) -> float:
    peak = starting_cash
    worst = 0.0
    for point in equity:
        value = point["equity"]
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    return worst


def _build_engine(session: Session, provider: BacktestDataProvider, settings: AppSettings) -> PaperTradingEngine:
    """The live exit engine, built exactly as the live scheduler and portfolio
    router build it (a test compares the two), with the simulated clock and the
    daily-only exit rules (the backtest has no hourly bars)."""
    return PaperTradingEngine(
        session,
        provider,
        settings.paper_starting_cash,
        settings.max_concurrent_positions,
        slippage_bps=settings.slippage_bps,
        commission_per_trade=settings.commission_per_trade,
        max_positions_per_sector=settings.max_positions_per_sector,
        max_position_pct_of_adv=settings.max_position_pct_of_adv,
        max_holding_days=settings.max_holding_days,
        liquidity_slippage_coefficient=settings.liquidity_slippage_coefficient if settings.liquidity_slippage_enabled else None,
        clock=current_as_of,
        intraday_exits=False,
    )


def _new_scratch_db():
    """A private in-memory database for one run. StaticPool keeps the single
    connection alive (an in-memory SQLite database exists only as long as its
    connection does)."""
    # Imported so every table is registered before create_all.
    from app.knowledge import models as _knowledge  # noqa: F401
    from app.portfolio import models as _portfolio  # noqa: F401
    from app.strategy import models as _strategy  # noqa: F401

    scratch = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(scratch)
    # generate_trade_plan looks pending plans up by symbol on every call.
    with scratch.begin() as conn:
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_scratch_plan ON tradeplanrecord (symbol, status)")
    return scratch


def run_backtest(
    params: BacktestParams,
    settings: AppSettings,
    book: PriceBook,
    *,
    progress: Callable[[int, int, date], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    provider: BacktestDataProvider | None = None,
    entry_policy: EntryPolicy | None = None,
    fact_session_factory: Callable[[], Session] | None = None,
) -> BacktestResult:
    """Replay `params` and return the trades, the daily equity and a summary.

    `settings` are the effective settings (see effective_settings). `provider`
    may be passed in by tests that need to watch every request. `entry_policy`
    replaces the decision on each (symbol, day): None is the live scoring; the
    random-entry baseline (baseline.py) passes its own. Exits, fills, sizing
    caps and the equity curve are the same whichever policy decides.

    `fact_session_factory` opens a read-only session on the database that holds the
    dated facts (revenue filings, Form 4s, earnings reports): it is needed when the
    run switches any of those parts on (include_fundamentals / include_insiders /
    include_earnings)."""
    validate_run(params, book)
    provider = provider or _provider_for(params, book, fact_session_factory, entry_policy)
    llm = NullLLMProvider()
    days = trading_days(params.start, params.end)
    if not days:
        raise BacktestInputError("there is no US trading day between start and end")
    symbols = [s for s in params.symbols if s in book]
    skipped = {s: book.skipped.get(s, "no stored price history") for s in params.symbols if s not in book}

    scratch = _new_scratch_db()
    stats: Counter[str] = Counter()
    errors: list[str] = []
    points_histogram: Counter[int] = Counter()
    first_evaluated: dict[str, date] = {}
    equity: list[dict[str, Any]] = []
    cancelled = False
    decision_days = 0
    plan_info: dict[int, dict[str, Any]] = {}

    try:
        with Session(scratch) as session:
            exit_engine = _build_engine(session, provider, settings)
            for index, day in enumerate(days):
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
                if index % params.decision_every_n_days == 0:
                    decision_days += 1
                    _decide(day, symbols, book, provider, llm, session, settings, stats, errors, points_histogram, first_evaluated, plan_info, entry_policy)
                with as_of(close_moment(day)):
                    exit_engine.mark_to_market(snapshot=True)
                    point = _equity_point(session, day)
                equity.append(point)
                if progress is not None:
                    progress(index + 1, len(days), day)

            last_day = days[len(equity) - 1] if equity else days[0]
            trades = _collect_trades(session, provider, days, last_day, plan_info)
    finally:
        scratch.dispose()

    coverage = describe_coverage(settings.min_confidence_for_trade, **_coverage_switches(params, entry_policy))
    summary = summarize(
        params, settings, book, days, equity, trades, stats, errors, points_histogram, first_evaluated,
        skipped, decision_days, cancelled,
    )
    availability = getattr(provider.dated_sources, "availability", None)
    if availability is not None:
        summary["dated_data"] = availability()
    return BacktestResult(
        status="cancelled" if cancelled else "done", trades=trades, equity=equity, summary=summary, coverage=coverage
    )


def _coverage_switches(params: BacktestParams, entry_policy: EntryPolicy | None) -> dict[str, bool]:
    """The dated-part switches that actually applied: a run with a replacement entry
    policy (the random baseline) never scores, so it is price-only whatever the flags."""
    if entry_policy is not None:
        return {}
    return params.dated_switches()


def _provider_for(
    params: BacktestParams,
    book: PriceBook,
    fact_session_factory: Callable[[], Session] | None,
    entry_policy: EntryPolicy | None,
) -> BacktestDataProvider:
    """The data a run sees: prices, plus the dated parts its flags switch on. The
    52-week range needs only prices; revenue, insiders and earnings need the stored
    facts, so asking for them without a way to read the facts is an error, never a
    silent price-only run that reports itself as something more."""
    if entry_policy is not None or not params.uses_dated_facts:
        return BacktestDataProvider(book)
    if fact_session_factory is None:
        raise BacktestInputError(
            "this run asks for dated data (fundamentals, insiders or earnings) but no database of stored facts was "
            "given: run scripts/backfill_fundamentals.py, backfill_insider_trades.py or backfill_earnings.py first"
        )
    sources = FactBackedSources(
        fact_session_factory,
        fundamentals=params.include_fundamentals,
        insiders=params.include_insiders,
        earnings=params.include_earnings,
    )
    return BacktestDataProvider(book, dated_sources=sources, overview_from_prices=params.include_fundamentals)


def _decide(
    day: date,
    symbols: list[str],
    book: PriceBook,
    provider: BacktestDataProvider,
    llm: NullLLMProvider,
    session: Session,
    settings: AppSettings,
    stats: Counter[str],
    errors: list[str],
    points_histogram: Counter[int],
    first_evaluated: dict[str, date],
    plan_info: dict[int, dict[str, Any]],
    entry_policy: EntryPolicy | None = None,
) -> None:
    """One decision pass: the live auto-scan's loop (automation_service.run_auto_scan)
    on the simulated 09:45 moment. Same slot rule: once max_concurrent_positions
    are held, symbols are still evaluated but may not auto-execute."""
    clock = current_as_of
    with as_of(decision_moment(day)):
        open_symbols = {
            p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()
        }
        slots_remaining = max(0, settings.max_concurrent_positions - len(open_symbols))
        for symbol in symbols:
            if symbol in open_symbols:
                continue
            series = book.series[symbol]
            today_bar = series.bar_index_on(day)
            if today_bar is None:
                stats["skipped_no_bar_today"] += 1  # the symbol did not trade (or has a hole in its data)
                continue
            if today_bar < MIN_WARMUP_BARS:
                stats["skipped_warmup"] += 1
                continue
            try:
                if entry_policy is not None:
                    response = entry_policy(symbol, day, provider, session, settings, allow_auto_execute=slots_remaining > 0)
                else:
                    response = generate_trade_plan(
                        symbol,
                        settings.paper_starting_cash,
                        settings.default_risk_pct,
                        provider,
                        llm,
                        session,
                        allow_auto_execute=slots_remaining > 0,
                        source=SOURCE_NAME,
                        clock=clock,
                        settings=settings,
                    )
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not end the run; it is counted and shown
                session.rollback()
                stats["evaluation_errors"] += 1
                message = f"{symbol} on {day}: {type(exc).__name__}: {exc}"
                logger.warning("Backtest evaluation failed: %s", message)
                if len(errors) < MAX_ERROR_SAMPLES:
                    errors.append(message)
                continue
            stats["evaluations"] += 1
            first_evaluated.setdefault(symbol, day)
            if response.direction is None:
                stats["no_trade"] += 1
                if (response.reason or "").startswith("Confidence too low"):
                    stats["no_trade_below_confidence_bar"] += 1
                    points_histogram[response.confidence_points] += 1
                continue
            stats["plans"] += 1
            points_histogram[response.confidence_points] += 1
            if response.status == "executed":
                stats["executed"] += 1
                slots_remaining -= 1
                plan_info[response.id] = _plan_info(response)
            else:
                stats["plans_not_executed"] += 1
        # Only plans behind a position are kept: a year of "no trade" rows would
        # make every later lookup slower and nothing reads them.
        session.exec(delete(TradePlanRecord).where(TradePlanRecord.status != "executed"))
        session.commit()


def _equity_point(session: Session, day: date) -> dict[str, Any]:
    """The close-of-day snapshot mark_to_market just recorded. Plain SQL: this runs
    once per simulated day and the ORM versions cost more than the exit scan."""
    connection = session.connection()
    snapshot = connection.exec_driver_sql(
        "SELECT equity_value, cash_balance FROM equitysnapshot ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if snapshot is None:  # no position was ever opened or closed, so nothing was recorded: cash only
        account = connection.exec_driver_sql("SELECT current_cash FROM accountstate LIMIT 1").fetchone()
        cash = account[0] if account else 0.0
        return {"day": day, "equity": cash, "cash": cash, "open_positions": 0}
    open_count = connection.exec_driver_sql("SELECT COUNT(*) FROM paperposition WHERE status = 'open'").fetchone()[0]
    return {"day": day, "equity": snapshot[0], "cash": snapshot[1], "open_positions": open_count}


SCORE_COMPONENTS = (
    "technical_score", "fundamental_score", "news_score", "market_confirmation_score", "vix_regime_score",
    "options_score", "insider_score", "expected_move_score", "earnings_surprise_score", "macro_event_score",
    "ai_overlay_score",
)


def _plan_info(response) -> dict[str, Any]:
    """What an executed plan scored, kept for the trade row (the stored plan
    record has the components but not the points)."""
    return {
        "confidence_points": response.confidence_points,
        "confidence_points_max": response.confidence_points_max,
        "confidence_score": response.confidence_score,
        "signal_reasons": response.signal_reasons,
        "scores": {name: getattr(response, name) for name in SCORE_COMPONENTS},
    }


def _collect_trades(
    session: Session,
    provider: BacktestDataProvider,
    days: list[date],
    last_day: date,
    plan_info: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    day_index = {d: i for i, d in enumerate(days)}
    trades: list[dict[str, Any]] = []
    positions = session.exec(select(PaperPosition).order_by(PaperPosition.id)).all()
    for position in positions:
        info = plan_info.get(position.trade_plan_id, {})
        entry_date = _market_date(position.opened_at)
        row: dict[str, Any] = {
            "symbol": position.symbol,
            "direction": position.direction,
            "status": position.status,
            "entry_at": position.opened_at,
            "entry_date": entry_date,
            "entry_price": position.entry_price,
            "planned_entry_price": position.planned_entry_price,
            "stop_loss": position.stop_loss,
            "tp1": position.tp1,
            "shares": position.shares,
            "confidence_points": info.get("confidence_points"),
            "confidence_points_max": info.get("confidence_points_max"),
            "confidence_score": info.get("confidence_score"),
            "scores": info.get("scores", {}),
            "signal_reasons": info.get("signal_reasons"),
            "mfe_r": position.mfe_r, "mae_r": position.mae_r, "mfe_pct": position.mfe_pct, "mae_pct": position.mae_pct,
        }
        if position.status == "closed":
            exit_date = _market_date(position.closed_at)
            row.update(
                exit_at=position.closed_at, exit_date=exit_date, exit_price=position.close_price,
                close_reason=position.close_reason, realized_pnl=position.realized_pnl,
                realized_r=position.realized_r, fees_paid=position.fees_paid,
                holding_days=day_index[exit_date] - day_index[entry_date] if exit_date in day_index and entry_date in day_index else None,
            )
        else:
            # Still held when the run ended: value it at the last close, leave it out of the win rate.
            with as_of(close_moment(last_day)):
                price = provider.get_quote(position.symbol).price
            sign = 1 if position.direction == "long" else -1
            fees = position.fees_paid or 0.0
            pnl = (price - position.entry_price) * position.shares * sign - fees
            risk = abs(position.entry_price - position.stop_loss) * position.shares
            row.update(
                exit_at=None, exit_date=None, exit_price=price, close_reason="open_at_end", realized_pnl=pnl,
                realized_r=pnl / risk if risk > 0 else 0.0, fees_paid=fees,
                holding_days=day_index[last_day] - day_index[entry_date] if entry_date in day_index else None,
            )
        trades.append(row)
    return trades


def summarize(
    params: BacktestParams,
    settings: AppSettings,
    book: PriceBook,
    days: list[date],
    equity: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    stats: Counter[str],
    errors: list[str],
    points_histogram: Counter[int],
    first_evaluated: dict[str, date],
    skipped: dict[str, str],
    decision_days: int,
    cancelled: bool,
) -> dict[str, Any]:
    """The numbers that sanity-check a run. The heavy statistics (Sharpe, yearly
    breakdown, benchmarks) are deliberately not here."""
    closed = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in closed if (t["realized_pnl"] or 0) > 0]
    starting = settings.paper_starting_cash
    final_equity = equity[-1]["equity"] if equity else starting
    r_values = [t["realized_r"] for t in closed if t["realized_r"] is not None]
    return {
        "starting_cash": starting,
        "final_equity": final_equity,
        "total_return_pct": (final_equity / starting - 1) * 100 if starting else 0.0,
        "trade_count": len(closed),
        "wins": len(wins),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "average_r": (sum(r_values) / len(r_values)) if r_values else None,
        "max_drawdown_pct": _max_drawdown_pct(starting, equity),
        "open_at_end": len(trades) - len(closed),
        "days_simulated": len(equity),
        "days_requested": len(days),
        "decision_days": decision_days,
        "first_day": days[0].isoformat(),
        "last_day": (days[len(equity) - 1] if equity else days[0]).isoformat(),
        "symbols_requested": len(params.symbols),
        "symbols_with_data": sum(1 for s in params.symbols if s in book),
        "symbols_skipped": [{"symbol": s, "reason": r} for s, r in skipped.items()],
        "symbols_evaluated": len(first_evaluated),
        "first_evaluation_by_symbol": {s: d.isoformat() for s, d in sorted(first_evaluated.items())},
        "evaluations": stats["evaluations"],
        "no_trade": stats["no_trade"],
        "no_trade_below_confidence_bar": stats["no_trade_below_confidence_bar"],
        "plans": stats["plans"],
        "executed": stats["executed"],
        "plans_not_executed": stats["plans_not_executed"],
        "skipped_no_bar_today": stats["skipped_no_bar_today"],
        "skipped_warmup": stats["skipped_warmup"],
        "evaluation_errors": stats["evaluation_errors"],
        "error_samples": errors,
        "points_histogram": {str(k): v for k, v in sorted(points_histogram.items())},
        "cancelled": cancelled,
    }


def params_to_json(params: BacktestParams, settings: AppSettings, book: PriceBook) -> str:
    """Everything needed to repeat the run, as stored on BacktestRun."""
    history = {
        symbol: {
            "first": book.series[symbol].days[0].astype(object).isoformat(),
            "last": book.series[symbol].days[-1].astype(object).isoformat(),
            "bars": len(book.series[symbol]),
        }
        for symbol in [*params.symbols, *BENCHMARK_SYMBOLS]
        if symbol in book
    }
    payload = {
        "symbols": params.symbols,
        "start": params.start.isoformat(),
        "end": params.end.isoformat(),
        "decision_every_n_days": params.decision_every_n_days,
        "include_fundamentals": params.include_fundamentals,
        "include_insiders": params.include_insiders,
        "include_earnings": params.include_earnings,
        "run_baseline": getattr(params, "run_baseline", False),
        "baseline_runs": getattr(params, "baseline_runs", 0),
        "overrides": params.overrides.model_dump(exclude_none=True),
        "effective_settings": settings_snapshot(settings),
        "decision_convention": "09:45 ET: bars through t-1, quote = t's open; exits at the close of t",
        "min_warmup_bars": MIN_WARMUP_BARS,
        "history": history,
    }
    return json.dumps(payload, sort_keys=True)

"""Kill switches and drift alarms for sleeves.

Two reasons a sleeve can be paused:
  drawdown  its equity is more than `kill_switch_drawdown_pct` below its peak
            (measured from the last time a pause was lifted, so lifting one does
            not instantly trigger again)
  drift     (core sleeve only, since the backtester runs the core rules) the
            spread of live trade results in R moved away from the latest finished
            backtest's, PSI above `kill_switch_drift_psi`, with enough trades on
            both sides

A pause only blocks OPENING (the engine refuses new positions); open positions
keep being managed and nothing is closed. It is lifted by hand. Everything is off
until `kill_switch_enabled` is switched on. The check reads stored rows only and
makes no market-data calls.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlmodel import Session, select

from app.backtest.models import RUN_DONE, BacktestRun, BacktestTrade
from app.config import AppSettings
from app.portfolio.drift import psi, psi_band
from app.portfolio.kill_switch_models import SleevePause, active_pause
from app.portfolio.models import EquitySnapshot, PaperPosition, Sleeve
from app.portfolio.sleeves import CORE_SLEEVE_KEY, ensure_core_sleeve, scope_clause, scope_of
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Fewer closed trades than this on either side and a distribution comparison is noise.
MIN_DRIFT_TRADES = 20


def list_pauses(session: Session, limit: int = 50) -> list[SleevePause]:
    return list(session.exec(select(SleevePause).order_by(SleevePause.id.desc()).limit(limit)).all())  # type: ignore[union-attr]


def _last_resolved_at(session: Session, sleeve_key: str):
    return session.exec(
        select(SleevePause.resolved_at)
        .where(SleevePause.sleeve_key == sleeve_key, SleevePause.resolved_at.is_not(None))  # type: ignore[union-attr]
        .order_by(SleevePause.resolved_at.desc())  # type: ignore[union-attr]
    ).first()


def resume_sleeve(session: Session, sleeve_key: str) -> bool:
    """Lifts the sleeve's pause. True if there was one."""
    pause = active_pause(session, sleeve_key)
    if pause is None:
        return False
    pause.resolved_at = utcnow_naive()
    session.add(pause)
    session.commit()
    return True


def drawdown_check(session: Session, sleeve: Sleeve, threshold_pct: float) -> str | None:
    """A sentence when the sleeve's equity is `threshold_pct` or more below its peak."""
    query = select(EquitySnapshot).where(scope_clause(EquitySnapshot.sleeve_id, scope_of(sleeve)))
    since = _last_resolved_at(session, sleeve.key)
    if since is not None:
        query = query.where(EquitySnapshot.timestamp >= since)
    points = session.exec(query.order_by(EquitySnapshot.timestamp)).all()
    if len(points) < 2:
        return None
    values = [p.equity_value for p in points]
    peak = max(values)
    if peak <= 0:
        return None
    down = (peak - values[-1]) / peak * 100  # the current fall, not a past, recovered one
    if down >= threshold_pct:
        return f"equity is {down:.1f}% below its peak (limit {threshold_pct:g}%)"
    return None


def drift_check(session: Session, sleeve: Sleeve, psi_limit: float) -> str | None:
    """Core only. A sentence when live R results drift from the latest finished backtest."""
    if sleeve.key != CORE_SLEEVE_KEY:
        return None
    run = session.exec(
        select(BacktestRun).where(BacktestRun.status == RUN_DONE).order_by(BacktestRun.id.desc())  # type: ignore[union-attr]
    ).first()
    if run is None:
        return None
    reference = [
        r for r in session.exec(select(BacktestTrade.realized_r).where(BacktestTrade.run_id == run.id)).all() if r is not None
    ]
    live = [
        r
        for r in session.exec(
            select(PaperPosition.realized_r).where(
                PaperPosition.status == "closed", scope_clause(PaperPosition.sleeve_id, scope_of(sleeve))
            )
        ).all()
        if r is not None
    ]
    if len(reference) < MIN_DRIFT_TRADES or len(live) < MIN_DRIFT_TRADES:
        return None
    value = psi(reference, live)
    if value is None or value < psi_limit:
        return None
    return (
        f"live results differ from backtest #{run.id} (PSI {value:.2f}, {psi_band(value)}; "
        f"{len(live)} live vs {len(reference)} backtest trades, limit {psi_limit:g})"
    )


def evaluate_kill_switches(
    session: Session, settings: AppSettings, notify: Callable[[str], None] | None = None
) -> list[SleevePause]:
    """Pauses any sleeve that trips a switch and returns the new pauses. A sleeve
    already paused is skipped. `notify` receives the alert text (best effort)."""
    if not settings.kill_switch_enabled:
        return []
    sleeves = [ensure_core_sleeve(session)]
    sleeves += list(session.exec(select(Sleeve).where(Sleeve.key != CORE_SLEEVE_KEY).order_by(Sleeve.id)).all())
    created: list[SleevePause] = []
    for sleeve in sleeves:
        if active_pause(session, sleeve.key) is not None:
            continue
        reason = ""
        detail = None
        if settings.kill_switch_drawdown_pct > 0:
            detail = drawdown_check(session, sleeve, settings.kill_switch_drawdown_pct)
            reason = "drawdown"
        if detail is None and settings.kill_switch_drift_psi > 0:
            detail = drift_check(session, sleeve, settings.kill_switch_drift_psi)
            reason = "drift"
        if detail is None:
            continue
        name = sleeve.name
        pause = SleevePause(sleeve_key=sleeve.key, reason=reason, detail=detail)
        session.add(pause)
        session.commit()
        session.refresh(pause)
        created.append(pause)
        if notify is not None:
            try:
                notify(
                    f"Sleeve '{name}' paused ({reason}): {detail}. It opens no new positions "
                    "until you resume it; open positions are untouched."
                )
                pause.alert_sent = True
                session.add(pause)
                session.commit()
            except Exception:
                logger.exception("Kill switch alert failed")
    return created

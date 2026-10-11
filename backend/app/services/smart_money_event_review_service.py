"""Event-exit review for held smart-money positions: an ALERT, never a sale.

A negative-leaning 8-K item (`NEGATIVE_LEANING_ITEMS`) filed after the position was opened, or an earnings
result under consensus reported after it, raises one Telegram message so the person can look at the position.
Nothing is closed, resized or moved: the engine's stop, target and time limit own every exit (no new exit
type; see notes/Decisions.md). Checked first: `thesis_service` re-checks price/trend pillars and alerts when
the core trend breaks or earnings are close, but it reads neither 8-K items nor a reported earnings miss, so
this covers what it does not. Each event alerts once per position (`SmartMoneyEventAlert`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.analysis.filing_8k_scoring import NEGATIVE_LEANING_ITEMS
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.sec_8k import filings_8k_as_of
from app.knowledge.point_in_time import is_simulated
from app.portfolio.engine import Clock, resolve_clock
from app.portfolio.models import PaperPosition, Sleeve
from app.services.telegram_service import notify
from app.smartmoney.models import SmartMoneyEventAlert
from app.smartmoney.pipeline_rules import EARNINGS_MISS_PCT, EVENT_REVIEW_LOOKBACK_DAYS, SMART_MONEY_SLEEVE_KEYS

logger = logging.getLogger(__name__)

KIND_8K = "negative_8k"
KIND_EARNINGS_MISS = "earnings_miss"


def _already_sent(session: Session, position_id: int, kind: str, ref: str) -> bool:
    return (
        session.exec(
            select(SmartMoneyEventAlert.id).where(
                SmartMoneyEventAlert.position_id == position_id,
                SmartMoneyEventAlert.kind == kind,
                SmartMoneyEventAlert.ref == ref,
            )
        ).first()
        is not None
    )


def _send(session: Session, settings: AppSettings, position: PaperPosition, kind: str, ref: str, text: str, now: datetime) -> str:
    session.add(SmartMoneyEventAlert(position_id=position.id, kind=kind, ref=ref, created_at=now))
    session.commit()
    notify(settings.telegram_bot_token, settings.telegram_chat_id, text)
    return text


def run_event_review(
    session: Session, settings: AppSettings, data_provider: DataProvider, *, clock: Clock | None = None
) -> list[str]:
    """Check every open position of the smart-money sleeves; returns the alert texts sent."""
    if is_simulated():
        return []
    now = resolve_clock(clock)()
    sleeves = session.exec(select(Sleeve).where(Sleeve.key.in_(SMART_MONEY_SLEEVE_KEYS))).all()
    if not sleeves:
        return []
    sleeve_names = {s.id: s.name for s in sleeves}
    positions = session.exec(
        select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.sleeve_id.in_(list(sleeve_names)))
    ).all()
    sent: list[str] = []
    since = now - timedelta(days=EVENT_REVIEW_LOOKBACK_DAYS)
    for position in positions:
        where = f"{position.symbol} (held in '{sleeve_names[position.sleeve_id]}', opened {position.opened_at:%Y-%m-%d})"
        for filing in filings_8k_as_of(session, position.symbol, now, EVENT_REVIEW_LOOKBACK_DAYS, items=NEGATIVE_LEANING_ITEMS):
            if filing.known_at < position.opened_at or filing.known_at < since:
                continue
            if _already_sent(session, position.id, KIND_8K, filing.accession):
                continue
            items = ", ".join(i for i in filing.items if i in NEGATIVE_LEANING_ITEMS)
            sent.append(
                _send(
                    session, settings, position, KIND_8K, filing.accession,
                    f"Event review: {where} - a negative-leaning 8-K (item {items}) was filed on "
                    f"{filing.known_at:%Y-%m-%d}. Look at the position; nothing was sold (the stop, target and time limit decide).",
                    now,
                )
            )
        try:
            history = data_provider.get_earnings_history(position.symbol)
        except Exception as exc:  # noqa: BLE001 - an alert must never break the sweep
            logger.info("Event review: no earnings history for %s: %s", position.symbol, exc)
            history = []
        for entry in history:
            if entry.surprise_pct is None or entry.surprise_pct > -EARNINGS_MISS_PCT:
                continue
            if entry.date < position.opened_at.date() or entry.date > now.date():
                continue
            ref = entry.date.isoformat()
            if _already_sent(session, position.id, KIND_EARNINGS_MISS, ref):
                continue
            sent.append(
                _send(
                    session, settings, position, KIND_EARNINGS_MISS, ref,
                    f"Event review: {where} - earnings reported on {ref} came in {entry.surprise_pct:.1f}% under consensus EPS. "
                    "Look at the position; nothing was sold (the stop, target and time limit decide).",
                    now,
                )
            )
    return sent

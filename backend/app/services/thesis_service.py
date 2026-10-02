# From Anthropic's financial-services skills (anthropics/financial-services).
# Licensed under the Apache License, Version 2.0; full text in THIRD_PARTY_NOTICES.md.
# Adapted from anthropics/financial-services@574ed36
# plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md; changes: the skill's
# workflow (define a thesis with pillars, risks and catalysts; log each development; keep a
# scorecard and a catalyst calendar) is kept as the shape of the record, but everything is
# computed from data the app already holds instead of asked of the user: the pillars are the
# scored reasons on the trade plan, the statuses come from rules over fresh price bars, and the
# log is written by those rules as well as by the user.
"""A thesis per open paper position: seeded from the trade plan, re-checked by rules, never acted on.

What it does, in order:

- **Seed** (`seed_thesis`): when a position is first seen without one, build it from data we
  already hold. Each reason the plan scored becomes a pillar, the AI overlay's verdict (if the plan
  has one) becomes a pillar or a risk, the stop becomes a pillar ("price holds on the right side of
  it"), and earnings and the macro-calendar dates that fall inside the holding window become
  catalysts. The user can add, remove and annotate anything afterwards.
- **Re-check** (`recheck_thesis`): re-evaluate only the pillars a rule can judge from data, using
  fresh price bars (never stale-served ones, the same standard the exit scan holds itself to):
  the daily trend for the trade's direction (the one *core* pillar), how close price is to the
  stop, and whether the weekly trend and the broad market (SPY) still agree. Every status change
  is written to the dated log. Earnings inside a few days is logged once as a catalyst alert.
- **Broken**: `thesis_broken` is set while the core trend pillar is broken, and a one-time
  Telegram alert goes out the first time that happens (setting `thesis_alerts`).
- **Review** (`generate_thesis_review`): an optional short paragraph from the user's AI, built
  only from the stored statuses and dates, and only when the user asks for it.

What it deliberately does not do: open, close, resize or move the stop of a position (the paper
engine's exit scan owns all of that), and write anything on a read. The sweep and the explicit
recheck button are the only callers that change a thesis, and neither runs inside a simulated
(backtest) moment.

Pillars that the rules cannot judge (a plan reason such as "revenue grew 22% YoY", insider buying)
stay "intact" until the user changes them: the tracker records what the plan cited and does not
pretend to re-measure it. Insider SELLING is not used as evidence against a thesis, for the same
reason the scoring ignores it: executives sell routinely, so it says little.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.analysis.indicators import latest_atr
from app.analysis.macro_calendar import ALL_MACRO_EVENTS
from app.analysis.trend import ChartAnalysis, analyze_chart
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.knowledge.point_in_time import is_simulated
from app.llm_providers.base import ROUTINE_TIER, LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.llm_providers.thesis_review_prompt import (
    REVIEW_LOG_ENTRIES,
    ThesisReviewFacts,
    build_thesis_review_prompt,
    untrusted_text,
)
from app.markets import to_market_time
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.portfolio.thesis_models import (
    ITEM_KINDS,
    KIND_CATALYST,
    KIND_PILLAR,
    KIND_RISK,
    LOG_NOTE,
    LOG_SYSTEM,
    PILLAR_AT_RISK,
    PILLAR_BROKEN,
    PILLAR_INTACT,
    PILLAR_STATUSES,
    SOURCE_PLAN,
    SOURCE_RULE,
    SOURCE_USER,
    ThesisRecord,
)
from app.schemas.thesis_schemas import (
    ThesisCatalystSchema,
    ThesisLogEntrySchema,
    ThesisPillarSchema,
    ThesisRiskSchema,
    ThesisSchema,
)
from app.services.telegram_service import notify
from app.services.trade_plan_service import ATR_PERIOD
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Within this many ATRs of the stop (on the right side of it) the "price holds above the stop"
# pillar is at risk. One ATR is about a normal day's range, so inside it an ordinary move can
# reach the stop; that is the point where the pillar should stop reading as comfortable.
STOP_AT_RISK_ATR_MULTIPLE = 1.0

# An earnings report this close (or closer) is logged once as a catalyst alert. Three days is
# the "this week" horizon the brief calls out: close enough that the gap risk is the next thing
# that will happen to the position.
EARNINGS_ALERT_DAYS = 3

# A thesis is re-checked at most this often by the sweep, which itself runs every few minutes
# during the session. The inputs are daily and weekly bars, so a faster cadence only re-reads
# the same data.
THESIS_RECHECK_MIN_INTERVAL = timedelta(minutes=60)

# Catalysts are looked for this far ahead when the time limit is off (0 days). Otherwise the
# window is the holding limit, converted from trading days to calendar days.
DEFAULT_CATALYST_WINDOW_DAYS = 28
TRADING_TO_CALENDAR_DAYS = 7 / 5

# Pillars seeded from the plan's scored reasons are capped so a plan with many small reasons
# does not bury the trend and the stop. Reasons beyond the cap are still in the plan itself.
MAX_SEED_PILLARS = 8

# Limits that keep a thesis (a JSON text column, shown whole) small.
MAX_ITEM_TEXT_CHARS = 600
MAX_NOTE_TEXT_CHARS = 2000
MAX_ITEMS_PER_LIST = 30
MAX_LOG_ENTRIES = 300
SEED_TEXT_CHARS = 200

# The stored failure reason of a review is a short note for the UI, not a log.
REVIEW_ERROR_MAX_CHARS = 200
REVIEW_MAX_CHARS = 900

# Reason text that marks something as evidence AGAINST the trade, not for it, in the strings the
# scorers write (analysis/market_confirmation.py, options_scoring.py, fundamental_scoring.py,
# ai_overlay_scoring.py). Those become risks instead of pillars.
_RISK_REASON_MARKERS = (
    "against ",
    "negative headline",
    "event risk",
    "elevated",
    "declined",
    "within 5% of its 52-week low",
    "insiders buying against",
)
_TECHNICAL_REASON = re.compile(r"\btrend with \w+ momentum\b", re.IGNORECASE)
_WEEKLY_AGREES = re.compile(r"^weekly timeframe also", re.IGNORECASE)
_MARKET_AGREES = re.compile(r"^broad market \(.*\) also", re.IGNORECASE)

KEY_TREND = "trend"
KEY_STOP = "stop"
KEY_WEEKLY = "weekly"
KEY_MARKET = "market"
KEY_REASON = "reason"
KEY_AI = "ai_overlay"
KEY_USER = "user"
# Pillar keys the re-check can judge from data.
MECHANICAL_KEYS = (KEY_TREND, KEY_STOP, KEY_WEEKLY, KEY_MARKET)

STATUS_LABELS = {PILLAR_INTACT: "intact", PILLAR_AT_RISK: "at risk", PILLAR_BROKEN: "broken"}


class ThesisEditError(ValueError):
    """A user edit that cannot be applied (empty text, a bad date, an unknown or protected item)."""


# ---------------------------------------------------------------- JSON helpers


def _load(text: str | None) -> list[dict]:
    try:
        value = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dump(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat() + "Z"


def _clean(text: str, max_chars: int) -> str:
    flat = " ".join(str(text).split())
    if not flat:
        raise ThesisEditError("Text cannot be empty.")
    if len(flat) > max_chars:
        raise ThesisEditError(f"Text is too long (at most {max_chars} characters).")
    return flat


def _short(text: str, max_chars: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= max_chars else flat[: max_chars - 1].rstrip() + "…"


def _market_today(now: datetime) -> date:
    """Today's date on the exchange's clock, so "earnings in 2 days" does not tick over at 8 pm
    New York time."""
    return to_market_time(now).date()


# ------------------------------------------------------------------ seeding


def holding_window_days(settings: AppSettings) -> int:
    """Calendar days a position is expected to be held: the time limit (trading days) converted,
    or a four-week default when the limit is off."""
    if settings.max_holding_days <= 0:
        return DEFAULT_CATALYST_WINDOW_DAYS
    return math.ceil(settings.max_holding_days * TRADING_TO_CALENDAR_DAYS)


def _pillar(key: str, text: str, source: str, *, core: bool = False, status: str = PILLAR_INTACT) -> dict:
    return {
        "id": _new_id(), "key": key, "text": text, "status": status, "source": source,
        "core": core, "detail": None, "checked_at": None,
    }


def _catalyst(key: str, text: str, when: date | None, source: str) -> dict:
    return {"id": _new_id(), "key": key, "text": text, "date": when.isoformat() if when else None, "source": source, "alerted": False}


def _split_reasons(signal_reasons: str | None) -> list[str]:
    return [part.strip() for part in (signal_reasons or "").split(";") if part.strip()]


def _is_risk_reason(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _RISK_REASON_MARKERS)


@dataclass
class SeedContent:
    pillars: list[dict] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    catalysts: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)


def build_seed(
    position: PaperPosition,
    plan: TradePlanRecord | None,
    *,
    earnings_date: date | None,
    today: date,
    window_days: int,
    max_holding_days: int,
    now: datetime,
) -> SeedContent:
    """The starting thesis for a position, built only from what the plan, the position and the
    calendars already hold. Pure: no database, no network."""
    long = position.direction == "long"
    seed = SeedContent()

    want = "bullish" if long else "bearish"
    seed.pillars.append(
        _pillar(KEY_TREND, f"Daily trend stays {want} for this {position.direction}", SOURCE_RULE, core=True)
    )
    side = "above" if long else "below"
    seed.pillars.append(_pillar(KEY_STOP, f"Price holds {side} the stop at ${position.stop_loss:,.2f}", SOURCE_RULE))

    for reason in _split_reasons(plan.signal_reasons if plan else None):
        if _TECHNICAL_REASON.search(reason):
            continue  # the daily trend pillar above is this reason
        text = _short(reason, SEED_TEXT_CHARS)
        if _is_risk_reason(reason):
            seed.risks.append({"id": _new_id(), "text": text[:1].upper() + text[1:], "source": SOURCE_PLAN})
        elif _WEEKLY_AGREES.search(reason):
            seed.pillars.append(_pillar(KEY_WEEKLY, text[:1].upper() + text[1:], SOURCE_PLAN))
        elif _MARKET_AGREES.search(reason):
            seed.pillars.append(_pillar(KEY_MARKET, text[:1].upper() + text[1:], SOURCE_PLAN))
        elif len([p for p in seed.pillars if p["source"] == SOURCE_PLAN]) < MAX_SEED_PILLARS:
            seed.pillars.append(_pillar(KEY_REASON, text[:1].upper() + text[1:], SOURCE_PLAN))

    if plan is not None and (plan.ai_trade_verdict or plan.ai_opinion_stance):
        verdict = plan.ai_trade_verdict
        reasoning = f" Its reasoning: {_short(plan.ai_opinion_text, SEED_TEXT_CHARS)}" if plan.ai_opinion_text else ""
        if verdict == "pass":
            seed.risks.append(
                {"id": _new_id(), "text": f"The AI overlay would have passed on this trade.{reasoning}", "source": SOURCE_PLAN}
            )
        elif verdict == "take":
            seed.pillars.append(_pillar(KEY_AI, f"The AI overlay would take this trade.{reasoning}", SOURCE_PLAN))
        else:
            seed.pillars.append(
                _pillar(KEY_AI, f"AI overlay stance: {plan.ai_opinion_stance or 'none'} (no take/pass verdict).{reasoning}", SOURCE_PLAN)
            )

    risk_dollars = abs(position.entry_price - position.stop_loss)
    seed.risks.append(
        {
            "id": _new_id(),
            "text": f"Touching the stop at ${position.stop_loss:,.2f} ends the trade (about -1R, "
            f"${risk_dollars:,.2f} per share; a gap past it fills worse).",
            "source": SOURCE_RULE,
        }
    )
    if max_holding_days > 0:
        seed.risks.append(
            {
                "id": _new_id(),
                "text": f"A trade that touches neither level is closed after {max_holding_days} trading days.",
                "source": SOURCE_RULE,
            }
        )

    horizon = today + timedelta(days=window_days)
    if earnings_date is not None and today <= earnings_date <= horizon:
        seed.catalysts.append(_catalyst("earnings", "Earnings report", earnings_date, SOURCE_RULE))
    for label, event_day in sorted(ALL_MACRO_EVENTS, key=lambda pair: pair[1]):
        if today <= event_day <= horizon:
            seed.catalysts.append(_catalyst("macro", f"{label} (market-wide)", event_day, SOURCE_RULE))
    target_word = "falls to" if not long else "reaches"
    seed.catalysts.append(_catalyst("target", f"Price {target_word} the first target ${position.tp1:,.2f}", None, SOURCE_RULE))

    if plan is not None:
        origin = f"Thesis created from trade plan #{plan.id} (confidence {plan.confidence_score}%)."
    else:
        origin = "Thesis created from the position itself (no trade plan is stored for it)."
    seed.log.append({"at": _iso(now), "kind": LOG_SYSTEM, "text": origin})
    return seed


def get_thesis(session: Session, position_id: int) -> ThesisRecord | None:
    return session.exec(select(ThesisRecord).where(ThesisRecord.position_id == position_id)).first()


def _safe_earnings_date(data_provider: DataProvider, symbol: str) -> date | None:
    try:
        return data_provider.get_earnings_date(symbol)
    except Exception as exc:  # noqa: BLE001 - a missing earnings date only means no earnings catalyst
        logger.info("Thesis: no earnings date for %s: %s", symbol, exc)
        return None


def seed_thesis(
    session: Session,
    position: PaperPosition,
    data_provider: DataProvider,
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> ThesisRecord:
    """The position's thesis, created from its plan if it has none yet. Idempotent."""
    existing = get_thesis(session, position.id)
    if existing is not None:
        return existing
    when = now or utcnow_naive()
    plan = session.get(TradePlanRecord, position.trade_plan_id) if position.trade_plan_id else None
    seed = build_seed(
        position,
        plan,
        earnings_date=_safe_earnings_date(data_provider, position.symbol),
        today=_market_today(when),
        window_days=holding_window_days(settings),
        max_holding_days=settings.max_holding_days,
        now=when,
    )
    thesis = ThesisRecord(
        position_id=position.id,
        trade_plan_id=position.trade_plan_id,
        symbol=position.symbol,
        direction=position.direction,
        pillars=_dump(seed.pillars),
        risks=_dump(seed.risks),
        catalysts=_dump(seed.catalysts),
        log=_dump(seed.log),
        created_at=when,
        updated_at=when,
    )
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    return thesis


# ----------------------------------------------------------------- re-checking


@dataclass
class MarketReads:
    """The fresh data a re-check judges the pillars against. `weekly` and `market` are None when
    their fetch failed, which leaves those pillars unchanged rather than guessing."""

    price: float
    atr: float | None
    daily: ChartAnalysis
    weekly: ChartAnalysis | None = None
    market: ChartAnalysis | None = None
    earnings_date: date | None = None


@dataclass
class RecheckOutcome:
    # "checked" | "skipped" (nothing re-checked; `note` says why) | "not_open"
    status: str
    changes: list[str] = field(default_factory=list)
    note: str = ""
    alert_sent: bool = False


def _trend_aligned(chart: ChartAnalysis, direction: str) -> str:
    """'aligned' | 'neutral' | 'opposed': what a chart's trend says about a trade direction."""
    if chart.trend == "Neutral":
        return "neutral"
    bullish = chart.trend == "Bullish"
    return "aligned" if bullish == (direction == "long") else "opposed"


def judge_pillar(key: str, position: PaperPosition, reads: MarketReads) -> tuple[str, str] | None:
    """(status, plain-words detail) for a pillar the rules can judge, None when this key is not
    mechanical or the data it needs is missing. Pure."""
    direction = position.direction
    sign = 1.0 if direction == "long" else -1.0
    if key == KEY_TREND:
        read = _trend_aligned(reads.daily, direction)
        detail = f"the daily trend is {reads.daily.trend}"
        if read == "aligned":
            return PILLAR_INTACT, detail
        return (PILLAR_AT_RISK if read == "neutral" else PILLAR_BROKEN), detail
    if key == KEY_STOP:
        room = (reads.price - position.stop_loss) * sign
        if room <= 0:
            return PILLAR_BROKEN, f"price ${reads.price:,.2f} is through the stop at ${position.stop_loss:,.2f}"
        if reads.atr is not None and room <= STOP_AT_RISK_ATR_MULTIPLE * reads.atr:
            return PILLAR_AT_RISK, f"price ${reads.price:,.2f} is within {room / reads.atr:.1f} ATR of the stop at ${position.stop_loss:,.2f}"
        return PILLAR_INTACT, f"price ${reads.price:,.2f} is clear of the stop at ${position.stop_loss:,.2f}"
    if key == KEY_WEEKLY:
        if reads.weekly is None:
            return None
        read = _trend_aligned(reads.weekly, direction)
        detail = f"the weekly trend is {reads.weekly.trend}"
        if read == "aligned":
            return PILLAR_INTACT, detail
        return (PILLAR_AT_RISK if read == "neutral" else PILLAR_BROKEN), detail
    if key == KEY_MARKET:
        if reads.market is None:
            return None
        read = _trend_aligned(reads.market, direction)
        detail = f"the broad market (SPY) trend is {reads.market.trend}"
        # The market is context, not the stock: disagreeing makes the pillar at risk, never broken.
        return (PILLAR_INTACT if read == "aligned" else PILLAR_AT_RISK), detail
    return None


def apply_recheck(
    pillars: list[dict],
    catalysts: list[dict],
    log: list[dict],
    position: PaperPosition,
    reads: MarketReads,
    *,
    now: datetime,
    window_days: int,
) -> list[str]:
    """Re-judge every mechanical pillar and refresh the earnings catalyst, editing the three lists
    in place and appending a dated system entry to `log` for each change. Returns the change texts.
    Pure apart from mutating its arguments."""
    changes: list[str] = []
    stamp = _iso(now)
    today = _market_today(now)

    def record(text: str) -> None:
        changes.append(text)
        log.append({"at": stamp, "kind": LOG_SYSTEM, "text": text})

    for pillar in pillars:
        if pillar.get("key") not in MECHANICAL_KEYS:
            continue
        judged = judge_pillar(pillar["key"], position, reads)
        if judged is None:
            continue
        status, detail = judged
        old = pillar.get("status", PILLAR_INTACT)
        pillar["detail"] = detail
        pillar["checked_at"] = stamp
        if status != old:
            pillar["status"] = status
            record(f"{pillar['text']}: {STATUS_LABELS.get(old, old)} -> {STATUS_LABELS[status]} ({detail}).")

    earnings = next((c for c in catalysts if c.get("key") == "earnings"), None)
    reported = reads.earnings_date
    if reported is not None and reported >= today:
        horizon = today + timedelta(days=window_days)
        if earnings is None and reported <= horizon:
            earnings = _catalyst("earnings", "Earnings report", reported, SOURCE_RULE)
            catalysts.append(earnings)
            record(f"Earnings report added as a catalyst: {reported.isoformat()}.")
        elif earnings is not None and earnings.get("date") != reported.isoformat():
            record(f"Earnings date changed from {earnings.get('date') or 'unknown'} to {reported.isoformat()}.")
            earnings["date"] = reported.isoformat()
            earnings["alerted"] = False
    if earnings is not None and earnings.get("date") and not earnings.get("alerted"):
        days = (date.fromisoformat(earnings["date"]) - today).days
        if 0 <= days <= EARNINGS_ALERT_DAYS:
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            record(f"Catalyst alert: earnings {when} ({earnings['date']}); an earnings gap can jump past the stop.")
            earnings["alerted"] = True
    return changes


def _fetch_reads(position: PaperPosition, data_provider: DataProvider) -> MarketReads | None:
    """Fresh price history for a re-check, or None when the daily history cannot be had (then
    nothing is re-judged: a thesis is never marked broken from missing or stale data)."""
    symbol = position.symbol
    with fresh_data_only():
        try:
            daily_bars = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
            daily = analyze_chart(daily_bars)
        except Exception as exc:  # noqa: BLE001 - any failure means no re-check this time
            logger.info("Thesis re-check for %s skipped: daily bars unavailable: %s", symbol, exc)
            return None
        try:
            price = float(data_provider.get_quote(symbol).price)
        except Exception:  # noqa: BLE001 - the last close is a fine stand-in
            price = daily.price
        try:
            weekly: ChartAnalysis | None = analyze_chart(data_provider.get_ohlcv(symbol, period="2y", interval="1wk"))
        except Exception:  # noqa: BLE001
            weekly = None
        market: ChartAnalysis | None = None
        if symbol != "SPY":
            try:
                market = analyze_chart(data_provider.get_ohlcv("SPY", period="1y", interval="1d"))
            except Exception:  # noqa: BLE001
                market = None
        earnings_date = _safe_earnings_date(data_provider, symbol)
    return MarketReads(
        price=price, atr=latest_atr(daily_bars, ATR_PERIOD), daily=daily, weekly=weekly, market=market, earnings_date=earnings_date
    )


def recheck_thesis(
    session: Session,
    position: PaperPosition,
    data_provider: DataProvider,
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> RecheckOutcome:
    """Seed the thesis if needed, then re-judge it against fresh data and store the result.
    Never raises for a data failure (that is a "skipped" outcome) and never touches the position."""
    if position.status != "open":
        return RecheckOutcome("not_open", note="Only an open position has a thesis to re-check.")
    when = now or utcnow_naive()
    thesis = seed_thesis(session, position, data_provider, settings, now=when)
    reads = _fetch_reads(position, data_provider)
    if reads is None:
        return RecheckOutcome("skipped", note="Fresh price history could not be fetched, so nothing was re-checked.")

    pillars, catalysts, log = _load(thesis.pillars), _load(thesis.catalysts), _load(thesis.log)
    changes = apply_recheck(pillars, catalysts, log, position, reads, now=when, window_days=holding_window_days(settings))

    broken_now = any(p.get("core") and p.get("status") == PILLAR_BROKEN for p in pillars)
    was_broken = thesis.thesis_broken
    alert_sent = False
    if broken_now and not was_broken:
        thesis.broken_at = when
        note = "Thesis broken: a core pillar is broken."
        log.append({"at": _iso(when), "kind": LOG_SYSTEM, "text": note})
        changes.append(note)
    elif was_broken and not broken_now:
        note = "Thesis no longer broken: the core pillar recovered."
        log.append({"at": _iso(when), "kind": LOG_SYSTEM, "text": note})
        changes.append(note)
    thesis.thesis_broken = broken_now

    thesis.pillars, thesis.catalysts, thesis.log = _dump(pillars), _dump(catalysts), _dump(log[-MAX_LOG_ENTRIES:])
    thesis.last_checked_at = when
    thesis.updated_at = when

    # One alert per thesis, the first time it breaks. The stamp is saved with the same commit as
    # the state, so a crash between the two can at worst skip an alert, never repeat one.
    if broken_now and thesis.broken_alerted_at is None:
        thesis.broken_alerted_at = when
        alert_sent = True
    session.add(thesis)
    session.commit()
    session.refresh(thesis)

    if alert_sent and settings.thesis_alerts:
        core = next((p for p in pillars if p.get("core")), {})
        notify(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            f"Thesis broken: {position.symbol} ({position.direction}). {core.get('text', 'The core pillar')} "
            f"is no longer true ({core.get('detail') or 'see the Portfolio page'}).",
        )
    return RecheckOutcome("checked", changes=changes, alert_sent=alert_sent and settings.thesis_alerts)


def run_thesis_sweep(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    *,
    now: datetime | None = None,
    min_interval: timedelta = THESIS_RECHECK_MIN_INTERVAL,
    recheck: bool = True,
) -> list[RecheckOutcome]:
    """One pass over every open position: seed a missing thesis, and (when `recheck`) re-check each
    one that has not been checked within `min_interval`. The caller turns `recheck` off outside the
    trading session, when no new bar can exist but a new position still deserves its thesis. Does
    nothing inside a simulated moment. Each position is isolated, so one failure never stops the
    others."""
    if is_simulated():
        return []
    when = now or utcnow_naive()
    outcomes: list[RecheckOutcome] = []
    for position in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all():
        try:
            thesis = seed_thesis(session, position, data_provider, settings, now=when)
            if not recheck or (thesis.last_checked_at is not None and when - thesis.last_checked_at < min_interval):
                continue
            outcomes.append(recheck_thesis(session, position, data_provider, settings, now=when))
        except Exception:  # noqa: BLE001 - one position's failure must not stop the sweep
            logger.exception("Thesis sweep failed for position %s", position.id)
            session.rollback()
    return outcomes


# --------------------------------------------------------------------- editing


def _touch(session: Session, thesis: ThesisRecord, now: datetime) -> None:
    thesis.updated_at = now
    session.add(thesis)
    session.commit()
    session.refresh(thesis)


def add_note(session: Session, thesis: ThesisRecord, text: str, *, now: datetime | None = None) -> ThesisRecord:
    when = now or utcnow_naive()
    log = _load(thesis.log)
    log.append({"at": _iso(when), "kind": LOG_NOTE, "text": _clean(text, MAX_NOTE_TEXT_CHARS)})
    thesis.log = _dump(log[-MAX_LOG_ENTRIES:])
    _touch(session, thesis, when)
    return thesis


def add_item(
    session: Session,
    thesis: ThesisRecord,
    kind: str,
    text: str,
    *,
    when_date: str | None = None,
    status: str | None = None,
    now: datetime | None = None,
) -> ThesisRecord:
    """Add a user pillar, risk or catalyst. A catalyst may carry an ISO date."""
    if kind not in ITEM_KINDS:
        raise ThesisEditError(f"Unknown item kind: {kind}")
    when = now or utcnow_naive()
    cleaned = _clean(text, MAX_ITEM_TEXT_CHARS)
    column = {KIND_PILLAR: "pillars", KIND_RISK: "risks", KIND_CATALYST: "catalysts"}[kind]
    items = _load(getattr(thesis, column))
    if len(items) >= MAX_ITEMS_PER_LIST:
        raise ThesisEditError(f"A thesis holds at most {MAX_ITEMS_PER_LIST} items of each kind.")
    if kind == KIND_PILLAR:
        if status is not None and status not in PILLAR_STATUSES:
            raise ThesisEditError(f"Unknown pillar status: {status}")
        items.append(_pillar(KEY_USER, cleaned, SOURCE_USER, status=status or PILLAR_INTACT))
    elif kind == KIND_RISK:
        items.append({"id": _new_id(), "text": cleaned, "source": SOURCE_USER})
    else:
        parsed: date | None = None
        if when_date:
            try:
                parsed = date.fromisoformat(when_date)
            except ValueError as exc:
                raise ThesisEditError("A catalyst date must look like 2026-10-28.") from exc
        items.append(_catalyst("user", cleaned, parsed, SOURCE_USER))
    setattr(thesis, column, _dump(items))
    _touch(session, thesis, when)
    return thesis


def _find_item(thesis: ThesisRecord, item_id: str) -> tuple[str, list[dict], dict]:
    for column in ("pillars", "risks", "catalysts"):
        items = _load(getattr(thesis, column))
        for item in items:
            if item.get("id") == item_id:
                return column, items, item
    raise ThesisEditError("That item does not exist.")


def remove_item(session: Session, thesis: ThesisRecord, item_id: str, *, now: datetime | None = None) -> ThesisRecord:
    column, items, item = _find_item(thesis, item_id)
    if item.get("core"):
        raise ThesisEditError("The core trend pillar cannot be removed: it drives the thesis-broken warning.")
    setattr(thesis, column, _dump([i for i in items if i is not item]))
    _touch(session, thesis, now or utcnow_naive())
    return thesis


def set_pillar_status(
    session: Session, thesis: ThesisRecord, item_id: str, status: str, *, now: datetime | None = None
) -> ThesisRecord:
    """Change the status of a pillar the rules do not judge (a user pillar or a plan reason).
    A pillar a rule re-checks is not editable: the next re-check would overwrite the edit."""
    if status not in PILLAR_STATUSES:
        raise ThesisEditError(f"Unknown pillar status: {status}")
    column, items, item = _find_item(thesis, item_id)
    if column != "pillars":
        raise ThesisEditError("Only a pillar has a status.")
    if item.get("key") in MECHANICAL_KEYS:
        raise ThesisEditError("This pillar is re-checked from price data automatically, so it cannot be set by hand.")
    when = now or utcnow_naive()
    old = item.get("status", PILLAR_INTACT)
    item["status"] = status
    log = _load(thesis.log)
    log.append({"at": _iso(when), "kind": LOG_SYSTEM, "text": f"You marked \"{item['text']}\" {STATUS_LABELS[status]} (was {STATUS_LABELS.get(old, old)})."})
    thesis.log = _dump(log[-MAX_LOG_ENTRIES:])
    thesis.pillars = _dump(items)
    _touch(session, thesis, when)
    return thesis


# ------------------------------------------------------------------ the AI review


@dataclass(frozen=True)
class ReviewOutcome:
    """`status`: "written", "failed" (recorded in review_error) or "skipped" (nothing attempted)."""

    status: str
    detail: str = ""


def _days_phrase(day_text: str | None, today: date) -> str:
    if not day_text:
        return "no date"
    days = (date.fromisoformat(day_text) - today).days
    if days < 0:
        return f"{day_text}, passed {-days} day{'s' if days != -1 else ''} ago"
    return f"{day_text}, {days} day{'s' if days != 1 else ''} away"


def build_review_facts(position: PaperPosition, thesis: ThesisRecord, *, now: datetime) -> ThesisReviewFacts:
    today = _market_today(now)
    held = max(0, (now.date() - position.opened_at.date()).days)
    facts = [
        f"Symbol: {position.symbol}",
        f"Direction: {position.direction}" + (" (profits when the price falls)" if position.direction == "short" else ""),
        f"Entered {position.opened_at.date().isoformat()} at ${position.entry_price:,.2f}; held {held} calendar days",
        f"Stop ${position.stop_loss:,.2f}, first target ${position.tp1:,.2f}, second target ${position.tp2:,.2f}",
        f"Thesis broken warning: {'ON' if thesis.thesis_broken else 'off'}",
        f"Last rule re-check: {thesis.last_checked_at.isoformat() + ' UTC' if thesis.last_checked_at else 'never'}",
    ]
    pillar_lines = []
    for p in _load(thesis.pillars):
        line = f"[{STATUS_LABELS.get(p.get('status', ''), p.get('status'))}] {untrusted_text(p.get('text', ''))}"
        if p.get("detail"):
            line += f" (last check: {untrusted_text(p['detail'])})"
        pillar_lines.append(line)
    return ThesisReviewFacts(
        fact_lines=facts,
        pillar_lines=pillar_lines,
        risk_lines=[untrusted_text(r.get("text", "")) for r in _load(thesis.risks)],
        catalyst_lines=[f"{untrusted_text(c.get('text', ''))} ({_days_phrase(c.get('date'), today)})" for c in _load(thesis.catalysts)],
        log_lines=[
            f"{e.get('at', '')[:10]} [{e.get('kind', '')}] {untrusted_text(e.get('text', ''))}"
            for e in _load(thesis.log)[-REVIEW_LOG_ENTRIES:]
        ],
    )


def _tidy_review(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= REVIEW_MAX_CHARS:
        return flat
    cut = flat[:REVIEW_MAX_CHARS]
    last_stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: last_stop + 1] if last_stop > 0 else cut.rstrip() + "…"


def generate_thesis_review(
    session: Session,
    position: PaperPosition,
    thesis: ThesisRecord,
    llm_provider: LLMProvider,
    *,
    now: datetime | None = None,
) -> ReviewOutcome:
    """Write (or rewrite) the AI review paragraph for one thesis and store it. Never raises.

    The caller checks that a real AI provider is configured. A failure stores only
    `review_error` and leaves any earlier review text alone; there is no template fallback (an
    empty fallback is passed on purpose, so a failed call reads back as a failure instead of
    rule text posing as a model's review)."""
    when = now or utcnow_naive()
    try:
        prompt = build_thesis_review_prompt(build_review_facts(position, thesis, now=when))
        result = generate_with_fallback(llm_provider, prompt, "", tier=ROUTINE_TIER)
        text = _tidy_review(result.text) if result.provider != "none" else ""
        error = None if text else _short(result.error or "The AI returned an empty answer", REVIEW_ERROR_MAX_CHARS)
    except Exception as exc:  # noqa: BLE001 - a review is best effort, whatever went wrong
        logger.warning("Thesis review for position %s failed", position.id, exc_info=True)
        text, result, error = "", None, _short(f"{type(exc).__name__}: {exc}", REVIEW_ERROR_MAX_CHARS)

    thesis.review_at = when
    if text and result is not None:
        thesis.review_text = text
        thesis.review_provider = result.provider
        thesis.review_model = result.model
        thesis.review_error = None
    else:
        thesis.review_error = error
    thesis.updated_at = when
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    return ReviewOutcome("written" if text else "failed", "" if text else (error or ""))


# ----------------------------------------------------------------- the response


def thesis_schema(thesis: ThesisRecord, *, today: date | None = None) -> ThesisSchema:
    """The API form of a thesis, with each catalyst's countdown worked out for `today`."""
    day = today or _market_today(utcnow_naive())
    catalysts = []
    for c in _load(thesis.catalysts):
        days = (date.fromisoformat(c["date"]) - day).days if c.get("date") else None
        catalysts.append(
            ThesisCatalystSchema(
                id=c.get("id", ""), key=c.get("key", ""), text=c.get("text", ""), date=c.get("date"), days_until=days, source=c.get("source", "")
            )
        )
    return ThesisSchema(
        id=thesis.id,
        position_id=thesis.position_id,
        trade_plan_id=thesis.trade_plan_id,
        symbol=thesis.symbol,
        direction=thesis.direction,
        pillars=[
            ThesisPillarSchema(
                id=p.get("id", ""), key=p.get("key", ""), text=p.get("text", ""), status=p.get("status", PILLAR_INTACT),
                source=p.get("source", ""), core=bool(p.get("core")), detail=p.get("detail"), checked_at=p.get("checked_at"),
            )
            for p in _load(thesis.pillars)
        ],
        risks=[ThesisRiskSchema(id=r.get("id", ""), text=r.get("text", ""), source=r.get("source", "")) for r in _load(thesis.risks)],
        catalysts=catalysts,
        log=[ThesisLogEntrySchema(at=e.get("at", ""), kind=e.get("kind", LOG_SYSTEM), text=e.get("text", "")) for e in _load(thesis.log)],
        thesis_broken=thesis.thesis_broken,
        broken_at=thesis.broken_at,
        last_checked_at=thesis.last_checked_at,
        review_text=thesis.review_text,
        review_at=thesis.review_at,
        review_provider=thesis.review_provider,
        review_model=thesis.review_model,
        review_error=thesis.review_error,
        created_at=thesis.created_at,
        updated_at=thesis.updated_at,
    )

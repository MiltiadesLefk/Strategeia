"""Two silent signals from big holders (see analysis/shadow_signals.py).

`fund_accumulation`: what the stored 13F filings of followed funds say about
this symbol at their latest quarter end. A fund that newly holds it or holds
more than the quarter before counts as buying; one that holds less or has sold
out counts as selling. The signal is the balance of the two.

  * Direction-signed like the app's other confluence checks: net buying supports
    a long (+1) and argues against a short (-1); net selling does the reverse.
  * A 13F is a snapshot of a quarter's last day, published up to 45 days later,
    so even a fresh filing describes the past. A filing older than
    FUND_SIGNAL_MAX_AGE_DAYS (the next quarter's filing is then due) is not read.
  * Reads every fund with stored filings: filings are only ever loaded for the
    funds being followed, so that is the followed list.

`ownership_5pct_filing`: a NEW Schedule 13D (a holder crossing 5% and saying it
may want to influence the company) filed in the last 30 days. Only the original
filing counts: an amendment (13D/A) can as easily report a sale as a purchase,
and a Schedule 13G is the routine passive form that index managers file for
nearly every large company. The original 13D is a stake being built, so it is
signed by direction (+1 for a long, -1 for a short), capped at one point.

Both are recorded on every plan and never added to confidence until a backtest
shows they beat luck. No stored data at all is reported as unavailable (never
loaded), which is different from "loaded, and nothing found".
"""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session

from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
from app.knowledge import FactKind, current_as_of, facts_known_as_of
from app.knowledge.fund_holdings import (
    STATUS_ADDED,
    STATUS_NEW,
    STATUS_SOLD_OUT,
    STATUS_TRIMMED,
    fund_holders_of_symbol,
    ownership_filings_as_of,
)

FUNDS_SIGNAL_NAME = "fund_accumulation"
OWNERSHIP_SIGNAL_NAME = "ownership_5pct_filing"
# Largest swing either signal would add in either direction.
FUND_SCORE_CAP = 2
OWNERSHIP_SCORE_CAP = 2
# A net of this many followed funds buying (or selling) is the strong reading: the second point.
STRONG_NET_FUNDS = 3
# How far back a stake change still counts, and how far back its earlier filings are looked up for comparison.
OWNERSHIP_CHANGE_DAYS = 90
OWNERSHIP_HISTORY_DAYS = 365
# A holder's stake moving by at least this many percentage points is a real buy or sell, not rounding.
MIN_STAKE_CHANGE_PP = 1.0
# Falling under this share is leaving the 5% club altogether.
OWNERSHIP_REPORTING_FLOOR_PCT = 5.0
# A filing older than this is the previous quarter's picture: the next one is due.
FUND_SIGNAL_MAX_AGE_DAYS = 120
# A new 13D counts as news for this long.
OWNERSHIP_WINDOW_DAYS = 30

_BUYING = frozenset({STATUS_NEW, STATUS_ADDED})
_SELLING = frozenset({STATUS_TRIMMED, STATUS_SOLD_OUT})


def _sign(direction: str | None, net: int) -> int:
    if direction not in ("long", "short") or net == 0:
        return 0
    positive = net > 0
    return 1 if positive == (direction == "long") else -1


def build_fund_accumulation_signal(direction: str | None, session: Session | None, symbol: str) -> ShadowSignal:
    name = FUNDS_SIGNAL_NAME
    if session is None:
        return ShadowSignal(name, None, 0, "No database session to read fund filings from.", available=False)
    if not facts_known_as_of(session, FactKind.FUND_FILING, limit=1):
        return ShadowSignal(name, None, 0, "No fund filings stored yet (load them on the Smart Money page).", available=False)
    cutoff = current_as_of() - timedelta(days=FUND_SIGNAL_MAX_AGE_DAYS)
    holders = [h for h in fund_holders_of_symbol(session, symbol) if h.filed_at >= cutoff]
    if not holders:
        return ShadowSignal(
            name, "none", 0, f"No followed fund reported a position in {symbol} in a filing from the last {FUND_SIGNAL_MAX_AGE_DAYS} days.", available=True
        )
    buying = sum(1 for h in holders if h.status in _BUYING)
    selling = sum(1 for h in holders if h.status in _SELLING)
    net = buying - selling
    value = f"{buying} buying, {selling} selling"
    if direction not in ("long", "short"):
        return ShadowSignal(name, value, 0, f"{value} among followed funds, but there is no clear direction to sign it by.", available=True)
    points = _sign(direction, net) * (FUND_SCORE_CAP if abs(net) >= STRONG_NET_FUNDS else 1)
    if net == 0:
        reason = f"Followed funds are split ({value}) for {symbol} at their latest quarter end."
    else:
        lean = "buying" if net > 0 else "selling"
        verb = "supports" if points > 0 else "argues against"
        reason = f"Net fund {lean} ({value}) at the latest quarter end {verb} a {direction}; 13F data is up to 45 days late."
    return ShadowSignal(name, value, points, reason, available=True)


def ownership_net_change(records: list, now) -> tuple[int, list[str]]:
    """The net buying (+) or selling (-) by 5% holders in the last OWNERSHIP_CHANGE_DAYS, from their Schedule 13D and
    13G filings, and one line per move. Per filer, oldest first:
      * a new Schedule 13D (an activist crossing 5% and saying it may want influence): +2
      * a later filing that shows a higher stake (at least MIN_STAKE_CHANGE_PP more): +1
      * a later filing that shows a lower stake (at least that much less): -1
      * a later filing that shows the stake under the 5% floor (the holder has left): -2
    A new Schedule 13G is the routine passive form and does not move it."""
    cutoff = now - timedelta(days=OWNERSHIP_CHANGE_DAYS)
    by_filer: dict[str, list] = {}
    for record in records:
        by_filer.setdefault(record.filer_cik or record.filer_name or record.accession, []).append(record)
    net = 0
    lines: list[str] = []
    for filings in by_filer.values():
        filings.sort(key=lambda r: r.known_at)
        for index, record in enumerate(filings):
            if record.known_at < cutoff:
                continue
            who = record.filer_name or "a holder"
            previous = filings[index - 1] if index > 0 else None
            if not record.is_amendment and record.schedule == "13D":
                net += 2
                pct = f" ({record.percent:g}%)" if record.percent is not None else ""
                lines.append(f"{who} filed a new Schedule 13D{pct}")
            elif record.percent is not None and record.percent < OWNERSHIP_REPORTING_FLOOR_PCT and (previous is not None or record.is_amendment):
                net -= 2
                lines.append(f"{who} now reports {record.percent:g}%, under the {OWNERSHIP_REPORTING_FLOOR_PCT:g}% line")
            elif previous is not None and previous.percent is not None and record.percent is not None:
                delta = record.percent - previous.percent
                if delta >= MIN_STAKE_CHANGE_PP:
                    net += 1
                    lines.append(f"{who} raised its stake from {previous.percent:g}% to {record.percent:g}%")
                elif delta <= -MIN_STAKE_CHANGE_PP:
                    net -= 1
                    lines.append(f"{who} cut its stake from {previous.percent:g}% to {record.percent:g}%")
    return net, lines


def build_ownership_signal(direction: str | None, session: Session | None, symbol: str) -> ShadowSignal:
    name = OWNERSHIP_SIGNAL_NAME
    if session is None:
        return ShadowSignal(name, None, 0, "No database session to read ownership filings from.", available=False)
    if not facts_known_as_of(session, FactKind.OWNERSHIP_FILING, symbol=symbol, limit=1):
        return ShadowSignal(name, None, 0, "No 13D/13G filings stored for this symbol.", available=False)
    records = ownership_filings_as_of(session, symbol, window_days=OWNERSHIP_HISTORY_DAYS)
    net, lines = ownership_net_change(records, current_as_of())
    if not lines:
        return ShadowSignal(name, "none", 0, f"No 5% holder bought or sold in the last {OWNERSHIP_CHANGE_DAYS} days.", available=True)
    value = f"net {net:+d}: " + "; ".join(lines[:3])
    if direction not in ("long", "short") or net == 0:
        return ShadowSignal(name, value, 0, f"5% holders: {'; '.join(lines[:3])}. No clear direction to sign it by or it nets out.", available=True)
    buying = net > 0
    supports = buying == (direction == "long")
    points = max(-OWNERSHIP_SCORE_CAP, min(OWNERSHIP_SCORE_CAP, abs(net))) * (1 if supports else -1)
    verb = "supports" if supports else "argues against"
    lean = "buying" if buying else "selling"
    return ShadowSignal(name, value, points, f"5% holders are net {lean} ({'; '.join(lines[:3])}), which {verb} a {direction}.", available=True)


@shadow_signal(FUNDS_SIGNAL_NAME)
def _fund_accumulation_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_fund_accumulation_signal(context.direction, context.session, context.symbol)


@shadow_signal(OWNERSHIP_SIGNAL_NAME)
def _ownership_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_ownership_signal(context.direction, context.session, context.symbol)

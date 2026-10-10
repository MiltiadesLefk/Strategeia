"""Net catalyst points for one symbol at one moment, from facts public at that moment.

Each source kind gives a signed, capped amount; items older than `window_days` stop counting. Everything is read
through the "known at" readers, so inside `with as_of(t):` nothing later than t can leak in. A kind with no stored
data at all is reported as uncovered instead of scored as "quiet": 0 points from silence and 0 points from no data
are different things (the same rule the live evidence parts follow).

Not covered in this first version, on purpose: headline news (no free historical archive; it only exists from the
day the app started storing it), Trump's own purchases (no feed), 13F / 13D holders, and Senate filings.
Trump *posts* are not scored: whether a mention is good news cannot be read by rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from app.analysis.filing_8k_scoring import NEGATIVE_LEANING_ITEMS
from app.backtest.earnings_history import earnings_history_as_of
from app.data_providers.sec_8k import filings_8k_as_of, has_8k_data
from app.knowledge import FactKind, current_as_of, facts_known_as_of, to_naive_utc
from app.knowledge.congress_trades import (
    EQUITY_ASSET_TYPES,
    congress_trades_as_of,
    has_congress_data,
    net_buyers_as_of,
)
from app.knowledge.insider_trades import insider_trades_as_of

KIND_INSIDER = "insider"
KIND_CONGRESS = "congress"
KIND_8K = "filing_8k"
KIND_EARNINGS = "earnings"
ALL_KINDS = (KIND_INSIDER, KIND_CONGRESS, KIND_8K, KIND_EARNINGS)

DEFAULT_WINDOW_DAYS = 5
DEFAULT_KIND_CAP = 2
# A reported EPS surprise smaller than this (in percent) is noise, not a catalyst.
EARNINGS_SURPRISE_THRESHOLD_PCT = 5.0
# Reports a few days old still count as news; older than the window they are history.


@dataclass(frozen=True)
class CatalystItem:
    kind: str
    points: int  # before the per-kind cap
    text: str  # the exact item: who, what, when, how much
    why: str = ""  # how the points were decided


@dataclass
class CatalystReading:
    symbol: str
    window_days: int
    items: list[CatalystItem] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)  # capped points per kind
    covered: dict[str, bool] = field(default_factory=dict)  # did this kind have any stored data at all

    @property
    def net(self) -> int:
        return sum(self.by_kind.values())


def _cap(points: int, cap: int) -> int:
    return max(-cap, min(cap, points))


def read_catalysts(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    kind_cap: int = DEFAULT_KIND_CAP,
) -> CatalystReading:
    """The catalyst reading of `symbol` as it stood at `as_of` (default: the simulated moment, else now)."""
    as_of = to_naive_utc(as_of) if as_of is not None else current_as_of()
    symbol = symbol.strip().upper()
    out = CatalystReading(symbol=symbol, window_days=window_days)

    def add(kind: str, points: int, text: str, why: str = "") -> None:
        out.items.append(CatalystItem(kind, points, text, why))

    # Insiders: distinct people buying on the open market (a scheduled-plan buy is still their money, but a
    # plan is not news, so 10b5-1 rows are left out).
    out.covered[KIND_INSIDER] = bool(
        facts_known_as_of(session, FactKind.INSIDER_TRADE, as_of=as_of, symbol=symbol, limit=1)
    )
    if out.covered[KIND_INSIDER]:
        buys = insider_trades_as_of(session, symbol, as_of, window_days, include_10b5_1=False)
        buyers = {t.owner_key or t.owner_name for t in buys}
        if buyers:
            lines = []
            for t in buys:
                what = f"{t.shares:,.0f} sh" if t.shares else "shares"
                price = f" at ${t.price:,.2f}" if t.price else ""
                lines.append(
                    f"{t.owner_name or 'an insider'} ({t.officer_title or ', '.join(t.roles) or 'insider'}) bought {what}{price}"
                    f", trade {t.transaction_date}, filed {t.known_at:%Y-%m-%d}"
                )
            add(
                KIND_INSIDER, min(len(buyers), kind_cap), " | ".join(lines),
                f"+1 per distinct insider buying on the open market (Form 4 code P, not a pre-arranged plan), max +{kind_cap}",
            )

    # Congress (House reports): distinct members, net of sellers. One member counts, unlike the live score.
    out.covered[KIND_CONGRESS] = has_congress_data(session, as_of)
    if out.covered[KIND_CONGRESS]:
        net = net_buyers_as_of(session, symbol, as_of, window_days)
        if net.net != 0:
            rows = congress_trades_as_of(session, symbol, as_of, window_days, asset_types=EQUITY_ASSET_TYPES)
            lines = [
                f"{t.member} ({t.state_district or '?'}, {t.owner}) {t.type} {t.amount_text}, trade {t.trade_date}, filed {t.filed_date}"
                for t in rows
                if t.side in ("buy", "sell")
            ]
            add(
                KIND_CONGRESS, net.net, " | ".join(lines),
                f"+1 per distinct member buying minus 1 per member selling, in reports filed in the window "
                f"({net.buyers} bought, {net.sellers} sold); max +-{kind_cap}. The report is filed up to 45 days after the trade",
            )

    # 8-K: negative-leaning items only; good 8-K news cannot be told from routine by item code.
    out.covered[KIND_8K] = has_8k_data(session, symbol, as_of)
    if out.covered[KIND_8K]:
        filings = filings_8k_as_of(session, symbol, as_of, window_days)
        codes = sorted({c for f in filings for c in f.items if c in NEGATIVE_LEANING_ITEMS})
        if codes:
            lines = [
                f"8-K filed {f.known_at:%Y-%m-%d}: " + "; ".join(f"item {c} {t}" for c, t in zip(f.items, f.item_titles))
                for f in filings
                if any(c in NEGATIVE_LEANING_ITEMS for c in f.items)
            ]
            add(
                KIND_8K, -len(codes), " | ".join(lines),
                "-1 per distinct negative-leaning item code (1.03 bankruptcy, 2.06 impairment, 3.01 delisting, "
                "4.02 restatement, 5.02 executive change); good 8-K news cannot be told from routine by code",
            )

    # Earnings: the newest report inside the window, beat or miss against consensus.
    history = earnings_history_as_of(session, symbol, as_of)
    out.covered[KIND_EARNINGS] = bool(history)
    if history:
        latest = history[0]
        age = (as_of.date() - latest.date).days
        s = latest.surprise_pct
        if 0 <= age <= window_days and s is not None and abs(s) >= EARNINGS_SURPRISE_THRESHOLD_PCT:
            add(
                KIND_EARNINGS, 1 if s > 0 else -1,
                f"reported {latest.date}: EPS {latest.eps_actual} against consensus {latest.eps_estimate} ({s:+.0f}%)",
                f"+1 for a beat, -1 for a miss, only when the surprise is {EARNINGS_SURPRISE_THRESHOLD_PCT:.0f}% or more "
                "and the report is inside the window",
            )

    for kind in ALL_KINDS:
        points = sum(i.points for i in out.items if i.kind == kind)
        if points:
            out.by_kind[kind] = _cap(points, kind_cap)
    return out

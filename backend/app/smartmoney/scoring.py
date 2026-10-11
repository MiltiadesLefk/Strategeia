"""The smart-money score (NEW_PLAN.md section 5). Pure: no I/O, no clock, no settings.

Input is dated facts the caller already read through the "known at" readers; output is a
total and a list of plain-English components that the candidate record stores. The weights
are starting values, written down, NOT validated on data: do not describe the score as
proven anywhere. Anything filed after `now` is ignored here as a second look-ahead guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.analysis.filing_8k_scoring import NEGATIVE_LEANING_ITEMS

# Window and recency
WINDOW_DAYS = 30
RECENT_DAYS = 7
OLD_RECENCY_FACTOR = 0.5

# Insiders (Form 4 open-market purchases by officers/directors)
INSIDER_SCORE_CAP = 10.0
INSIDER_PER_BUYER = 1.0
INSIDER_CEO_CFO_EXTRA = 1.0
INSIDER_SIZE_TIERS = ((1_000_000.0, 3.0), (500_000.0, 2.0), (100_000.0, 1.0))  # highest tier only
INSIDER_CLUSTER_MIN_BUYERS = 3
INSIDER_CLUSTER_BONUS = 2.0
# NEXT BUILD: insider sells (user decision 2026-10-11). Not scored in this build: sells are
# mostly pay/tax/plans. Do not add a sell-scoring setting until that build.

# House members (bracket lower bounds as printed by the Clerk: 1,001 / 15,001 / 100,001 / 500,001)
HOUSE_SCORE_CAP = 6.0
HOUSE_BRACKET_POINTS = ((500_001.0, 3.0), (100_001.0, 2.0), (15_001.0, 1.0))  # below the last: ignored
HOUSE_EXTRA_MEMBER_POINTS = 1.0
HOUSE_SELLER_POINTS = -1.0

# 5% holders
OWNERSHIP_13D_POINTS = 3.0
OWNERSHIP_13G_POINTS = 1.0  # amendments: 0

# Funds (13F, quarterly, stale by construction)
FUND_NEW_POINTS = 2.0
FUND_ADD_POINTS = 1.0
FUND_SCORE_CAP = 3.0

# Negatives
NEGATIVE_8K_POINTS = -1.0

# Candidate rules
SMART_MONEY_MIN_SCORE = 3
REFLAG_DAYS = 30
REFLAG_SCORE_RISE = 2
LIQUIDITY_MIN_PRICE = 3.0
LIQUIDITY_MIN_DOLLAR_VOLUME = 1_000_000
REVIEW_EXPIRY_DAYS = 14


@dataclass
class InsiderBuy:
    owner_key: str
    family_key: str
    is_ceo_or_cfo: bool
    is_officer_or_director: bool
    usd: float
    filed_at: datetime
    is_10b5_1: bool = False


@dataclass
class HouseTrade:
    member: str
    kind: str  # "buy" | "sell"
    bracket_low: float
    bracket_high: float | None
    filed_at: datetime
    followed: bool = False


@dataclass
class FundChange:
    fund: str
    kind: str  # "new" | "add"
    filed_at: datetime


@dataclass
class OwnershipFiling:
    form: str  # "13D" | "13G"
    is_amendment: bool
    filed_at: datetime


@dataclass
class Filing8K:
    item_codes: list[str]
    filed_at: datetime


@dataclass
class ScoreComponent:
    source: str
    points: float
    reason: str


@dataclass
class ScoreResult:
    total: float
    components: list[ScoreComponent] = field(default_factory=list)


def _factor(now: datetime, filed_at: datetime) -> float | None:
    """Recency factor, or None when the filing is outside the window or not yet known."""
    age = now - filed_at
    if age < timedelta(0) or age > timedelta(days=WINDOW_DAYS):
        return None
    return 1.0 if age <= timedelta(days=RECENT_DAYS) else OLD_RECENCY_FACTOR


def _usd(value: float) -> str:
    return f"${value:,.0f}"


def _cap(comps: list[ScoreComponent], source: str, cap: float) -> list[ScoreComponent]:
    total = sum(c.points for c in comps)
    if total > cap:
        comps.append(ScoreComponent(source, cap - total, f"{source} source capped at +{cap:g}"))
    return comps


def _score_insiders(now: datetime, buys: list[InsiderBuy]) -> list[ScoreComponent]:
    families: dict[str, dict] = {}
    for b in buys:
        if b.is_10b5_1 or not b.is_officer_or_director or b.usd <= 0:
            continue
        f = _factor(now, b.filed_at)
        if f is None:
            continue
        fam = families.setdefault(b.family_key, {"usd": 0.0, "ceo": False, "factor": 0.0})
        fam["usd"] += b.usd
        fam["ceo"] = fam["ceo"] or b.is_ceo_or_cfo
        fam["factor"] = max(fam["factor"], f)
    if not families:
        return []
    comps: list[ScoreComponent] = []
    for key in sorted(families):
        fam = families[key]
        pts = INSIDER_PER_BUYER
        notes = ["officer/director bought"]
        if fam["ceo"]:
            pts += INSIDER_CEO_CFO_EXTRA
            notes.append("CEO/CFO")
        for floor, tier_pts in INSIDER_SIZE_TIERS:
            if fam["usd"] >= floor:
                pts += tier_pts
                notes.append(f"{_usd(fam['usd'])} in the window")
                break
        comps.append(ScoreComponent("insider", pts * fam["factor"], f"{key}: {', '.join(notes)}"))
    if len(families) >= INSIDER_CLUSTER_MIN_BUYERS:
        factor = max(f["factor"] for f in families.values())
        comps.append(ScoreComponent(
            "insider", INSIDER_CLUSTER_BONUS * factor,
            f"Cluster: {len(families)} distinct insiders bought (joint filings count once)",
        ))
    return _cap(comps, "insider", INSIDER_SCORE_CAP)


def _bracket_points(low: float) -> float:
    for floor, pts in HOUSE_BRACKET_POINTS:
        if low >= floor:
            return pts
    return 0.0


def _score_house(now: datetime, trades: list[HouseTrade], multiplier: float) -> list[ScoreComponent]:
    buyers: dict[str, dict] = {}
    sellers: dict[str, float] = {}
    for t in trades:
        f = _factor(now, t.filed_at)
        if f is None:
            continue
        if t.kind == "buy":
            pts = _bracket_points(t.bracket_low)
            if pts <= 0:
                continue  # smallest bracket is ignored and is not a distinct buyer
            cur = buyers.setdefault(t.member, {"pts": 0.0, "factor": 0.0, "followed": False})
            cur["pts"] = max(cur["pts"], pts)
            cur["factor"] = max(cur["factor"], f)
            cur["followed"] = cur["followed"] or t.followed
        elif t.kind == "sell":
            sellers[t.member] = max(sellers.get(t.member, 0.0), f)
    comps: list[ScoreComponent] = []
    for i, name in enumerate(sorted(buyers)):
        b = buyers[name]
        pts = b["pts"] + (HOUSE_EXTRA_MEMBER_POINTS if i > 0 else 0.0)
        note = f"{name} reported a buy (amount range as filed)"
        if b["followed"]:
            pts *= multiplier
            note += f", followed member x{multiplier:g}"
        comps.append(ScoreComponent("house", pts * b["factor"], note))
    for name in sorted(sellers):
        comps.append(ScoreComponent("house", HOUSE_SELLER_POINTS * sellers[name], f"{name} reported a sale (netted off)"))
    return _cap(comps, "house", HOUSE_SCORE_CAP)


def _score_ownership(now: datetime, filings: list[OwnershipFiling]) -> list[ScoreComponent]:
    comps: list[ScoreComponent] = []
    for o in filings:
        f = _factor(now, o.filed_at)
        if f is None or o.is_amendment:
            continue  # amendments are information only
        if o.form == "13D":
            comps.append(ScoreComponent("ownership", OWNERSHIP_13D_POINTS * f, "New 13D filing (5% holder)"))
        elif o.form == "13G":
            comps.append(ScoreComponent("ownership", OWNERSHIP_13G_POINTS * f, "New 13G filing (5% holder, passive)"))
    return comps


def _score_funds(now: datetime, changes: list[FundChange]) -> list[ScoreComponent]:
    best: dict[str, tuple[float, str]] = {}
    for c in changes:
        f = _factor(now, c.filed_at)
        base = FUND_NEW_POINTS if c.kind == "new" else FUND_ADD_POINTS if c.kind == "add" else 0.0
        if f is None or base <= 0:
            continue
        if c.fund not in best or base * f > best[c.fund][0]:
            best[c.fund] = (base * f, c.kind)
    comps = [
        ScoreComponent("fund", pts, f"{fund}: {'new position' if kind == 'new' else 'added to position'} (13F, as of quarter end)")
        for fund, (pts, kind) in sorted(best.items())
    ]
    return _cap(comps, "fund", FUND_SCORE_CAP)


def _score_8k(now: datetime, filings: list[Filing8K]) -> list[ScoreComponent]:
    comps: list[ScoreComponent] = []
    for fl in filings:
        f = _factor(now, fl.filed_at)
        if f is None:
            continue
        for code in sorted(set(fl.item_codes)):
            if code in NEGATIVE_LEANING_ITEMS:
                comps.append(ScoreComponent("filing_8k", NEGATIVE_8K_POINTS * f, f"8-K item {code} (negative-leaning)"))
    return comps


def score_smart_money(
    *,
    now: datetime,
    insider_buys: list[InsiderBuy],
    house_trades: list[HouseTrade],
    fund_changes: list[FundChange],
    ownership_filings: list[OwnershipFiling],
    filings_8k: list[Filing8K],
    followed_member_multiplier: float = 1.5,
) -> ScoreResult:
    comps: list[ScoreComponent] = []
    comps += _score_insiders(now, insider_buys)
    comps += _score_house(now, house_trades, followed_member_multiplier)
    comps += _score_ownership(now, ownership_filings)
    comps += _score_funds(now, fund_changes)
    comps += _score_8k(now, filings_8k)
    for c in comps:
        c.points = round(c.points, 2)
    return ScoreResult(total=round(sum(c.points for c in comps), 2), components=comps)


def should_reflag(prev_score: float | None, prev_flagged_at: datetime | None, new_score: float, now: datetime) -> bool:
    """A symbol flagged before is a new candidate only after REFLAG_DAYS, or once its score rose by REFLAG_SCORE_RISE."""
    if prev_flagged_at is None or prev_score is None:
        return True
    if now - prev_flagged_at >= timedelta(days=REFLAG_DAYS):
        return True
    return new_score - prev_score >= REFLAG_SCORE_RISE

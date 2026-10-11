"""The smart-money tracker's read side: facts in, score and candidate out (NEW_PLAN section 6, steps 2-3).

`smart_money_score_as_of` reads ONLY through the "known at" readers
(`insider_trades_as_of`, `congress_trades_as_of`, `fund_changes_as_of`,
`ownership_filings_as_of`, `filings_8k_as_of`), turns what they return into the
dataclasses of `smartmoney.scoring` and calls `score_smart_money`. It never
writes and never touches the network, so a fact that became known after the
cutoff cannot change the answer.

`flag_candidate` is the only writer here: it records a `SmartMoneyCandidate` when
the score reaches the bar and the re-flag guard allows it. It sets no
direction, size, stop or target and opens nothing; a candidate only says "this
symbol deserves the ordinary full evaluation" (a later phase).

Wording rule for anything shown to a person: these are REPORTED filings, with
the delay each source has (Form 4: up to 2 business days after the trade; House:
up to 45 days; 13F: as of a quarter end, up to 45 days after it). Never "smart
money knows".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.sec_13f import followed_fund_ciks, starter_fund_name
from app.data_providers.sec_8k import filings_8k_as_of
from app.knowledge import current_as_of
from app.knowledge.congress_trades import EQUITY_ASSET_TYPES, congress_trades_as_of, member_key
from app.knowledge.fund_holdings import STATUS_ADDED, STATUS_NEW, FundChangeSet, fund_changes_as_of, ownership_filings_as_of
from app.knowledge.insider_trades import OPEN_MARKET_BUY, InsiderTrade, insider_trades_as_of
from app.services.congress_service import followed_members
from app.smartmoney import scoring
from app.smartmoney.models import STATUS_EVALUATING, STATUS_EXPIRED, SmartMoneyCandidate
from app.smartmoney.models import STATUS_NEW as CANDIDATE_NEW
from app.smartmoney.scoring import ScoreResult, score_smart_money, should_reflag

# Facts are read over the scoring window; the scorer applies the recency factor itself.
WINDOW_DAYS = scoring.WINDOW_DAYS
# A candidate nobody has acted on after this many days is closed as expired (NEW_PLAN 6.7).
CANDIDATE_EXPIRY_DAYS = scoring.REVIEW_EXPIRY_DAYS

SCOPE_UNIVERSE = "universe"
SCOPE_MARKET_WIDE = "market_wide"

# What each source's delay is, shown beside its points ("reported", never "happened").
SOURCE_DELAY_NOTES = {
    "insider": "Form 4 filings: reported up to 2 business days after the trade.",
    "house": "House reports: filed up to 45 days after the trade; amounts are ranges.",
    "ownership": "13D/13G filings: dated by the day SEC accepted them.",
    "fund": "13F: long positions as of a quarter end, filed up to 45 days later.",
    "filing_8k": "8-K: filed within 4 business days of the event.",
}

_CEO_CFO_RE = re.compile(r"\b(ceo|cfo|chief executive|chief financial)\b", re.IGNORECASE)


@dataclass
class ScoringInputs:
    insider_buys: list[scoring.InsiderBuy] = field(default_factory=list)
    house_trades: list[scoring.HouseTrade] = field(default_factory=list)
    fund_changes: list[scoring.FundChange] = field(default_factory=list)
    ownership_filings: list[scoring.OwnershipFiling] = field(default_factory=list)
    filings_8k: list[scoring.Filing8K] = field(default_factory=list)

    def filed_times(self) -> dict[str, list[datetime]]:
        return {
            "insider": [b.filed_at for b in self.insider_buys],
            "house": [t.filed_at for t in self.house_trades],
            "fund": [c.filed_at for c in self.fund_changes],
            "ownership": [o.filed_at for o in self.ownership_filings],
            "filing_8k": [f.filed_at for f in self.filings_8k],
        }


@dataclass
class SymbolScore:
    symbol: str
    now: datetime
    result: ScoreResult
    inputs: ScoringInputs

    @property
    def first_trigger_known_at(self) -> datetime | None:
        """The earliest filing among the sources that earned points: the earliest a trader could have seen it."""
        positive = {c.source for c in self.result.components if c.points > 0}
        times = [t for source, ts in self.inputs.filed_times().items() if source in positive for t in ts]
        return min(times) if times else None


# --------------------------------------------------------------------------
# Facts -> scoring inputs
# --------------------------------------------------------------------------


def _owner_flags(payload: dict) -> tuple[str | None, bool, bool]:
    """(key, is_officer_or_director, is_ceo_or_cfo) of the filing person. Verified against
    `sec_form4._row_payload`: the primary owner's flags sit at the top of the payload and every
    reporting owner of a joint filing is in `owners`. A fund or trust listed first with the
    officer second is read through `owners`, so the officer is not lost. A 10%-owner-only filer
    is not eligible (it is a fund or a big holder, not an insider)."""
    primary_ok = bool(payload.get("is_officer") or payload.get("is_director"))
    title = payload.get("officer_title") or ""
    if primary_ok:
        return payload.get("owner_key"), True, bool(_CEO_CFO_RE.search(title))
    for owner in payload.get("owners") or []:
        if owner.get("is_officer") or owner.get("is_director"):
            key = owner.get("cik") or (owner.get("name") or "").strip().upper() or None
            return key, True, bool(_CEO_CFO_RE.search(owner.get("officer_title") or ""))
    return payload.get("owner_key"), False, False


def _family_key(trade: InsiderTrade, owner_key: str | None) -> str:
    """One buyer per joint filing: every owner of a joint filing (a person, their fund, a trust)
    is the same family. A single-owner filing is just that owner."""
    owners = trade.payload.get("owners") or []
    if len(owners) > 1:
        ids = sorted({(o.get("cik") or (o.get("name") or "").strip().upper()) for o in owners} - {""})
        if ids:
            return "joint:" + "|".join(ids)
    return owner_key or (trade.owner_name or "").strip().upper() or trade.accession


def _insider_buys_as_of(session: Session, symbol: str, as_of: datetime | None = None) -> list[scoring.InsiderBuy]:
    out: list[scoring.InsiderBuy] = []
    for trade in insider_trades_as_of(session, symbol, as_of, WINDOW_DAYS, codes=(OPEN_MARKET_BUY,), include_10b5_1=True):
        key, eligible, ceo = _owner_flags(trade.payload)
        key = key or trade.owner_key or (trade.owner_name or "").strip().upper() or trade.accession
        out.append(
            scoring.InsiderBuy(
                owner_key=key,
                family_key=_family_key(trade, key),
                is_ceo_or_cfo=ceo,
                is_officer_or_director=eligible,
                usd=float(trade.value or 0.0),
                filed_at=trade.known_at,
                is_10b5_1=trade.is_10b5_1,
            )
        )
    return out


def _house_trades_as_of(
    session: Session, symbol: str, settings: AppSettings, as_of: datetime | None = None
) -> list[scoring.HouseTrade]:
    followed = followed_members(settings)
    followed_keys = {member_key(m) for m in followed} if followed is not None else set()
    out: list[scoring.HouseTrade] = []
    for t in congress_trades_as_of(session, symbol, as_of, WINDOW_DAYS, asset_types=EQUITY_ASSET_TYPES):
        if t.side not in ("buy", "sell"):
            continue
        out.append(
            scoring.HouseTrade(
                member=t.member,
                kind=t.side,
                bracket_low=float(t.amount_low or 0),
                bracket_high=float(t.amount_high) if t.amount_high is not None else None,
                filed_at=t.known_at,
                followed=t.member_key in followed_keys,
            )
        )
    return out


def fund_sets_at(
    session: Session, settings: AppSettings, moment: datetime | None = None
) -> dict[int, tuple[str, FundChangeSet]]:
    """cik -> (label, change set) for each followed fund with a 13F known at the cutoff. Computed
    once per scoring pass: the change set covers the whole fund, not one symbol."""
    out: dict[int, tuple[str, FundChangeSet]] = {}
    for cik in followed_fund_ciks(settings.smart_money_followed_funds):
        change_set = fund_changes_as_of(session, cik, moment)
        if change_set is None:
            continue
        out[cik] = (change_set.manager or starter_fund_name(cik) or f"CIK {cik}", change_set)
    return out


def fund_changes_for_symbol(sets: dict[int, tuple[str, FundChangeSet]], symbol: str) -> list[scoring.FundChange]:
    out: list[scoring.FundChange] = []
    for label, change_set in sets.values():
        for c in change_set.changes:
            if c.symbol != symbol or c.share_type != "SH" or c.put_call is not None:
                continue
            if c.status == STATUS_NEW:
                out.append(scoring.FundChange(fund=label, kind="new", filed_at=change_set.filed_at))
            elif c.status == STATUS_ADDED:
                out.append(scoring.FundChange(fund=label, kind="add", filed_at=change_set.filed_at))
    return out


def _gather_inputs(
    session: Session,
    symbol: str,
    settings: AppSettings,
    as_of: datetime | None = None,
    *,
    fund_sets: dict[int, tuple[str, FundChangeSet]] | None = None,
) -> ScoringInputs:
    sym = symbol.strip().upper()
    if fund_sets is None:
        fund_sets = fund_sets_at(session, settings, as_of)
    return ScoringInputs(
        insider_buys=_insider_buys_as_of(session, sym, as_of),
        house_trades=_house_trades_as_of(session, sym, settings, as_of),
        fund_changes=fund_changes_for_symbol(fund_sets, sym),
        ownership_filings=[
            scoring.OwnershipFiling(form=r.schedule, is_amendment=r.is_amendment, filed_at=r.known_at)
            for r in ownership_filings_as_of(session, sym, as_of, WINDOW_DAYS)
        ],
        filings_8k=[
            scoring.Filing8K(item_codes=list(f.items), filed_at=f.known_at)
            for f in filings_8k_as_of(session, sym, as_of, WINDOW_DAYS)
        ],
    )


def smart_money_score_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    settings: AppSettings | None = None,
    *,
    fund_sets: dict[int, tuple[str, FundChangeSet]] | None = None,
) -> SymbolScore:
    """The smart-money score of `symbol` at the cutoff (inside `with as_of(t):` the argument can
    be left out). Only facts known at the cutoff are read; the scorer then ignores anything filed
    after `now` a second time."""
    settings = settings or AppSettings()
    now = as_of if as_of is not None else current_as_of()
    inputs = _gather_inputs(session, symbol, settings, as_of, fund_sets=fund_sets)
    result = score_smart_money(
        now=now,
        insider_buys=inputs.insider_buys,
        house_trades=inputs.house_trades,
        fund_changes=inputs.fund_changes,
        ownership_filings=inputs.ownership_filings,
        filings_8k=inputs.filings_8k,
        followed_member_multiplier=settings.smart_money_followed_member_multiplier,
    )
    return SymbolScore(symbol=symbol.strip().upper(), now=now, result=result, inputs=inputs)


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


def components_json(result: ScoreResult) -> list[dict]:
    return [{"source": c.source, "points": c.points, "reason": c.reason} for c in result.components]


def last_candidate(session: Session, symbol: str) -> SmartMoneyCandidate | None:
    return session.exec(
        select(SmartMoneyCandidate)
        .where(SmartMoneyCandidate.symbol == symbol)
        .order_by(SmartMoneyCandidate.created_at.desc(), SmartMoneyCandidate.id.desc())
    ).first()


def flag_candidate(
    session: Session, scored: SymbolScore, settings: AppSettings, *, scope: str | None = None
) -> SmartMoneyCandidate | None:
    """Create a candidate when the score reaches the bar and the re-flag guard allows it
    (NEW_PLAN 5: the same symbol is not a new candidate for 30 days unless its score rose by 2 or
    more since the last flag). Returns the new row, or None. Opens nothing and decides nothing."""
    if scored.result.total < settings.smart_money_min_score:
        return None
    previous = last_candidate(session, scored.symbol)
    if previous is not None and not should_reflag(previous.score, previous.created_at, scored.result.total, scored.now):
        return None
    candidate = SmartMoneyCandidate(
        symbol=scored.symbol,
        score=scored.result.total,
        components=components_json(scored.result),
        scored_at=scored.now,
        first_trigger_known_at=scored.first_trigger_known_at,
        status=CANDIDATE_NEW,
        created_at=scored.now,
        expires_at=scored.now + timedelta(days=CANDIDATE_EXPIRY_DAYS),
        scope=scope,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def expire_stale_candidates(session: Session, now: datetime) -> int:
    """Close candidates still `new` (or waiting for the open as `evaluating`) past their expiry. Returns how many.
    Items in the review queue expire through `smart_money_pipeline_service.expire_review_items`."""
    rows = session.exec(
        select(SmartMoneyCandidate).where(
            SmartMoneyCandidate.status.in_((CANDIDATE_NEW, STATUS_EVALUATING)), SmartMoneyCandidate.expires_at < now
        )
    ).all()
    for row in rows:
        row.status = STATUS_EXPIRED
        row.reason = "expired"
        row.decided_by = "auto"
        row.decided_at = now
        session.add(row)
    if rows:
        session.commit()
    return len(rows)

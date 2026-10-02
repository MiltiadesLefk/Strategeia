"""Reading fund holdings and 5% ownership filings as they were known at a moment.

The facts are written by `app.data_providers.sec_13f` (one `fund_filing` fact per
13F filing and one `fund_holding` fact per security in it) and
`app.data_providers.sec_13dg` (one `ownership_filing` fact per Schedule 13D/13G).
Every read goes through `facts_known_as_of`, so a filing accepted after the
cutoff is invisible: a June 30 position of a fund that filed on August 14 does
not exist for any moment before August 14. Inside `with as_of(t):` the `as_of`
argument can be left out.

What a 13F can and cannot say (the readers return it as is; the screens repeat it):
long positions only, a snapshot of the last day of the quarter, published up to
45 days later, and no trade dates. "Added" below means "the share count at this
quarter end is higher than at the previous quarter end", not "bought on a day".
Share counts are as filed: a stock split inside the quarter shows up as a large
"added", because the filing does not adjust for it.

Amendments (13F-HR/A): a "restatement" replaces the original filing for that
quarter from the moment it was accepted; "new holdings" adds lines the original
left out. Before the amendment was accepted, the original is what was known and
stays in force. An amendment that does not say which kind it is is read as a
restatement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session

from app.knowledge import FactKind, KnownFact, current_as_of, facts_known_as_of, to_naive_utc

AMENDMENT_NEW_HOLDINGS = "NEW HOLDINGS"
# A change in share count smaller than this (in percent) is called "unchanged":
# funds' counts move slightly for reasons that are not decisions (a corporate
# action, a rounding in the filing).
MATERIAL_CHANGE_PCT = 1.0
# Two periods this many days apart or fewer are consecutive quarters.
CONSECUTIVE_QUARTER_MAX_DAYS = 105

STATUS_NEW = "new"
STATUS_ADDED = "added"
STATUS_TRIMMED = "trimmed"
STATUS_SOLD_OUT = "sold_out"
STATUS_UNCHANGED = "unchanged"
STATUS_NO_COMPARISON = "no_comparison"


@dataclass(frozen=True)
class FundFiling:
    cik: str
    manager: str | None
    accession: str
    form: str
    period: date | None
    filing_date: date | None
    known_at: datetime  # SEC acceptance time (naive UTC)
    is_amendment: bool
    amendment_type: str | None
    is_notice: bool
    holdings_count: int
    matched_count: int
    total_value: float
    value_unit: str | None
    url: str | None


@dataclass(frozen=True)
class FundPosition:
    cusip: str
    issuer: str
    symbol: str | None
    put_call: str | None
    share_type: str
    shares: float
    value: float  # dollars
    title_of_class: str | None = None

    @property
    def key(self) -> tuple[str, str | None, str]:
        return (self.cusip, self.put_call, self.share_type)


@dataclass(frozen=True)
class FundChange:
    cusip: str
    issuer: str
    symbol: str | None
    put_call: str | None
    share_type: str
    status: str  # new | added | trimmed | sold_out | unchanged
    shares_now: float
    shares_before: float
    change_pct: float | None  # None for a new position (no base)
    value_now: float
    value_before: float
    weight_now_pct: float  # share of the fund's reported portfolio, 0 when sold out
    weight_before_pct: float


@dataclass
class FundChangeSet:
    cik: str
    manager: str | None
    period: date | None
    previous_period: date | None
    filed_at: datetime  # when the latest quarter's filing was accepted
    previous_filed_at: datetime | None
    consecutive: bool  # previous_period is the quarter right before `period`
    holdings_count: int
    total_value: float
    changes: list[FundChange] = field(default_factory=list)
    has_comparison: bool = False

    def count(self, status: str) -> int:
        return sum(1 for c in self.changes if c.status == status)


@dataclass(frozen=True)
class FundHolder:
    """One fund's position in one symbol at its latest known quarter end."""

    cik: str
    manager: str | None
    period: date | None
    filed_at: datetime
    shares: float
    value: float
    weight_pct: float
    previous_shares: float | None  # None: no earlier quarter known for this fund
    status: str  # new | added | trimmed | sold_out | unchanged | no_comparison
    change_pct: float | None
    filing_url: str | None


@dataclass(frozen=True)
class OwnershipRecord:
    symbol: str | None
    accession: str
    schedule: str  # "13D" | "13G"
    form: str
    is_amendment: bool
    known_at: datetime
    filing_date: date | None
    event_date: date | None
    filer_name: str | None
    filer_cik: str | None
    issuer_name: str | None
    issuer_cik: str | None
    class_title: str | None
    percent: float | None
    shares: float | None
    rule: str | None
    purpose: str | None
    person_count: int
    filing_url: str | None


def _iso(raw) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _cutoff(as_of: datetime | None) -> datetime:
    return to_naive_utc(as_of) if as_of is not None else current_as_of()


# --------------------------------------------------------------------------
# Filings
# --------------------------------------------------------------------------


def _to_filing(fact: KnownFact) -> FundFiling:
    p = fact.payload or {}
    return FundFiling(
        cik=str(p.get("cik") or ""),
        manager=p.get("manager"),
        accession=p.get("accession") or "",
        form=p.get("form") or "",
        period=_iso(p.get("period")),
        filing_date=_iso(p.get("filing_date")),
        known_at=fact.known_at,
        is_amendment=bool(p.get("is_amendment")),
        amendment_type=(p.get("amendment_type") or None),
        is_notice=bool(p.get("is_notice")),
        holdings_count=int(p.get("holdings_count") or 0),
        matched_count=int(p.get("matched_count") or 0),
        total_value=float(p.get("total_value") or 0.0),
        value_unit=p.get("value_unit"),
        url=fact.source_ref or p.get("filing_url"),
    )


def fund_filings_as_of(session: Session, cik: str | int | None = None, as_of: datetime | None = None) -> list[FundFiling]:
    """Every 13F filing (holdings reports and notices) accepted by the cutoff,
    newest first, optionally for one manager."""
    wanted = str(cik).lstrip("0") if cik is not None else None
    out = [_to_filing(f) for f in facts_known_as_of(session, FactKind.FUND_FILING, as_of=as_of)]
    return [f for f in out if wanted is None or f.cik == wanted]


def _periods(filings: list[FundFiling]) -> dict[date, list[FundFiling]]:
    by_period: dict[date, list[FundFiling]] = {}
    for f in filings:
        if f.period is not None and not f.is_notice:
            by_period.setdefault(f.period, []).append(f)
    return by_period


def _in_force(period_filings: list[FundFiling]) -> list[FundFiling]:
    """The filings whose rows make up the quarter, oldest first: the original,
    changed by whatever amendments were accepted. Only filings already visible
    are passed in, so an amendment accepted later does not exist yet."""
    ordered = sorted(period_filings, key=lambda f: (f.known_at, f.accession))
    chosen: list[FundFiling] = []
    for f in ordered:
        if f.is_amendment and f.amendment_type == AMENDMENT_NEW_HOLDINGS and chosen:
            chosen.append(f)
        else:
            chosen = [f]  # an original, or a restatement, replaces what came before
    return chosen


def _filing_rows(session: Session, filing: FundFiling) -> list[KnownFact]:
    """The holding facts of one filing. All rows of a filing share its acceptance
    time, so reading exactly that instant finds them through the ordinary
    guarded reader (other managers filing in the same second are filtered out)."""
    facts = facts_known_as_of(session, FactKind.FUND_HOLDING, as_of=filing.known_at, since=filing.known_at)
    return [f for f in facts if (f.payload or {}).get("accession") == filing.accession]


def _to_position(fact: KnownFact) -> FundPosition:
    p = fact.payload or {}
    return FundPosition(
        cusip=p.get("cusip") or "",
        issuer=p.get("issuer") or "",
        symbol=fact.symbol or p.get("symbol"),
        put_call=p.get("put_call"),
        share_type=p.get("share_type") or "SH",
        shares=float(p.get("shares") or 0.0),
        value=float(p.get("value") or 0.0),
        title_of_class=p.get("title_of_class"),
    )


def _positions_of_period(session: Session, period_filings: list[FundFiling]) -> dict[tuple, FundPosition]:
    positions: dict[tuple, FundPosition] = {}
    for filing in _in_force(period_filings):
        for fact in _filing_rows(session, filing):
            position = _to_position(fact)
            positions[position.key] = position
    return positions


def _total_value(period_filings: list[FundFiling], positions: dict[tuple, FundPosition]) -> float:
    return sum(p.value for p in positions.values()) or sum(f.total_value for f in _in_force(period_filings))


def fund_positions_as_of(
    session: Session, cik: str | int, as_of: datetime | None = None, period: date | None = None
) -> tuple[FundFiling | None, list[FundPosition]]:
    """The fund's holdings for `period` (default: its newest quarter known at the
    cutoff), largest first, and the filing that is its latest word on it."""
    filings = fund_filings_as_of(session, cik, as_of)
    by_period = _periods(filings)
    if not by_period:
        return None, []
    chosen = period if period in by_period else max(by_period)
    positions = _positions_of_period(session, by_period[chosen])
    latest = _in_force(by_period[chosen])[-1]
    return latest, sorted(positions.values(), key=lambda p: p.value, reverse=True)


# --------------------------------------------------------------------------
# Quarter over quarter
# --------------------------------------------------------------------------


def classify_change(shares_before: float, shares_now: float) -> tuple[str, float | None]:
    """(status, percent change in shares)."""
    if shares_before <= 0 < shares_now:
        return STATUS_NEW, None
    if shares_now <= 0 < shares_before:
        return STATUS_SOLD_OUT, -100.0
    if shares_before <= 0 and shares_now <= 0:
        return STATUS_UNCHANGED, 0.0
    pct = (shares_now - shares_before) / shares_before * 100.0
    if abs(pct) < MATERIAL_CHANGE_PCT:
        return STATUS_UNCHANGED, pct
    return (STATUS_ADDED if pct > 0 else STATUS_TRIMMED), pct


def fund_changes_as_of(session: Session, cik: str | int, as_of: datetime | None = None) -> FundChangeSet | None:
    """New, added, trimmed and sold-out positions: the newest known quarter
    against the quarter known before it. None when no filing of the fund is
    known at the cutoff. With only one quarter known, `has_comparison` is False
    and `changes` is empty (the holdings are still readable with
    `fund_positions_as_of`)."""
    filings = fund_filings_as_of(session, cik, as_of)
    by_period = _periods(filings)
    if not by_period:
        return None
    periods = sorted(by_period, reverse=True)
    latest_period = periods[0]
    latest_filing = _in_force(by_period[latest_period])[-1]
    now_positions = _positions_of_period(session, by_period[latest_period])
    now_total = _total_value(by_period[latest_period], now_positions)
    result = FundChangeSet(
        cik=latest_filing.cik,
        manager=latest_filing.manager,
        period=latest_period,
        previous_period=None,
        filed_at=latest_filing.known_at,
        previous_filed_at=None,
        consecutive=False,
        holdings_count=len(now_positions),
        total_value=now_total,
    )
    if len(periods) < 2:
        return result
    previous_period = periods[1]
    previous_filing = _in_force(by_period[previous_period])[-1]
    before_positions = _positions_of_period(session, by_period[previous_period])
    before_total = _total_value(by_period[previous_period], before_positions)
    result.previous_period = previous_period
    result.previous_filed_at = previous_filing.known_at
    result.consecutive = (latest_period - previous_period).days <= CONSECUTIVE_QUARTER_MAX_DAYS
    result.has_comparison = True

    for key in set(now_positions) | set(before_positions):
        now = now_positions.get(key)
        before = before_positions.get(key)
        shares_now = now.shares if now else 0.0
        shares_before = before.shares if before else 0.0
        status, pct = classify_change(shares_before, shares_now)
        ref = now or before
        assert ref is not None
        result.changes.append(
            FundChange(
                cusip=ref.cusip,
                issuer=ref.issuer,
                symbol=ref.symbol,
                put_call=ref.put_call,
                share_type=ref.share_type,
                status=status,
                shares_now=shares_now,
                shares_before=shares_before,
                change_pct=pct,
                value_now=now.value if now else 0.0,
                value_before=before.value if before else 0.0,
                weight_now_pct=(now.value / now_total * 100.0) if now and now_total else 0.0,
                weight_before_pct=(before.value / before_total * 100.0) if before and before_total else 0.0,
            )
        )
    result.changes.sort(key=lambda c: max(c.value_now, c.value_before), reverse=True)
    return result


# --------------------------------------------------------------------------
# Who holds a symbol
# --------------------------------------------------------------------------


def _stock_rows(facts: list[KnownFact]) -> list[KnownFact]:
    return [
        f for f in facts if not (f.payload or {}).get("put_call") and (f.payload or {}).get("share_type", "SH") == "SH"
    ]


def fund_holders_of_symbol(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    ciks: list[str] | list[int] | None = None,
) -> list[FundHolder]:
    """The funds that hold `symbol` at their latest known quarter end, or held it
    the quarter before and no longer do (status "sold_out"), largest position
    first. `ciks` limits the answer to those managers (the followed funds).

    Each fund is read at its own newest quarter: filing dates differ, so two
    funds in the list can describe different quarter ends, which each row says."""
    symbol_facts = _stock_rows(facts_known_as_of(session, FactKind.FUND_HOLDING, as_of=as_of, symbol=symbol))
    if not symbol_facts:
        return []
    wanted = {str(c).lstrip("0") for c in ciks} if ciks is not None else None
    all_filings = fund_filings_as_of(session, None, as_of)
    by_cik: dict[str, list[FundFiling]] = {}
    for filing in all_filings:
        by_cik.setdefault(filing.cik, []).append(filing)

    rows_by_accession: dict[str, list[KnownFact]] = {}
    for fact in symbol_facts:
        rows_by_accession.setdefault((fact.payload or {}).get("accession") or "", []).append(fact)
    holder_ciks = {str((f.payload or {}).get("cik") or "") for f in symbol_facts}

    holders: list[FundHolder] = []
    for cik in sorted(holder_ciks):
        if wanted is not None and cik not in wanted:
            continue
        by_period = _periods(by_cik.get(cik, []))
        if not by_period:
            continue
        periods = sorted(by_period, reverse=True)

        def shares_and_value(period: date) -> tuple[float, float]:
            shares = value = 0.0
            seen: dict[tuple, KnownFact] = {}
            for filing in _in_force(by_period[period]):
                for fact in rows_by_accession.get(filing.accession, []):
                    seen[((fact.payload or {}).get("cusip"), None, "SH")] = fact
            for fact in seen.values():
                shares += float((fact.payload or {}).get("shares") or 0.0)
                value += float((fact.payload or {}).get("value") or 0.0)
            return shares, value

        latest_period = periods[0]
        shares_now, value_now = shares_and_value(latest_period)
        previous: float | None = None
        if len(periods) > 1:
            previous = shares_and_value(periods[1])[0]
        if shares_now <= 0 and not previous:
            continue  # never held it, as far as the stored quarters show
        latest_filing = _in_force(by_period[latest_period])[-1]
        status, pct = (
            classify_change(previous, shares_now) if previous is not None else (STATUS_NO_COMPARISON, None)
        )
        total = _total_value(by_period[latest_period], _positions_of_period(session, by_period[latest_period]))
        holders.append(
            FundHolder(
                cik=cik,
                manager=latest_filing.manager,
                period=latest_period,
                filed_at=latest_filing.known_at,
                shares=shares_now,
                value=value_now,
                weight_pct=(value_now / total * 100.0) if total else 0.0,
                previous_shares=previous,
                status=status,
                change_pct=pct,
                filing_url=latest_filing.url,
            )
        )
    holders.sort(key=lambda h: (h.value, h.shares), reverse=True)
    return holders


# --------------------------------------------------------------------------
# 5% ownership filings
# --------------------------------------------------------------------------


def _to_ownership(fact: KnownFact) -> OwnershipRecord:
    p = fact.payload or {}
    return OwnershipRecord(
        symbol=fact.symbol,
        accession=p.get("accession") or "",
        schedule=p.get("schedule") or "",
        form=p.get("form") or "",
        is_amendment=bool(p.get("is_amendment")),
        known_at=fact.known_at,
        filing_date=_iso(p.get("filing_date")),
        event_date=_iso(p.get("event_date")),
        filer_name=p.get("filer_name"),
        filer_cik=p.get("filer_cik"),
        issuer_name=p.get("issuer_name"),
        issuer_cik=p.get("issuer_cik"),
        class_title=p.get("class_title"),
        percent=p.get("percent"),
        shares=p.get("shares"),
        rule=p.get("rule"),
        purpose=p.get("purpose"),
        person_count=len(p.get("persons") or []),
        filing_url=fact.source_ref or p.get("filing_url"),
    )


def ownership_filings_as_of(
    session: Session,
    symbol: str | None = None,
    as_of: datetime | None = None,
    window_days: int = 90,
    schedule: str | None = None,
    limit: int | None = None,
) -> list[OwnershipRecord]:
    """13D/13G filings accepted in the `window_days` before the cutoff, newest
    first; `schedule` ("13D" or "13G") narrows to one form family."""
    cutoff = _cutoff(as_of)
    facts = facts_known_as_of(
        session,
        FactKind.OWNERSHIP_FILING,
        as_of=as_of,
        symbol=symbol,
        since=cutoff - timedelta(days=window_days),
    )
    records = [_to_ownership(f) for f in facts]
    if schedule is not None:
        records = [r for r in records if r.schedule == schedule]
    return records[:limit] if limit is not None else records

"""Look-ahead guards: tests that fail if the backtest, or any reader of dated data,
can see something that was not public at the simulated moment.

Four layers, each catching a different way to cheat:

1. A probe checks the timestamps on whatever a source returns (tests/lookahead_probe.py)
   and is itself proven to catch a deliberately leaky provider and reader.
2. "Future perturbation": change everything after a cutoff (bars, facts, extra rows)
   and demand a byte-identical answer, over many seeded random cutoffs. This also
   catches a source that computes from future data but returns old-looking stamps.
3. Date boundaries: daylight-saving changes, early closes, weekends, holidays,
   midnight UTC versus New York, a filing accepted at 17:59 versus 18:01 ET.
4. A discovery test: every public reader that takes an `as_of` is listed here, so a
   new reader cannot ship without being put through layers 1 and 2.

hypothesis is not installed, so the random cases come from fixed seeds: a failure
is reproducible and the message names the cutoff.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app as app_package
from app.backtest import runner
from app.backtest.calendar import close_moment, decision_moment, trading_days
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.earnings_history import earnings_history_as_of
from app.backtest.fundamentals_history import known_annual_revenue, revenue_history_as_of
from app.backtest.params import BacktestParams, SettingsOverrides, effective_settings
from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, DataProvider, NewsItem
from app.data_providers.sec_8k import filings_8k_as_of, has_8k_data
from app.knowledge import (
    FactKind,
    KnownFact,
    LookAheadError,
    as_of,
    end_of_local_day_utc,
    facts_known_as_of,
    make_dedupe_key,
    record_fact,
    source_time_to_utc,
)
from app.knowledge.congress_trades import (
    congress_clusters_as_of,
    congress_filings_as_of,
    congress_member_summaries_as_of,
    congress_trades_as_of,
    has_congress_data,
    known_members_as_of,
    net_buyers_as_of,
)
from app.knowledge.fund_holdings import (
    fund_changes_as_of,
    fund_filings_as_of,
    fund_holders_of_symbol,
    fund_positions_as_of,
    ownership_filings_as_of,
)
from app.knowledge.research_library import grounded_context, library_entries_as_of
from app.knowledge.insider_trades import insider_activity_as_of, insider_clusters_as_of, insider_trades_as_of
from app.knowledge.point_in_time import SEC_EDGAR_TZ
from app.knowledge.store import fact_stats, latest_known
from app.markets import is_daily_bar_final, is_us_trading_day, to_market_time, us_early_closes, us_session_bounds
from app.services.archive_service import archived_news, latest_fundamentals_snapshot
from app.signals.finra import short_volume_ratio_as_of
from tests.backtest_helpers import frame_from, standard_book, trading_days_from, wiggly_uptrend
from tests.lookahead_probe import (
    LookAheadFinding,
    Probe,
    ProbedProvider,
    assert_independent_of_future,
    canon,
    observe,
    violations,
)

NEW_YORK = ZoneInfo("America/New_York")
SEED = 20260930
SYMBOLS = ("AAA", "BBB")


def et(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """A New York wall-clock time as naive UTC (the stored convention)."""
    return source_time_to_utc(datetime(year, month, day, hour, minute, second), NEW_YORK)


# ===================================================================== prices

BOOK_DAYS = trading_days_from(date(2024, 6, 3), 700)  # through early 2027
BOOK_FIRST_DECISION = BOOK_DAYS[300]
BOOK_LAST_DECISION = BOOK_DAYS[600]


def random_book_frames(rng: random.Random) -> dict[str, pd.DataFrame]:
    """Random-walk bars where every field of every day is distinct and positive."""
    count = len(BOOK_DAYS)
    frames: dict[str, pd.DataFrame] = {}
    for symbol in ("AAA", "BBB", "SPY", "^VIX"):
        np_rng = np.random.default_rng(rng.randrange(1 << 30))
        closes = 100 * np.cumprod(1 + np_rng.normal(0.0004, 0.012, count))
        opens = closes * (1 + np_rng.normal(0, 0.004, count))
        highs = np.maximum(opens, closes) * (1 + np.abs(np_rng.normal(0, 0.004, count)))
        lows = np.minimum(opens, closes) * (1 - np.abs(np_rng.normal(0, 0.004, count)))
        frame = frame_from(BOOK_DAYS, closes, opens=opens, highs=highs, lows=lows)
        frame["volume"] = np_rng.integers(10_000, 5_000_000, count).astype(float)
        frames[symbol] = frame
    return frames


def future_of(frame: pd.DataFrame, moment: datetime) -> pd.Series:
    """Rows of `frame` that did not exist yet at `moment`, as a boolean mask over its
    rows, plus which of those rows may keep their `open` (a session in progress has
    printed its opening price)."""
    return pd.Series(
        [not is_daily_bar_final("SPY", pd.Timestamp(d).date(), moment) for d in frame["date"]], index=frame.index
    )


def perturb_future(frames: dict[str, pd.DataFrame], moment: datetime, rng: random.Random) -> dict[str, pd.DataFrame]:
    """A copy of the bars with every value that is not knowable at `moment` replaced
    by something else. The open of a session already under way is kept: at 09:45 ET
    its opening print is the one thing a trader can see of that day's bar."""
    now_et = to_market_time(moment)
    out: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        copy = frame.copy()
        not_final = future_of(copy, moment)
        for index in copy.index[not_final]:
            day = pd.Timestamp(copy.at[index, "date"]).date()
            bounds = us_session_bounds(day)
            open_known = bounds is not None and now_et >= bounds.open
            for column in ("open", "high", "low", "close", "volume"):
                if column == "open" and open_known:
                    continue
                copy.at[index, column] = copy.at[index, column] * rng.uniform(0.5, 2.0)
        out[symbol] = copy
    return out


def random_cutoffs(rng: random.Random, count: int) -> list[datetime]:
    """Decision moments, close moments and awkward in-between instants (midnight UTC,
    weekends, pre-open, mid-session) across the book's range."""
    days = BOOK_DAYS[300:600]
    cutoffs: list[datetime] = []
    for _ in range(count):
        day = rng.choice(days)
        kind = rng.choice(["decision", "close", "random", "midnight_utc", "pre_open", "weekend"])
        if kind == "decision":
            cutoffs.append(decision_moment(day))
        elif kind == "close":
            cutoffs.append(close_moment(day))
        elif kind == "midnight_utc":
            cutoffs.append(datetime.combine(day, time(0, 0)))
        elif kind == "pre_open":
            cutoffs.append(et(day.year, day.month, day.day, 8, rng.randrange(0, 59)))
        elif kind == "weekend":
            saturday = day + timedelta(days=(5 - day.weekday()) % 7)
            cutoffs.append(et(saturday.year, saturday.month, saturday.day, rng.randrange(0, 24), rng.randrange(0, 59)))
        else:
            cutoffs.append(datetime.combine(day, time(0, 0)) + timedelta(minutes=rng.randrange(0, 24 * 60)))
    return cutoffs


PRICE_REQUESTS: list[tuple[str, str, str]] = [
    (symbol, period, interval)
    for symbol in ("AAA", "SPY", "^VIX")
    for period, interval in (("5d", "1d"), ("1mo", "1d"), ("6mo", "1d"), ("1y", "1d"), ("max", "1d"), ("6mo", "1wk"), ("1y", "1wk"), ("2y", "1wk"))
]


def ask_prices(provider: BacktestDataProvider | ProbedProvider, moment: datetime) -> list:
    """Every public price method, at `moment`; an unavailable answer is part of the result."""
    answers: list = []
    with as_of(moment):
        for symbol, period, interval in PRICE_REQUESTS:
            try:
                answers.append(canon(provider.get_ohlcv(symbol, period=period, interval=interval)))
            except AllProvidersFailedError as exc:
                answers.append(("unavailable", str(exc)))
        for symbol in ("AAA", "SPY"):
            try:
                answers.append(canon(provider.get_quote(symbol)))
            except AllProvidersFailedError as exc:
                answers.append(("unavailable", str(exc)))
    return answers


def test_prices_do_not_depend_on_anything_after_the_cutoff():
    rng = random.Random(SEED)
    frames = random_book_frames(rng)
    real = BacktestDataProvider(PriceBook.from_frames(frames))
    compared = 0
    for moment in random_cutoffs(rng, 12):
        changed_frames = perturb_future(frames, moment, rng)
        # The perturbation is real: some stored value differs from the original.
        assert any(not changed_frames[s].equals(frames[s]) for s in frames), moment
        changed = BacktestDataProvider(PriceBook.from_frames(changed_frames))
        assert ask_prices(real, moment) == ask_prices(changed, moment), f"cutoff {moment}"
        compared += 1
    assert compared == 12


def test_every_price_answer_passes_the_probe_at_random_cutoffs():
    rng = random.Random(SEED + 1)
    frames = random_book_frames(rng)
    provider = ProbedProvider(BacktestDataProvider(PriceBook.from_frames(frames)))
    for moment in random_cutoffs(rng, 12):
        ask_prices(provider, moment)
    assert provider.probe.observed > 100 and not provider.probe.problems


def test_the_provider_methods_probed_are_every_method_of_the_data_provider_interface():
    interface = {n for n, f in inspect.getmembers(DataProvider, inspect.isfunction) if n.startswith("get_")}
    implemented = {n for n, f in inspect.getmembers(BacktestDataProvider, inspect.isfunction) if n.startswith("get_")}
    assert interface <= implemented, f"BacktestDataProvider lacks {interface - implemented}"
    # The probe wraps every get_* by name, so a new interface method is covered automatically; what
    # needs a human is answering it in `ask_the_rest` below.
    assert interface == set(ASKED_BY_THE_REST) | {"get_ohlcv", "get_quote"}, (
        "a DataProvider method was added or removed: add it to ask_the_rest (and its dated source handler "
        "to the leaky-handler test) so it is probed"
    )


# Methods that need a dated source, each paired with how to call it.
ASKED_BY_THE_REST: dict[str, Callable[[Any], Any]] = {}  # filled just below



def _register_rest() -> None:
    for name, call in {
        "get_company_overview": lambda p: p.get_company_overview("AAA"),
        "get_financials": lambda p: p.get_financials("AAA"),
        "get_news": lambda p: p.get_news("AAA"),
        "get_earnings_date": lambda p: p.get_earnings_date("AAA"),
        "get_earnings_estimate": lambda p: p.get_earnings_estimate("AAA"),
        "get_earnings_history": lambda p: p.get_earnings_history("AAA"),
        "get_options_summary": lambda p: p.get_options_summary("AAA"),
        "get_options_chain": lambda p: p.get_options_chain("AAA"),
        "get_insider_activity": lambda p: p.get_insider_activity("AAA"),
    }.items():
        ASKED_BY_THE_REST[name] = call


_register_rest()


def ask_the_rest(provider: Any) -> None:
    for name, call in ASKED_BY_THE_REST.items():
        try:
            call(provider)
        except AllProvidersFailedError:
            pass


# ---------------------------------------------------------- the probe proves itself


class LeakyBars:
    """A provider that ignores the simulated moment and hands back every bar it has."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        return self.frames[symbol]


class OffByOneBars:
    """The classic mistake: includes the decision day's own bar."""

    def __init__(self, inner: BacktestDataProvider, frames: dict[str, pd.DataFrame]):
        self.inner, self.frames = inner, frames

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        honest = self.inner.get_ohlcv(symbol, period, interval)
        last = pd.Timestamp(honest["date"].iloc[-1])
        frame = self.frames[symbol]
        return frame[frame["date"] <= last + pd.Timedelta(days=1)].tail(len(honest) + 1)


def test_the_probe_catches_a_provider_that_returns_future_bars():
    frames = random_book_frames(random.Random(SEED))
    day = BOOK_DAYS[400]
    with as_of(decision_moment(day)):
        with pytest.raises(LookAheadFinding, match="not final"):
            ProbedProvider(LeakyBars(frames)).get_ohlcv("AAA")
        with pytest.raises(LookAheadFinding, match="not final"):
            ProbedProvider(OffByOneBars(BacktestDataProvider(PriceBook.from_frames(frames)), frames)).get_ohlcv("AAA")
        # ...and the honest provider is fine in the same place
        ProbedProvider(BacktestDataProvider(PriceBook.from_frames(frames))).get_ohlcv("AAA")


def test_the_future_perturbation_check_catches_a_provider_that_reads_the_future():
    rng = random.Random(SEED)
    frames = random_book_frames(rng)
    moment = decision_moment(BOOK_DAYS[420])

    class PeeksAtTomorrow:
        """Returns honest-looking stamps but its numbers come from the unfiltered book."""

        def __init__(self, book_frames):
            self.inner = BacktestDataProvider(PriceBook.from_frames(book_frames))
            self.frames = book_frames

        def get_ohlcv(self, symbol, period="6mo", interval="1d"):
            honest = self.inner.get_ohlcv(symbol, period, interval).copy()
            leaked = self.frames[symbol]["close"].iloc[-1]  # the very last bar of the whole history
            honest.loc[honest.index[-1], "close"] = leaked
            return honest

        def get_quote(self, symbol):
            return self.inner.get_quote(symbol)

    changed = perturb_future(frames, moment, rng)
    with pytest.raises(AssertionError):
        assert ask_prices(PeeksAtTomorrow(frames), moment) == ask_prices(PeeksAtTomorrow(changed), moment)
    # the real provider passes the identical comparison
    assert ask_prices(BacktestDataProvider(PriceBook.from_frames(frames)), moment) == ask_prices(
        BacktestDataProvider(PriceBook.from_frames(changed)), moment
    )


def test_a_dated_source_handler_is_probed_and_a_leaky_one_is_caught():
    frames = random_book_frames(random.Random(SEED))
    book = PriceBook.from_frames(frames)
    moment = decision_moment(BOOK_DAYS[450])

    class Honest:
        def news(self, symbol, at):
            return [NewsItem("old", "src", "https://example.com/a", (at - timedelta(hours=3)).isoformat() + "Z")]

    class Leaky:
        def news(self, symbol, at):
            return [NewsItem("tomorrow", "src", "https://example.com/b", (at + timedelta(hours=3)).isoformat() + "Z")]

    with as_of(moment):
        honest = ProbedProvider(BacktestDataProvider(book, dated_sources=Honest()))
        assert honest.get_news("AAA") and not honest.probe.problems
        with pytest.raises(LookAheadFinding, match="published_at"):
            ProbedProvider(BacktestDataProvider(book, dated_sources=Leaky())).get_news("AAA")
        ask_the_rest(ProbedProvider(BacktestDataProvider(book)))  # every other method answers or declines cleanly


def test_observe_reads_the_timestamps_each_kind_of_result_carries():
    cutoff = datetime(2026, 6, 2, 12, 0)
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-05-28", "2026-05-29"]), "close": [1.0, 2.0]})
    assert [o.kind for o in observe(frame)] == ["bar_day"] and not violations(observe(frame), cutoff)
    late = pd.DataFrame({"date": pd.to_datetime(["2026-06-02"]), "close": [1.0]})  # that day's bar is not final at noon
    assert violations(observe(late), cutoff)


# ============================================================ dated facts

KINDS = (
    FactKind.INSIDER_TRADE, FactKind.SEC_FILING_8K, FactKind.FINRA_SHORT_VOLUME, FactKind.FUNDAMENTALS_REVENUE,
    FactKind.EARNINGS_REPORT, FactKind.NEWS, FactKind.FUNDAMENTALS_SNAPSHOT,
)
FACT_START = datetime(2025, 1, 6)
FACT_SPAN_DAYS = 480


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _add(session, kind, symbol, key, known_at, payload, effective_at=None, basis=None):
    record_fact(
        session, kind=kind, source="test", symbol=symbol, dedupe_key=make_dedupe_key(kind, symbol, key), known_at=known_at,
        known_at_basis=basis, effective_at=effective_at, payload=payload, fetched_at=known_at, commit=False,
    )


def seed_facts(session: Session, rng: random.Random) -> None:
    """A few hundred facts of every kind, spread over FACT_SPAN_DAYS with awkward times of day."""
    owners = [("o1", "Alice"), ("o2", "Bob"), ("o3", "Cleo")]
    for symbol in SYMBOLS:
        for n in range(70):
            at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
            owner_key, owner = rng.choice(owners)
            trade_day = (at - timedelta(days=rng.randint(1, 4))).date()
            _add(
                session, FactKind.INSIDER_TRADE, symbol, f"t{n}", at,
                {
                    "accession": f"0001-{symbol}-{n}", "row_index": 0, "form": "4", "table": "non_derivative",
                    "code": rng.choice(["P", "P", "S"]), "shares": rng.randint(100, 9000), "price": rng.uniform(20, 300),
                    "value": rng.uniform(2_000, 900_000), "owner_key": owner_key, "owner_name": owner,
                    "transaction_date": trade_day.isoformat(), "filing_date": at.date().isoformat(),
                    "period_of_report": trade_day.isoformat(), "issuer_cik": symbol, "is_officer": True,
                },
                effective_at=datetime.combine(trade_day, time()),
            )
        for n in range(25):
            at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
            _add(
                session, FactKind.SEC_FILING_8K, symbol, f"k{n}", at,
                {"accession": f"8k-{symbol}-{n}", "form": "8-K", "report_date": at.date().isoformat(),
                 "items": rng.sample(["1.01", "2.02", "5.02", "8.01"], 2), "item_titles": [], "categories": ["earnings"]},
            )
        for n in range(100, 300):  # FINRA files for a 200-day stretch in the middle of the span
            day = FACT_START.date() + timedelta(days=n)
            if day.weekday() >= 5:
                continue
            total = rng.uniform(1e6, 9e6)
            short = total * rng.uniform(0.3, 0.7)
            _add(
                session, FactKind.FINRA_SHORT_VOLUME, symbol, day.isoformat(), end_of_local_day_utc(day),
                {"short_volume": short, "short_exempt_volume": 0.0, "total_volume": total, "ratio": short / total},
                effective_at=datetime.combine(day, time()), basis="derived",
            )
        for year in range(2016, 2026):
            filed = date(year + 1, 2, rng.randint(10, 28))
            _add(
                session, FactKind.FUNDAMENTALS_REVENUE, symbol, f"rev{year}", end_of_local_day_utc(filed),
                {"period_end": f"{year}-12-31", "value": rng.uniform(1e9, 9e9), "filed": filed.isoformat(),
                 "concept": "Revenues", "accession": f"10k-{symbol}-{year}"},
                basis="derived",
            )
        for q in range(14):
            report_day = date(2023, 1, 25) + timedelta(days=91 * q + rng.randint(0, 6))
            _add(
                session, FactKind.EARNINGS_REPORT, symbol, f"er{q}", end_of_local_day_utc(report_day),
                {"report_date": report_day.isoformat(), "eps_estimate": rng.uniform(0.5, 2), "eps_actual": rng.uniform(0.5, 2),
                 "surprise_pct": rng.uniform(-10, 10)},
                basis="derived",
            )
        for n in range(40):
            at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
            _add(session, FactKind.NEWS, symbol, f"n{n}", at, {"headline": f"headline {n}", "url": f"https://example.com/{n}"})
        for n in range(10):
            at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
            _add(session, FactKind.FUNDAMENTALS_SNAPSHOT, symbol, f"f{n}", at, {"pe": rng.uniform(5, 40), "n": n})
        for n in range(40):  # congress_ rows: filed some days after the trade
            at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
            traded = (at - timedelta(days=rng.randint(2, 40))).date()
            member = rng.choice(["Ann Member", "Bob Member", "Cy Member"])
            _add(
                session, FactKind.CONGRESS_TRADE, symbol, f"c{n}", at,
                {"doc_id": f"d-{symbol}-{n}", "row_index": 0, "member": member, "member_key": member.lower(),
                 "owner": "self", "asset_type": "ST", "ticker": symbol, "type": "purchase", "side": rng.choice(["buy", "buy", "sell"]),
                 "amount_low": 1001, "amount_high": 15000, "amount_text": "$1,001 - $15,000",
                 "trade_date": traded.isoformat(), "filed_date": at.date().isoformat()},
                effective_at=datetime.combine(traded, time()),
            )
    for n in range(40):
        at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
        member = rng.choice(["Ann Member", "Bob Member", "Cy Member"])
        _add(
            session, "congress_filing", None, f"cf{n}", at,
            {"doc_id": f"f-{n}", "member": member, "state_district": "TX01", "filed_date": at.date().isoformat(),
             "status": rng.choice(["ok", "ok", "unreadable"]), "rows": 1},
        )
    seed_fund_facts(session, rng)
    session.commit()


def seed_fund_facts(session: Session, rng: random.Random) -> None:
    """One fund filing six quarters of 13F holdings (a filing and one row per symbol each), and 5% owner filings."""
    for q in range(6):
        period = date(2025, 3, 31) + timedelta(days=91 * q)
        accepted = datetime.combine(period + timedelta(days=rng.randint(30, 45)), time(rng.randint(0, 23), rng.randint(0, 59), 7))
        accession = f"0111-{q}"
        _add(
            session, FactKind.FUND_FILING, None, accession, accepted,
            {"cik": "111", "manager": "Fund One", "accession": accession, "form": "13F-HR", "period": period.isoformat(),
             "filing_date": accepted.date().isoformat(), "is_amendment": False, "is_notice": False, "holdings_count": len(SYMBOLS),
             "matched_count": len(SYMBOLS), "total_value": 1e6, "value_unit": "dollars"},
            effective_at=datetime.combine(period, time()), basis="source",
        )
        for symbol in SYMBOLS:
            shares = rng.randint(1000, 90000)
            _add(
                session, FactKind.FUND_HOLDING, symbol, f"{accession}-{symbol}", accepted,
                {"cik": "111", "manager": "Fund One", "accession": accession, "period": period.isoformat(), "cusip": f"CUSIP{symbol}",
                 "issuer": symbol, "symbol": symbol, "put_call": None, "share_type": "SH", "shares": shares, "value": shares * 50.0},
                effective_at=datetime.combine(period, time()), basis="source",
            )
    for symbol in SYMBOLS:
        for n in range(15):
            at = FACT_START + timedelta(days=rng.uniform(0, FACT_SPAN_DAYS))
            _add(
                session, FactKind.OWNERSHIP_FILING, symbol, f"{symbol}{n}", at,
                {"accession": f"0222-{symbol}-{n}", "form": rng.choice(["SCHEDULE 13D", "SCHEDULE 13G"]), "schedule": rng.choice(["13D", "13G"]),
                 "is_amendment": False, "filing_date": at.date().isoformat(), "filer_name": "Holder", "percent": rng.uniform(5, 15),
                 "shares": rng.randint(1000, 9000), "persons": [{"name": "Holder"}]},
                basis="source",
            )


def scramble(value: Any, rng: random.Random) -> Any:
    """The same payload with every number replaced (what a revised or fabricated future row looks like)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return type(value)(value * rng.uniform(1.5, 4)) if value else value
    if isinstance(value, dict):
        return {k: scramble(v, rng) for k, v in value.items()}
    if isinstance(value, list):
        return [scramble(v, rng) for v in value]
    return value


def rewrite_future_facts(session: Session, cutoff: datetime, rng: random.Random) -> None:
    """Change every fact known after `cutoff`, delete some, and add new ones. Never touches a fact
    known at or before it."""
    future = session.exec(select(KnownFact).where(KnownFact.known_at > cutoff)).all()
    # New rows are cloned only from kinds whose payload carries no date that must agree with known_at
    # (a clone of a FINRA day or an earnings report would be impossible data: known before its own day).
    cloneable = [
        f for f in session.exec(select(KnownFact)).all()
        if f.kind in (FactKind.NEWS, FactKind.FUNDAMENTALS_SNAPSHOT, FactKind.INSIDER_TRADE, FactKind.SEC_FILING_8K)
    ]
    templates = rng.sample(cloneable, 25)
    for fact in future:
        roll = rng.random()
        if roll < 0.15:
            session.delete(fact)
        else:
            fact.payload = scramble(fact.payload, rng)
            session.add(fact)
    for n, template in enumerate(templates):
        session.add(
            KnownFact(
                kind=template.kind, symbol=template.symbol, known_at=cutoff + timedelta(seconds=rng.randint(1, 86400 * 90)),
                fetched_at=cutoff + timedelta(days=100), known_at_basis="source", source="test", dedupe_key=f"future-{n}-{rng.random()}",
                payload=scramble(template.payload, rng), effective_at=None,
            )
        )
    session.commit()


@dataclass(frozen=True)
class Reader:
    """One public reader and how to call it: `call(session, symbol, cutoff)`, with `cutoff=None`
    meaning "take the simulated moment" (the way the backtest calls everything)."""

    function: Callable[..., Any]
    call: Callable[[Session, str, datetime | None], Any]

    @property
    def name(self) -> str:
        return f"{self.function.__module__}.{self.function.__name__}"


READERS: list[Reader] = [
    Reader(facts_known_as_of, lambda s, sym, t: facts_known_as_of(s, KINDS, as_of=t, symbol=sym)),
    Reader(latest_known, lambda s, sym, t: [latest_known(s, k, symbol=sym, as_of=t) for k in KINDS]),
    Reader(fact_stats, lambda s, sym, t: fact_stats(s, KINDS, symbol=sym, as_of=t)),
    Reader(insider_trades_as_of, lambda s, sym, t: insider_trades_as_of(s, sym, t, 60, codes=None)),
    Reader(insider_activity_as_of, lambda s, sym, t: insider_activity_as_of(s, sym, t, 60)),
    Reader(insider_clusters_as_of, lambda s, sym, t: insider_clusters_as_of(s, sym, t, 90, 14, 2)),
    Reader(short_volume_ratio_as_of, lambda s, sym, t: short_volume_ratio_as_of(s, sym, t)),
    Reader(earnings_history_as_of, lambda s, sym, t: earnings_history_as_of(s, sym, t)),
    Reader(known_annual_revenue, lambda s, sym, t: known_annual_revenue(s, sym, t)),
    Reader(revenue_history_as_of, lambda s, sym, t: revenue_history_as_of(s, sym, t)),
    Reader(has_8k_data, lambda s, sym, t: has_8k_data(s, sym, t)),
    Reader(filings_8k_as_of, lambda s, sym, t: filings_8k_as_of(s, sym, t, 90)),
    Reader(congress_trades_as_of, lambda s, sym, t: congress_trades_as_of(s, sym, t, 60)),
    Reader(congress_clusters_as_of, lambda s, sym, t: congress_clusters_as_of(s, sym, t, 120)),
    Reader(net_buyers_as_of, lambda s, sym, t: net_buyers_as_of(s, sym, t, 60)),
    Reader(congress_filings_as_of, lambda s, sym, t: congress_filings_as_of(s, t, 120)),
    Reader(congress_member_summaries_as_of, lambda s, sym, t: congress_member_summaries_as_of(s, t, 120)),
    Reader(known_members_as_of, lambda s, sym, t: known_members_as_of(s, t)),
    Reader(has_congress_data, lambda s, sym, t: has_congress_data(s, t)),
    Reader(fund_filings_as_of, lambda s, sym, t: fund_filings_as_of(s, None, t)),
    Reader(fund_positions_as_of, lambda s, sym, t: fund_positions_as_of(s, "111", t)),
    Reader(fund_changes_as_of, lambda s, sym, t: fund_changes_as_of(s, "111", t)),
    Reader(fund_holders_of_symbol, lambda s, sym, t: fund_holders_of_symbol(s, sym, t)),
    Reader(ownership_filings_as_of, lambda s, sym, t: ownership_filings_as_of(s, sym, t, 60)),
    Reader(archived_news, lambda s, sym, t: archived_news(s, sym, t)),
    Reader(latest_fundamentals_snapshot, lambda s, sym, t: latest_fundamentals_snapshot(s, sym, t)),
    Reader(library_entries_as_of, lambda s, sym, t: library_entries_as_of(s, sym, t)),
    Reader(grounded_context, lambda s, sym, t: grounded_context(s, sym, as_of=t)),
]


def fact_cutoffs(session: Session, rng: random.Random, count: int) -> list[datetime]:
    """Random instants, plus instants sitting exactly on a stored known_at and a microsecond before it
    (the boundary a `<` versus `<=` mistake would break)."""
    stamps = [f.known_at for f in session.exec(select(KnownFact)).all()]
    cutoffs = [FACT_START + timedelta(days=rng.uniform(60, FACT_SPAN_DAYS)) for _ in range(count)]
    for stamp in rng.sample(stamps, count // 2):
        cutoffs += [stamp, stamp - timedelta(microseconds=1)]
    return [c for c in cutoffs if c > FACT_START + timedelta(days=30)]


def has_content(value: Any) -> bool:
    """Whether an answer holds anything at all (so a reader that always returns nothing cannot pass for 'guarded')."""
    if isinstance(value, (list, tuple)):
        return any(has_content(v) for v in value)
    if isinstance(value, dict):
        return any(has_content(v) for v in value.values())
    if hasattr(value, "__dict__") or hasattr(value, "__dataclass_fields__"):
        return True
    return bool(value)


@pytest.mark.parametrize("reader", READERS, ids=lambda r: r.function.__name__)
def test_reader_output_depends_only_on_what_was_public_at_the_cutoff(session, reader):
    rng = random.Random(SEED + zlib.crc32(reader.function.__name__.encode()) % 1000)
    seed_facts(session, rng)
    probe = Probe()
    answered = 0
    substantive: list[bool] = []
    for cutoff in fact_cutoffs(session, rng, 6):

        def ask(cutoff=cutoff):
            """Both symbols, asked with an explicit cutoff and again from inside the simulated moment."""
            answers = []
            for symbol in SYMBOLS:
                explicit = reader.call(session, symbol, cutoff)
                with as_of(cutoff):
                    ambient = reader.call(session, symbol, None)
                probe.check(f"{reader.name}[explicit] {symbol}", explicit, cutoff)
                probe.check(f"{reader.name}[ambient] {symbol}", ambient, cutoff)
                assert canon(explicit) == canon(ambient), f"{reader.name}: explicit and ambient cutoffs disagree at {cutoff}"
                answers.append(explicit)
            substantive.append(has_content(answers))
            return answers

        assert_independent_of_future(
            ask, lambda cutoff=cutoff: rewrite_future_facts(session, cutoff, rng), f"{reader.name} at {cutoff}"
        )
        answered += 1
    assert answered and not probe.problems
    assert any(substantive), f"{reader.name} returned nothing at every cutoff, so the seeded data does not exercise it"


@pytest.mark.parametrize("reader", READERS, ids=lambda r: r.function.__name__)
def test_reader_refuses_a_cutoff_later_than_the_simulated_moment(session, reader):
    seed_facts(session, random.Random(SEED))
    moment = datetime(2026, 1, 15, 14, 45)
    with as_of(moment):
        with pytest.raises(LookAheadError):
            reader.call(session, "AAA", moment + timedelta(days=30))


def test_the_future_perturbation_check_catches_a_reader_that_skips_the_filter(session):
    rng = random.Random(SEED)
    seed_facts(session, rng)
    cutoff = datetime(2025, 9, 1)

    def leaky_reader():
        # include_future=True is the escape hatch that exists for admin views only.
        return facts_known_as_of(session, FactKind.NEWS, as_of=cutoff, symbol="AAA", include_future=True)

    with pytest.raises(LookAheadFinding, match="changed when only data after the cutoff changed"):
        assert_independent_of_future(leaky_reader, lambda: rewrite_future_facts(session, cutoff, rng), "leaky reader")

    # and the probe alone catches a reader that returns a future-stamped fact
    probe = Probe()
    with pytest.raises(LookAheadFinding, match="after the cutoff"):
        probe.check("leaky", leaky_reader(), cutoff)


def test_a_reader_is_stable_when_only_the_microsecond_after_the_cutoff_changes(session):
    # known_at == cutoff is visible (readers use <=), one microsecond later is not.
    at = datetime(2025, 7, 1, 12, 0, 0)
    _add(session, FactKind.NEWS, "AAA", "edge", at, {"headline": "on the line"})
    _add(session, FactKind.NEWS, "AAA", "after", at + timedelta(microseconds=1), {"headline": "one tick later"})
    session.commit()
    assert [f.payload["headline"] for f in facts_known_as_of(session, FactKind.NEWS, as_of=at)] == ["on the line"]


# ===================================================================== discovery


# Public names containing "as_of" that are not readers of dated data.
NOT_READERS = {
    "app.knowledge.point_in_time.current_as_of": "returns the moment itself",
    "app.knowledge.point_in_time.simulated_as_of": "returns the moment itself",
    "app.analysis.expected_move.days_to_expiration": "arithmetic on an option expiry; its as_of is a plain date, not a reader",
}


def discover_readers() -> set[str]:
    """Every public function in app.* named *_as_of or taking an `as_of` argument."""
    found: set[str] = set()
    for info in pkgutil.walk_packages(app_package.__path__, "app."):
        module = importlib.import_module(info.name)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            if name.endswith("_as_of") or "as_of" in inspect.signature(fn).parameters:
                found.add(f"{module.__name__}.{name}")
    return found


def test_every_reader_is_guarded():
    registered = {r.name for r in READERS}
    discovered = discover_readers() - set(NOT_READERS)
    unguarded = sorted(discovered - registered)
    assert not unguarded, (
        "These readers take an as-of moment but are not in READERS in tests/test_lookahead_guards.py, so nothing "
        "proves they cannot see the future. Add a Reader entry for each (the generic tests then cover it), or, if "
        f"it is not a reader of dated data, list it in NOT_READERS with the reason: {unguarded}"
    )
    stale = sorted(registered - discovered)
    assert not stale, f"READERS lists functions that no longer exist or take no as_of: {stale}"
    assert not (set(NOT_READERS) - discover_readers()), "NOT_READERS has an entry that is no longer discovered"


def test_the_discovery_would_notice_a_new_reader(monkeypatch):
    import app.knowledge.store as store

    def brand_new_reader_as_of(session, symbol, as_of=None):  # pragma: no cover - never called
        return []

    brand_new_reader_as_of.__module__ = store.__name__
    monkeypatch.setattr(store, "brand_new_reader_as_of", brand_new_reader_as_of, raising=False)
    assert f"{store.__name__}.brand_new_reader_as_of" in discover_readers()


# ================================================================ date boundaries

BOUNDARY_BOOK = PriceBook.from_frames({"AAA": frame_from(BOOK_DAYS, 100 + np.arange(len(BOOK_DAYS), dtype=float))})


def last_visible_bar(moment: datetime) -> date:
    with as_of(moment):
        bars = BacktestDataProvider(BOUNDARY_BOOK).get_ohlcv("AAA", period="5d")
    return pd.Timestamp(bars["date"].iloc[-1]).date()


def previous_trading_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_us_trading_day(day):
        day -= timedelta(days=1)
    return day


ALL_2026 = trading_days(date(2026, 1, 2), date(2026, 12, 31))


def test_on_every_2026_trading_day_the_bar_appears_exactly_when_the_session_has_closed():
    """Covers both daylight-saving changes, both early closes (28 Nov, 24 Dec) and every holiday gap."""
    assert any(d in us_early_closes(2026) for d in ALL_2026)
    for day in ALL_2026:
        before_close = close_moment(day) - timedelta(minutes=1)
        assert last_visible_bar(decision_moment(day)) == previous_trading_day(day), day
        assert last_visible_bar(before_close) == previous_trading_day(day), day
        assert last_visible_bar(close_moment(day)) == day, day


@pytest.mark.parametrize(
    "day, utc_decision, utc_close",
    [
        (date(2026, 3, 6), datetime(2026, 3, 6, 14, 45), datetime(2026, 3, 6, 21, 30)),  # Friday, EST
        (date(2026, 3, 9), datetime(2026, 3, 9, 13, 45), datetime(2026, 3, 9, 20, 30)),  # Monday after the clocks went forward
        (date(2026, 10, 30), datetime(2026, 10, 30, 13, 45), datetime(2026, 10, 30, 20, 30)),  # Friday, EDT
        (date(2026, 11, 2), datetime(2026, 11, 2, 14, 45), datetime(2026, 11, 2, 21, 30)),  # Monday after they went back
        (date(2026, 11, 27), datetime(2026, 11, 27, 14, 45), datetime(2026, 11, 27, 18, 30)),  # early close: 13:30 ET
    ],
)
def test_decision_and_close_moments_follow_new_york_time_across_dst_and_early_closes(day, utc_decision, utc_close):
    assert decision_moment(day) == utc_decision and close_moment(day) == utc_close


def test_weekend_and_holiday_decision_days_see_only_the_last_session():
    friday, monday = date(2026, 5, 22), date(2026, 5, 26)  # 25 May is Memorial Day
    assert last_visible_bar(et(2026, 5, 23, 12)) == friday  # Saturday noon
    assert last_visible_bar(et(2026, 5, 24, 23, 59)) == friday  # Sunday night
    assert last_visible_bar(et(2026, 5, 25, 12)) == friday  # the holiday itself: no session, nothing new
    assert last_visible_bar(decision_moment(monday)) == friday  # Tuesday's decision, bars through Friday
    assert last_visible_bar(close_moment(monday)) == monday
    mlk = date(2026, 1, 19)
    assert not is_us_trading_day(mlk) and last_visible_bar(decision_moment(date(2026, 1, 20))) == date(2026, 1, 16)


def test_midnight_utc_is_not_the_end_of_the_new_york_day(session):
    june_1 = date(2026, 6, 1)
    stamp = end_of_local_day_utc(june_1)
    assert stamp == datetime(2026, 6, 2, 3, 59, 59, 999999)  # EDT: UTC-4
    _add(session, FactKind.FINRA_SHORT_VOLUME, "AAA", "d", stamp, {"short_volume": 1.0, "total_volume": 2.0, "ratio": 0.5}, basis="derived")
    session.commit()
    seen = lambda moment: len(facts_known_as_of(session, FactKind.FINRA_SHORT_VOLUME, as_of=moment))  # noqa: E731
    assert seen(datetime(2026, 6, 2, 0, 0)) == 0  # midnight UTC = 8 pm in New York: the day is not over
    assert seen(datetime(2026, 6, 2, 3, 59)) == 0
    assert seen(datetime(2026, 6, 2, 4, 0)) == 1
    winter = end_of_local_day_utc(date(2026, 12, 1))
    assert winter == datetime(2026, 12, 2, 4, 59, 59, 999999)  # EST: UTC-5


def test_a_new_york_stamp_read_as_utc_would_leak_and_the_conversion_prevents_it(session):
    stamped = datetime(2026, 6, 1, 20, 30)  # a source's local time: 8:30 pm New York
    converted = source_time_to_utc(stamped, NEW_YORK)
    assert converted == datetime(2026, 6, 2, 0, 30)
    _add(session, FactKind.NEWS, "AAA", "x", converted, {"headline": "x"})
    session.commit()
    just_before = datetime(2026, 6, 2, 0, 0)  # 8:00 pm New York: the item did not exist yet
    assert facts_known_as_of(session, FactKind.NEWS, as_of=just_before) == []
    # the mistake: calling 20:30 "UTC" would put it at 4:30 pm New York, hours too early
    assert stamped < just_before


@pytest.mark.parametrize("day", [date(2026, 3, 6), date(2026, 3, 9), date(2026, 11, 2), date(2026, 7, 14)])
def test_a_filing_accepted_at_17_59_versus_18_01_eastern(session, day):
    a = et(day.year, day.month, day.day, 17, 59, 0)
    b = et(day.year, day.month, day.day, 18, 1, 0)
    _add(session, FactKind.INSIDER_TRADE, "AAA", "a", a, {"accession": "a", "table": "non_derivative", "code": "P"})
    _add(session, FactKind.INSIDER_TRADE, "AAA", "b", b, {"accession": "b", "table": "non_derivative", "code": "P"})
    session.commit()

    def accessions(moment):
        # Inside the simulated moment: a live read can never be later than now, and some of these days are ahead of it.
        with as_of(moment):
            return sorted(f.payload["accession"] for f in facts_known_as_of(session, FactKind.INSIDER_TRADE))

    assert accessions(a - timedelta(seconds=1)) == []
    assert accessions(a) == ["a"]  # accepted exactly at the cutoff: visible
    assert accessions(et(day.year, day.month, day.day, 17, 59, 59)) == ["a"]
    assert accessions(et(day.year, day.month, day.day, 18, 0, 59)) == ["a"]
    assert accessions(b) == ["a", "b"]
    next_day = day + timedelta(days=1)
    while not is_us_trading_day(next_day):
        next_day += timedelta(days=1)
    assert accessions(decision_moment(next_day)) == ["a", "b"]  # 09:45 the next session sees both
    same_day_decision = decision_moment(day) if is_us_trading_day(day) else None
    assert same_day_decision is None or accessions(same_day_decision) == []  # ...and the same morning saw neither
    with as_of(decision_moment(next_day)):
        assert {t.accession for t in insider_trades_as_of(session, "AAA", codes=None)} == {"a", "b"}


def test_ambiguous_and_missing_local_times_resolve_to_the_later_instant():
    # 1:30 am on 1 Nov 2026 happens twice (EDT then EST): the later one, 06:30 UTC.
    assert source_time_to_utc(datetime(2026, 11, 1, 1, 30), NEW_YORK) == datetime(2026, 11, 1, 6, 30)
    # 2:30 am on 8 Mar 2026 never happens: read as EST, 07:30 UTC, never the earlier 06:30.
    assert source_time_to_utc(datetime(2026, 3, 8, 2, 30), NEW_YORK) == datetime(2026, 3, 8, 7, 30)


def test_weekly_bar_in_progress_never_shows_the_rest_of_its_week():
    rng = random.Random(SEED + 7)
    frames = {"AAA": random_book_frames(rng)["AAA"]}
    tuesday = date(2026, 6, 2)
    moment = close_moment(tuesday)  # Mon and Tue are known, Wed-Fri are not
    changed = perturb_future(frames, moment, rng)
    one = BacktestDataProvider(PriceBook.from_frames(frames))
    two = BacktestDataProvider(PriceBook.from_frames(changed))
    with as_of(moment):
        a, b = one.get_ohlcv("AAA", "1y", "1wk"), two.get_ohlcv("AAA", "1y", "1wk")
    assert a.equals(b)
    daily = frames["AAA"].set_index("date")
    week = daily.loc["2026-06-01":"2026-06-02"]
    last = a.iloc[-1]
    assert last["high"] == week["high"].max() and last["low"] == week["low"].min() and last["close"] == week["close"].iloc[-1]
    assert last["volume"] == week["volume"].sum()


# ================================================================ the engine

ENGINE_DAYS = trading_days_from(date(2024, 1, 2), 330)
ENGINE_START, ENGINE_END = ENGINE_DAYS[250], ENGINE_DAYS[272]
ENGINE_CLOSES = wiggly_uptrend(330, daily=0.01, dip=-0.01)


def engine_run(book: PriceBook, end: date, *, provider: BacktestDataProvider | None = None):
    settings = effective_settings(AppSettings(), SettingsOverrides(slippage_bps=10.0))
    params = BacktestParams(symbols=["AAA"], start=ENGINE_START, end=end)
    return runner.run_backtest(params, settings, book, provider=provider)


def result_signature(result) -> str:
    return json.dumps({"trades": result.trades, "equity": result.equity}, default=str, sort_keys=True)


def perturbed_engine_book(after: date, rng: random.Random) -> PriceBook:
    base = standard_book(ENGINE_DAYS, {"AAA": ENGINE_CLOSES})
    frames = {}
    for symbol, series in base.series.items():
        frame = series.frame.copy()
        mask = frame["date"] > pd.Timestamp(after)
        for column in ("open", "high", "low", "close", "volume"):
            frame.loc[mask, column] = frame.loc[mask, column] * np.array([rng.uniform(0.4, 2.5) for _ in range(int(mask.sum()))])
        frames[symbol] = frame
    return PriceBook.from_frames(frames)


def test_a_whole_backtest_is_unchanged_when_only_bars_after_its_last_day_change():
    rng = random.Random(SEED + 11)
    honest = standard_book(ENGINE_DAYS, {"AAA": ENGINE_CLOSES})
    changed = perturbed_engine_book(ENGINE_END, rng)
    assert not changed.series["AAA"].frame.equals(honest.series["AAA"].frame)
    first = engine_run(honest, ENGINE_END)
    second = engine_run(changed, ENGINE_END)
    assert first.trades, "the fixture should trade, or this test proves nothing"
    assert result_signature(first) == result_signature(second)


def test_a_shorter_run_is_the_prefix_of_a_longer_one():
    """Days are decided one at a time from what is known then, so extending the run cannot change the past."""
    book = standard_book(ENGINE_DAYS, {"AAA": ENGINE_CLOSES})
    short_end = ENGINE_DAYS[262]
    short = engine_run(book, short_end)
    long = engine_run(book, ENGINE_END)
    days = {point["day"] for point in short.equity}
    assert days and max(days) == short_end
    assert [p for p in long.equity if p["day"] in days] == short.equity
    short_entries = [(t["symbol"], t["entry_date"], t["entry_price"], t["shares"]) for t in short.trades]
    long_entries = [(t["symbol"], t["entry_date"], t["entry_price"], t["shares"]) for t in long.trades if t["entry_date"] <= short_end]
    assert short_entries == long_entries


def test_every_request_made_during_a_backtest_passes_the_probe():
    book = standard_book(ENGINE_DAYS, {"AAA": ENGINE_CLOSES})
    provider = BacktestDataProvider(book, record_calls=True)
    engine_run(book, ENGINE_END, provider=provider)
    assert len(provider.calls) > 50
    probe = Probe(strict=False)
    for call in provider.calls:
        if call.last_bar_day is not None:
            probe.check(
                f"{call.method} {call.symbol}", pd.DataFrame({"date": [pd.Timestamp(call.last_bar_day)]}), call.as_of
            )
    assert probe.observed == sum(1 for c in provider.calls if c.last_bar_day is not None)
    assert not probe.problems, probe.problems[:3]

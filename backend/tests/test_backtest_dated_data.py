"""The dated, non-price data a backtest can rebuild: revenue by SEC filing date,
earnings reports by report day, insider buying by Form 4 acceptance time, and the
52-week range from prices. Everything runs on fixtures and synthetic bars; nothing
touches the network."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.earnings_history_scoring import (
    MIN_QUARTERS_FOR_TRACK_RECORD,
    score_earnings_surprise_track_record,
)
from app.analysis.fundamental_scoring import score_fundamentals
from app.backtest import runner
from app.backtest.calendar import decision_moment
from app.backtest.coverage import FUNDAMENTALS_REACHABLE_POINTS, describe_coverage
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.dated_sources import FactBackedSources
from app.backtest.earnings_history import (
    backfill_earnings,
    earnings_history_as_of,
    ingest_earnings_reports,
    parse_earnings_frame,
)
from app.backtest.fundamentals_history import revenue_history_as_of
from app.backtest.params import BacktestInputError, BacktestParams, SettingsOverrides, effective_settings
from app.config import AppSettings
from app.data_providers import sec_xbrl
from app.data_providers.base import AllProvidersFailedError, CompanyOverview, DataProviderError, FinancialYear
from app.data_providers.sec_form4 import ingest_form4_filing
from app.knowledge import FactKind, KnownFact, as_of, end_of_local_day_utc
from tests.backtest_helpers import frame_from, standard_book, trading_days_from, wiggly_uptrend
from tests.test_sec_form4 import filing_of, xml_of

FIXTURES = Path(__file__).parent / "fixtures"
ET = ZoneInfo("America/New_York")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def engine():
    return _engine()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _moment(day: date, hour: int = 9, minute: int = 45) -> datetime:
    """A New York wall-clock time as the naive UTC the simulation uses."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


# --------------------------------------------------------------------------- SEC XBRL parsing


def _aapl_rows():
    revenue = sec_xbrl.parse_annual_revenue(_fixture("sec_xbrl_aapl_revenue.json"), "RevenueFromContractWithCustomerExcludingAssessedTax")
    legacy = sec_xbrl.parse_annual_revenue(_fixture("sec_xbrl_aapl_salesrevenuenet.json"), "SalesRevenueNet")
    return revenue + legacy


def test_only_whole_year_10k_values_are_kept():
    document = _fixture("sec_xbrl_aapl_revenue.json")
    raw = document["units"]["USD"]
    assert any(row["form"] == "10-Q" for row in raw) and any(row["end"] == "2018-12-29" for row in raw)  # the fixture holds quarters too
    rows = sec_xbrl.parse_annual_revenue(document, "RevenueFromContractWithCustomerExcludingAssessedTax")
    assert len(rows) == 14  # fiscal 2018-2022, each in up to three 10-Ks; no quarters, no 10-Q
    assert {row.form for row in rows} == {"10-K"}
    assert all(350 <= (row.period_end - row.period_start).days <= 380 for row in rows)
    fy2020 = [row for row in rows if row.period_end == date(2020, 9, 26)]
    assert sorted(row.filed for row in fy2020) == [date(2020, 10, 30), date(2021, 10, 29), date(2022, 10, 28)]
    assert {row.value for row in fy2020} == {274_515_000_000.0}


def test_parse_ignores_rows_it_cannot_trust():
    base = {"start": "2020-01-01", "end": "2020-12-31", "val": 10, "accn": "a", "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2021-02-01"}
    document = {
        "units": {
            "USD": [
                base,
                {**base, "val": "oops"},
                {**base, "filed": None},
                {**base, "start": None},
                {**base, "form": "10-Q"},
                {**base, "end": "2020-06-30"},  # half a year
            ]
        }
    }
    assert len(sec_xbrl.parse_annual_revenue(document, "Revenues")) == 1
    assert sec_xbrl.parse_annual_revenue({}, "Revenues") == []


class FakeSecClient:
    """Answers companyconcept URLs from a dict; a missing tag is a 404 like SEC's."""

    def __init__(self, documents: dict[str, dict] | None = None, fail_with: str | None = None):
        self.documents = documents or {}
        self.fail_with = fail_with
        self.urls: list[str] = []

    def get_json(self, url: str):
        self.urls.append(url)
        if self.fail_with:
            raise DataProviderError(self.fail_with)
        for concept, document in self.documents.items():
            if url.endswith(f"/{concept}.json"):
                return document
        raise DataProviderError(f"sec fetch failed for {url}: HTTP 404")


def test_fetch_tries_every_tag_and_skips_the_ones_the_company_never_used():
    client = FakeSecClient(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _fixture("sec_xbrl_aapl_revenue.json"),
            "SalesRevenueNet": _fixture("sec_xbrl_aapl_salesrevenuenet.json"),
        }
    )
    rows = sec_xbrl.fetch_annual_revenue(320193, client)
    assert len(client.urls) == len(sec_xbrl.REVENUE_CONCEPTS)
    assert all("CIK0000320193" in url for url in client.urls)
    assert {row.concept for row in rows} == {"RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"}


def test_a_broken_download_is_an_error_not_an_empty_history():
    with pytest.raises(DataProviderError):
        sec_xbrl.fetch_annual_revenue(320193, FakeSecClient(fail_with="sec fetch failed: HTTP 503"))


def test_ingest_dates_each_value_by_its_filing_day_and_is_idempotent(session):
    rows = _aapl_rows()
    created, existing = sec_xbrl.ingest_annual_revenue(session, "AAPL", 320193, rows)
    assert (created, existing) == (len(rows), 0)
    assert sec_xbrl.ingest_annual_revenue(session, "AAPL", 320193, rows) == (0, len(rows))

    fact = session.exec(
        select(KnownFact).where(KnownFact.kind == FactKind.FUNDAMENTALS_REVENUE, KnownFact.dedupe_key.contains("2020-09-26"))
        .order_by(KnownFact.known_at)
    ).first()
    assert fact.known_at == end_of_local_day_utc(date(2020, 10, 30))
    assert fact.known_at_basis == "derived"
    assert fact.effective_at == datetime(2020, 9, 26)
    assert fact.payload["value"] == 274_515_000_000.0 and fact.payload["form"] == "10-K"


def test_backfill_reports_unknown_symbols_and_failures_without_stopping(session):
    client = FakeSecClient({"RevenueFromContractWithCustomerExcludingAssessedTax": _fixture("sec_xbrl_aapl_revenue.json")})

    def resolver(symbol, client=None):
        if symbol == "NOPE":
            return None
        if symbol == "BOOM":
            raise DataProviderError("ticker file down")
        return 320193

    progress: list[str] = []
    report = sec_xbrl.backfill_fundamentals(
        session, ["aapl", "NOPE", "BOOM"], client=client, cik_resolver=resolver, progress=lambda s, i, n: progress.append(s)
    )
    assert report.symbols == 3 and report.facts_created == 14
    assert report.unknown_symbols == ["NOPE"]
    assert len(report.errors) == 1 and "BOOM" in report.errors[0]
    assert progress == ["AAPL", "NOPE", "BOOM"]
    # a registrant with no revenue under any tag is told apart from an unknown symbol
    empty = sec_xbrl.backfill_fundamentals(session, ["AAPL"], client=FakeSecClient({}), cik_resolver=lambda s, client=None: 1)
    assert empty.no_revenue_symbols == ["AAPL"]


# --------------------------------------------------------------------------- revenue as of a moment


@pytest.fixture
def aapl(session):
    sec_xbrl.ingest_annual_revenue(session, "AAPL", 320193, _aapl_rows())
    return session


def test_a_year_is_unknown_until_its_filing_day_is_over(aapl):
    filed = date(2020, 10, 30)  # the 10-K for the year to 26 Sep 2020
    morning = revenue_history_as_of(aapl, "AAPL", _moment(filed))
    assert morning[-1].year == 2019 and morning[-1].revenue == 260_174_000_000.0
    evening = revenue_history_as_of(aapl, "AAPL", _moment(filed, 20, 0))  # filing day not over in New York? 20:00 < 23:59
    assert evening[-1].year == 2019
    next_morning = revenue_history_as_of(aapl, "AAPL", _moment(date(2020, 11, 2)))
    assert [y.year for y in next_morning] == [2017, 2018, 2019, 2020]
    assert next_morning[-1].revenue == 274_515_000_000.0
    assert all(math.isnan(y.net_income) for y in next_morning)  # not stored, so not invented


def test_nothing_is_known_before_the_first_filing(aapl):
    assert revenue_history_as_of(aapl, "AAPL", _moment(date(2016, 10, 1))) == []
    assert revenue_history_as_of(aapl, "MSFT", _moment(date(2024, 1, 3))) == []


def test_a_restated_year_keeps_its_original_value_until_the_restating_filing_is_public(aapl):
    # Synthetic: the 10-K filed 2023-11-03 restates the year to Sep 2021 upwards.
    restated = sec_xbrl.AnnualRevenue(
        concept="RevenueFromContractWithCustomerExcludingAssessedTax", period_start=date(2020, 9, 27),
        period_end=date(2021, 9, 25), value=370_000_000_000.0, filed=date(2023, 11, 20), accession="restating-10k-a",
        form="10-K/A", fiscal_year=2023, fiscal_period="FY",
    )
    sec_xbrl.ingest_annual_revenue(aapl, "AAPL", 320193, [restated])

    def fy2021(moment):
        years = revenue_history_as_of(aapl, "AAPL", moment)
        return next(y.revenue for y in years if y.year == 2021)

    assert fy2021(_moment(date(2022, 11, 1))) == 365_817_000_000.0
    assert fy2021(_moment(date(2023, 11, 20))) == 365_817_000_000.0  # filed that day, public only from its end
    assert fy2021(_moment(date(2023, 11, 21))) == 370_000_000_000.0


def test_a_missing_year_breaks_the_chain_instead_of_comparing_two_years_as_one(session):
    rows = [r for r in _aapl_rows() if r.period_end.year != 2019]  # no fiscal 2019 stored
    sec_xbrl.ingest_annual_revenue(session, "AAPL", 320193, rows)
    years = revenue_history_as_of(session, "AAPL", _moment(date(2022, 12, 1)))
    assert [y.year for y in years] == [2020, 2021, 2022]  # 2017/2018 sit behind the hole and are dropped


def test_a_history_that_stopped_long_ago_counts_as_missing(aapl):
    assert revenue_history_as_of(aapl, "AAPL", _moment(date(2027, 1, 4))) == []


def test_same_day_filings_resolve_the_same_way_whatever_the_row_order(session):
    def row(concept, value, accession):
        return sec_xbrl.AnnualRevenue(
            concept=concept, period_start=date(2022, 1, 1), period_end=date(2022, 12, 31), value=value,
            filed=date(2023, 2, 1), accession=accession, form="10-K", fiscal_year=2022, fiscal_period="FY",
        )

    sec_xbrl.ingest_annual_revenue(session, "ZZZ", 1, [row("Revenues", 111.0, "a1"), row("RevenueFromContractWithCustomerExcludingAssessedTax", 222.0, "a1")])
    years = revenue_history_as_of(session, "ZZZ", _moment(date(2023, 3, 1)))
    assert years[-1].revenue == 222.0  # the tag listed first wins a same-day tie


def test_the_revenue_reader_never_returns_a_filing_made_after_the_moment(aapl):
    """Brute-force oracle: for many moments, every returned value must come from a
    filing public by then, and be the newest such filing for its year."""
    all_rows = [f for f in aapl.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUNDAMENTALS_REVENUE)).all()]
    for day in pd.date_range("2016-09-01", "2024-12-31", freq="17D"):
        moment = _moment(day.date())
        years = revenue_history_as_of(aapl, "AAPL", moment)
        public = [f for f in all_rows if f.known_at <= moment]
        by_end: dict[str, list[KnownFact]] = {}
        for f in public:
            by_end.setdefault(f.payload["period_end"], []).append(f)
        for y in years:
            candidates = [f for end, fs in by_end.items() if end.startswith(str(y.year)) for f in fs]
            assert candidates, f"{day.date()}: {y.year} returned with no public filing"
            newest = max(candidates, key=lambda f: (f.payload["filed"], f.payload["accession"]))
            assert y.revenue == newest.payload["value"]
            assert newest.known_at <= moment


# --------------------------------------------------------------------------- fundamentals score, same function as live


def test_the_backtest_overview_and_revenue_feed_the_live_fundamentals_scorer(aapl):
    days = trading_days_from(date(2020, 1, 2), 330)
    closes = np.linspace(100, 160, 330)  # a steady climb: the latest close is the 52-week high
    book = PriceBook.from_frames({"AAPL": frame_from(days, closes)})
    sources = FactBackedSources(lambda: Session(aapl.get_bind()), fundamentals=True)
    provider = BacktestDataProvider(book, dated_sources=sources, overview_from_prices=True)
    decision_day = days[300]
    with as_of(decision_moment(decision_day)):
        overview = provider.get_company_overview("AAPL")
        years = provider.get_financials("AAPL").years
        price = provider.get_quote("AAPL").price
    assert years[-1].revenue > years[-2].revenue
    long_score, long_reasons = score_fundamentals(overview, years, None, price, "long")
    short_score, _ = score_fundamentals(overview, years, None, price, "short")
    assert (long_score, short_score) == (2, -2)
    assert any("revenue grew" in r for r in long_reasons) and any("52-week high" in r for r in long_reasons)

    # The very same call with the equivalent hand-made live objects gives the identical answer.
    live_overview = CompanyOverview("AAPL", "Apple", 1e12, 30.0, 1e11, 6.0, overview.week52_low, overview.week52_high)
    assert score_fundamentals(live_overview, years, None, price, "long") == (long_score, long_reasons)


def test_two_reachable_fundamentals_points_is_what_the_scorer_can_actually_award():
    overview = CompanyOverview("X", "X", None, None, None, None, 50.0, 100.0)
    growing = [FinancialYear(2022, 100.0, 0.0), FinancialYear(2023, 200.0, 0.0)]
    best = max(
        score_fundamentals(overview, growing, None, price, "long")[0] for price in (50.0, 52.0, 75.0, 98.0, 100.0)
    )
    assert best == FUNDAMENTALS_REACHABLE_POINTS == 2


# --------------------------------------------------------------------------- 52-week range from prices


def _price_provider(poison_day: date | None = None):
    days = trading_days_from(date(2022, 1, 3), 700)
    closes = 100 + np.arange(len(days), dtype=float)
    frame = frame_from(days, closes, opens=closes - 0.25, highs=closes + 0.5, lows=closes - 0.5)
    if poison_day is not None:
        frame.loc[frame["date"] == pd.Timestamp(poison_day), "high"] = 9999.0
    book = PriceBook.from_frames({"AAA": frame})
    return days, closes, BacktestDataProvider(book, overview_from_prices=True)


def test_52_week_range_comes_from_the_bars_known_at_the_moment():
    days, closes, provider = _price_provider()
    day = days[500]
    with as_of(decision_moment(day)):
        overview = provider.get_company_overview("AAA")
    last_known = 499  # the decision moment sees bars through the previous day
    assert overview.week52_high == pytest.approx(closes[last_known] + 0.5)
    window_start = int(np.searchsorted(np.array(days, dtype="datetime64[D]"), np.datetime64(day - pd.Timedelta(days=365).to_pytimedelta(), "D")))
    assert overview.week52_low == pytest.approx(closes[window_start] - 0.5)
    assert overview.market_cap is None and overview.pe_ratio is None and overview.revenue_ttm is None


def test_a_spike_on_the_decision_day_itself_is_not_in_the_52_week_high():
    days, _, provider = _price_provider(poison_day=None)
    day = days[500]
    days2, _, poisoned = _price_provider(poison_day=day)
    with as_of(decision_moment(day)):
        assert poisoned.get_company_overview("AAA").week52_high == provider.get_company_overview("AAA").week52_high
    with as_of(decision_moment(days2[501])):
        assert poisoned.get_company_overview("AAA").week52_high == 9999.0  # a day later it is history


def test_too_short_a_history_is_refused_rather_than_called_a_52_week_range():
    days, _, provider = _price_provider()
    with as_of(decision_moment(days[100])):
        with pytest.raises(AllProvidersFailedError):
            provider.get_company_overview("AAA")


def test_without_the_switch_the_overview_stays_unavailable():
    days, _, _ = _price_provider()
    bare = BacktestDataProvider(_price_provider()[2]._book)
    with as_of(decision_moment(days[500])):
        with pytest.raises(AllProvidersFailedError):
            bare.get_company_overview("AAA")


# --------------------------------------------------------------------------- earnings reports


def _earnings_frame() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            "2025-07-31 16:00", "2025-05-01 16:00", "2025-01-30 16:00", "2024-10-31 16:00", "2024-08-01 16:00", "2024-05-02 16:00",
        ],
        tz="America/New_York",
    )
    return pd.DataFrame(
        {
            "EPS Estimate": [1.60, 1.50, 2.30, 1.40, 1.30, 1.25],
            "Reported EPS": [np.nan, 1.65, 2.40, 1.46, 1.40, 1.30],  # newest row is an upcoming report
            "Surprise(%)": [np.nan, 10.0, 4.3, 4.3, 7.7, 4.0],
        },
        index=index,
    )


def test_only_reported_quarters_are_parsed_newest_first():
    entries = parse_earnings_frame(_earnings_frame())
    assert [e.date for e in entries][:2] == [date(2025, 5, 1), date(2025, 1, 30)]
    assert len(entries) == 5 and entries[0].eps_actual == 1.65 and entries[0].surprise_pct == 10.0
    assert parse_earnings_frame(None) == [] and parse_earnings_frame(pd.DataFrame()) == []


def test_a_report_is_public_from_the_end_of_its_report_day(session):
    ingest_earnings_reports(session, "AAA", parse_earnings_frame(_earnings_frame()))
    report_day = date(2025, 5, 1)
    assert [e.date for e in earnings_history_as_of(session, "AAA", _moment(report_day, 23, 0))][0] == date(2025, 1, 30)
    after = earnings_history_as_of(session, "AAA", _moment(date(2025, 5, 2)))
    assert after[0].date == report_day and after[0].surprise_pct == 10.0
    assert earnings_history_as_of(session, "AAA", _moment(date(2024, 5, 2))) == []  # that day's own report is not yet public


def test_ingest_is_idempotent_and_backfill_survives_a_failing_symbol(session):
    entries = parse_earnings_frame(_earnings_frame())
    assert ingest_earnings_reports(session, "AAA", entries) == (5, 0)
    assert ingest_earnings_reports(session, "AAA", entries) == (0, 5)

    def fetcher(symbol, rows):
        if symbol == "BAD":
            raise RuntimeError("yahoo said no")
        if symbol == "NONE":
            return pd.DataFrame()
        return _earnings_frame()

    report = backfill_earnings(session, ["BBB", "BAD", "NONE"], fetcher=fetcher)
    assert report.facts_created == 5 and report.no_history_symbols == ["NONE"]
    assert len(report.errors) == 1 and "BAD" in report.errors[0]


def test_the_track_record_needs_four_known_quarters_and_uses_the_live_scorer(session):
    ingest_earnings_reports(session, "AAA", parse_earnings_frame(_earnings_frame()))
    early = earnings_history_as_of(session, "AAA", _moment(date(2024, 11, 5)))  # three quarters reported so far
    assert len(early) == 3 < MIN_QUARTERS_FOR_TRACK_RECORD
    assert score_earnings_surprise_track_record("long", early) == (0, [])
    late = earnings_history_as_of(session, "AAA", _moment(date(2025, 6, 2)))
    assert len(late) == 5 >= MIN_QUARTERS_FOR_TRACK_RECORD
    score, reasons = score_earnings_surprise_track_record("long", late)
    assert score == 1 and "beat consensus EPS in 5/5" in reasons[0]
    assert score_earnings_surprise_track_record("short", late)[0] == -1


# --------------------------------------------------------------------------- insiders through the provider


def test_insider_buying_reaches_the_provider_only_from_the_filings_acceptance_time(engine):
    with Session(engine) as session:
        for name in ("unh_000140", "unh_000142", "unh_000146"):
            ingest_form4_filing(session, "UNH", filing_of(name), xml=xml_of(name))
    sources = FactBackedSources(lambda: Session(engine), insiders=True)
    provider = BacktestDataProvider(PriceBook.from_frames({}), dated_sources=sources)
    with as_of(datetime(2025, 5, 16, 16, 0)):
        assert provider.get_insider_activity("UNH") is None  # nothing accepted yet: data absent, not "quiet"
    with as_of(datetime(2025, 5, 16, 16, 25)):
        first = provider.get_insider_activity("UNH")
    with as_of(datetime(2025, 5, 20)):
        later = provider.get_insider_activity("UNH")
    assert first is not None and later is not None
    assert later.buy_count > first.buy_count >= 1
    with as_of(datetime(2025, 5, 20)):
        assert provider.get_insider_activity("AAPL") is None  # never loaded for this symbol


# --------------------------------------------------------------------------- switches, coverage


def test_a_part_that_is_off_has_no_handler_at_all(engine):
    only_earnings = FactBackedSources(lambda: Session(engine), earnings=True)
    assert hasattr(only_earnings, "earnings_history")
    assert not hasattr(only_earnings, "financials") and not hasattr(only_earnings, "insider_activity")
    provider = BacktestDataProvider(PriceBook.from_frames({}), dated_sources=only_earnings)
    with as_of(datetime(2025, 5, 20)):
        with pytest.raises(AllProvidersFailedError):
            provider.get_financials("AAA")
        assert provider.get_insider_activity("AAA") is None
        assert provider.get_earnings_date("AAA") is None  # the next report date is never answered


def test_coverage_counts_only_the_parts_a_run_switched_on():
    base = describe_coverage(16)
    assert base["profile"] == "price_only" and base["achievable_points"] == 9
    assert base["included"] == {"fundamentals": False, "insiders": False, "earnings": False}

    fundamentals = describe_coverage(16, include_fundamentals=True)
    assert fundamentals["achievable_points"] == 9 + FUNDAMENTALS_REACHABLE_POINTS
    assert fundamentals["profile"] == "price_plus_dated_data"
    active = {p["part"]: p for p in fundamentals["active_parts"]}
    assert active["fundamentals"]["live_points_max"] == 3
    assert "fundamentals" not in {p["part"] for p in fundamentals["inactive_parts"]}

    everything = describe_coverage(16, include_fundamentals=True, include_insiders=True, include_earnings=True)
    assert everything["achievable_points"] == 14
    assert everything["achievable_max_confidence_pct"] == 44
    inactive = {p["part"] for p in everything["inactive_parts"]}
    assert {"news", "options", "earnings_date", "macro_event", "ai_overlay", "expected_move"} <= inactive
    assert everything["bar_points_needed"] == 5 and everything["bar_share_of_achievable"] == pytest.approx(5 / 14)


# --------------------------------------------------------------------------- the run


DAYS = trading_days_from(date(2024, 1, 2), 330)
START, END = DAYS[250], DAYS[280]
CLOSES = wiggly_uptrend(330, daily=0.01, dip=-0.01)


def _settings() -> AppSettings:
    return effective_settings(AppSettings(), SettingsOverrides(slippage_bps=10.0))


def _book() -> PriceBook:
    return standard_book(DAYS, {"AAA": CLOSES, "BBB": CLOSES})


def _revenue_facts(engine, symbol: str):
    def row(year, value, filed):
        return sec_xbrl.AnnualRevenue(
            concept="Revenues", period_start=date(year, 1, 1), period_end=date(year, 12, 31), value=value,
            filed=filed, accession=f"{symbol}-{year}", form="10-K", fiscal_year=year, fiscal_period="FY",
        )

    with Session(engine) as session:
        sec_xbrl.ingest_annual_revenue(session, symbol, 1, [row(2022, 80e9, date(2023, 2, 15)), row(2023, 100e9, date(2024, 2, 15))])


def test_asking_for_dated_data_without_a_fact_database_is_an_error():
    params = BacktestParams(symbols=["AAA"], start=START, end=END, include_fundamentals=True)
    with pytest.raises(BacktestInputError, match="stored facts"):
        runner.run_backtest(params, _settings(), _book())


def test_dated_parts_change_points_only_where_the_data_exists(engine):
    _revenue_facts(engine, "AAA")  # BBB has prices but no stored filings
    book = _book()
    plain = runner.run_backtest(BacktestParams(symbols=["AAA", "BBB"], start=START, end=END), _settings(), book)
    rich = runner.run_backtest(
        BacktestParams(symbols=["AAA", "BBB"], start=START, end=END, include_fundamentals=True),
        _settings(), book, fact_session_factory=lambda: Session(engine),
    )
    assert plain.trades and rich.trades
    assert all(t["scores"]["fundamental_score"] == 0 for t in plain.trades)
    assert plain.coverage["profile"] == "price_only"

    by_symbol = {}
    for t in rich.trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    aaa = by_symbol["AAA"][0]
    assert "revenue grew 25.0% YoY" in aaa["signal_reasons"]
    assert aaa["scores"]["fundamental_score"] >= 1
    for t in by_symbol.get("BBB", []):
        assert "revenue grew" not in (t["signal_reasons"] or "")  # no filings stored: no revenue point, only the price-range part
        assert t["scores"]["fundamental_score"] <= 1

    assert rich.coverage["profile"] == "price_plus_dated_data" and rich.coverage["achievable_points"] == 11
    availability = rich.summary["dated_data"]["fundamentals"]
    assert availability["symbols_with_data"] == 1 and 0 < availability["answered"] < availability["requests"]
    assert "dated_data" not in plain.summary


def test_a_run_with_the_dated_parts_on_is_repeatable(engine):
    _revenue_facts(engine, "AAA")
    params = BacktestParams(
        symbols=["AAA"], start=START, end=END, include_fundamentals=True, include_insiders=True, include_earnings=True
    )

    def go():
        return runner.run_backtest(params, _settings(), _book(), fact_session_factory=lambda: Session(engine))

    first, second = go(), go()
    assert first.trades == second.trades and first.equity == second.equity
    assert first.summary == second.summary
    assert first.coverage["included"] == {"fundamentals": True, "insiders": True, "earnings": True}
    assert json.loads(runner.params_to_json(params, _settings(), _book()))["include_insiders"] is True


def test_the_random_entry_baseline_stays_price_only(engine):
    params = BacktestParams(symbols=["AAA"], start=START, end=DAYS[251], include_fundamentals=True)
    # A replacement entry policy never scores, so no fact database is needed and the coverage says so.
    result = runner.run_backtest(params, _settings(), _book(), entry_policy=lambda *a, **k: _no_trade())
    assert result.coverage["profile"] == "price_only" and "dated_data" not in result.summary


def _no_trade():
    from types import SimpleNamespace

    return SimpleNamespace(direction=None, reason="test", confidence_points=0)


def test_facts_of_other_kinds_do_not_leak_into_the_readers(session):
    from app.knowledge import make_dedupe_key, record_fact

    record_fact(
        session, kind=FactKind.NEWS, symbol="AAA", source="t", dedupe_key=make_dedupe_key("n"),
        known_at=datetime(2024, 1, 1), payload={"period_end": "2023-12-31", "value": 5, "filed": "2024-01-01"},
    )
    assert revenue_history_as_of(session, "AAA", datetime(2024, 6, 1)) == []
    assert earnings_history_as_of(session, "AAA", datetime(2024, 6, 1)) == []

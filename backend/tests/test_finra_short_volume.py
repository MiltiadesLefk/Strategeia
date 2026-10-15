"""FINRA short-sale volume, the silent-signal mechanism and what they must never touch."""

from __future__ import annotations

import gzip
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.short_volume_scoring import (
    HIGH_RATIO_DELTA,
    SIGNAL_NAME,
    build_short_volume_signal,
    score_short_volume,
)
from app.api.deps import get_session
from app.api.routers.signals import get_finra_provider
from app.data_providers.base import DataProviderError
from app.data_providers.finra_provider import FinraProvider, parse_short_volume_file
from app.knowledge import FactKind, KnownFact, as_of, end_of_local_day_utc, facts_known_as_of
from app.main import app
from app.signals.finra import (
    MIN_BASELINE_DAYS,
    ShortVolumeReading,
    ingest_finra_short_volume,
    reading_is_fresh,
    refresh_finra_short_volume,
    short_volume_ratio_as_of,
)

# A real excerpt of CNMSshvol20260930.txt (volumes are fractional in the live file),
# plus the oddities the parser must survive: blank line, a short row, a non-numeric
# row, a negative row and a trailer.
REAL_EXCERPT = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260930|A|388551.214956|45|1009929.273351|B,Q,N
20260930|AAPL|6527108.672426|27741|15902818.258075|B,Q,N

20260930|BRK/B|1053721.925737|4383|1815854.883029|B,Q,N
20260930|NVDA|16496296.951734|110002|38738416.879772|B,Q,N
20260930|BAD|abc|0|100|Q
20260930|NEG|-5|0|100|Q
20260930|SHORTROW|1|2
12345
"""


def _day_text(day: date, rows: dict[str, tuple[float, float]]) -> str:
    lines = ["Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"]
    for symbol, (short, total) in rows.items():
        lines.append(f"{day.strftime('%Y%m%d')}|{symbol}|{short}|0|{total}|Q")
    return "\n".join(lines) + "\n"


class FakeFinra:
    """A FinraProvider stand-in keyed by date; counts what was asked for."""

    def __init__(self, files: dict[date, dict[str, tuple[float, float]]] | None = None):
        self.files = files or {}
        self.asked: list[date] = []

    def url_for(self, day):
        return f"https://example.invalid/{day.isoformat()}"

    def get_short_volume(self, symbols, day):
        self.asked.append(day)
        if day not in self.files:
            return None
        wanted = {s.upper() for s in symbols}
        rows = parse_short_volume_file(_day_text(day, self.files[day]))
        return {s: r for s, r in rows.items() if s in wanted}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _weekdays(end: date, count: int) -> list[date]:
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


# ---- parser and provider -------------------------------------------------------------


def test_parser_reads_a_real_excerpt_and_skips_oddities():
    rows = parse_short_volume_file(REAL_EXCERPT)
    assert set(rows) == {"A", "AAPL", "BRK/B", "NVDA"}
    aapl = rows["AAPL"]
    assert aapl.trade_date == date(2026, 9, 30)
    assert aapl.short_exempt_volume == 27741
    assert aapl.ratio == pytest.approx(6527108.672426 / 15902818.258075)


def test_parser_zero_total_has_no_ratio_and_duplicates_keep_the_first():
    rows = parse_short_volume_file("20260930|X|0|0|0|Q\n20260930|Y|5|0|10|Q\n20260930|Y|9|0|10|Q\n")
    assert rows["X"].ratio is None
    assert rows["Y"].short_volume == 5


def test_provider_caches_a_downloaded_file_and_never_refetches(tmp_path):
    calls = []

    def http_get(url):
        calls.append(url)
        return 200, REAL_EXCERPT

    provider = FinraProvider(cache_dir=tmp_path, http_get=http_get)
    first = provider.get_short_volume(["aapl", "nvda", "zzzz"], date(2026, 9, 30))
    assert set(first) == {"AAPL", "NVDA"}  # an unlisted symbol is simply absent
    assert calls == ["https://cdn.finra.org/equity/regsho/daily/CNMSshvol20260930.txt"]
    assert gzip.decompress((tmp_path / "CNMSshvol20260930.txt.gz").read_bytes()).decode() == REAL_EXCERPT
    FinraProvider(cache_dir=tmp_path, http_get=http_get).get_short_volume(["AAPL"], date(2026, 9, 30))
    assert len(calls) == 1


def test_provider_missing_file_is_none_and_old_misses_are_remembered(tmp_path):
    calls = []

    def http_get(url):
        calls.append(url)
        return 403, ""

    provider = FinraProvider(cache_dir=tmp_path, http_get=http_get, pacing_seconds=0)
    today = date(2026, 10, 1)
    assert provider.fetch_day(date(2026, 9, 27), today=today) is None  # recent miss: may not be posted yet
    assert provider.fetch_day(date(2026, 9, 27), today=today) is None
    assert len(calls) == 2
    assert provider.fetch_day(date(2026, 1, 1), today=today) is None  # old miss: permanent
    assert provider.fetch_day(date(2026, 1, 1), today=today) is None
    assert len(calls) == 3


def test_provider_server_error_and_empty_body_raise_and_cache_nothing(tmp_path):
    provider = FinraProvider(cache_dir=tmp_path, http_get=lambda url: (500, "oops"), pacing_seconds=0)
    with pytest.raises(DataProviderError):
        provider.fetch_day(date(2026, 9, 30))
    provider = FinraProvider(cache_dir=tmp_path, http_get=lambda url: (200, "Date|Symbol\n"), pacing_seconds=0)
    with pytest.raises(DataProviderError):
        provider.fetch_day(date(2026, 9, 30))
    assert list(tmp_path.iterdir()) == []


def test_provider_paces_between_downloads(tmp_path):
    sleeps = []
    provider = FinraProvider(
        cache_dir=tmp_path, http_get=lambda url: (200, REAL_EXCERPT), pacing_seconds=0.5, sleep=sleeps.append
    )
    provider.fetch_day(date(2026, 9, 29))
    provider.fetch_day(date(2026, 9, 30))
    assert sleeps == [0.5]


# ---- dated storage -------------------------------------------------------------------


def test_known_at_is_the_end_of_the_trading_day_in_eastern_time(session):
    day = date(2026, 9, 30)
    fake = FakeFinra({day: {"AAPL": (40.0, 100.0)}})
    ingest_finra_short_volume(session, ["AAPL"], day, day, fake)
    fact = session.exec(select(KnownFact)).one()
    assert fact.kind == FactKind.FINRA_SHORT_VOLUME
    assert fact.known_at == end_of_local_day_utc(day)
    assert fact.known_at.date() == date(2026, 10, 1)  # 23:59 EDT is already 03:59 UTC next day
    assert fact.known_at_basis == "derived"
    assert fact.effective_at == datetime(2026, 9, 30)
    assert fact.payload == {"short_volume": 40.0, "short_exempt_volume": 0.0, "total_volume": 100.0, "ratio": 0.4}


def test_ingest_stores_only_requested_symbols_and_is_idempotent_and_resumable(session):
    days = _weekdays(date(2026, 9, 30), 3)
    files = {d: {"AAPL": (40.0, 100.0), "NVDA": (50.0, 100.0), "OTHER": (1.0, 2.0)} for d in days}
    fake = FakeFinra(files)
    first = ingest_finra_short_volume(session, ["AAPL", "nvda"], days[0], days[-1], fake)
    assert first.facts_created == 6 and first.days_fetched == 3 and not first.errors
    assert {f.symbol for f in session.exec(select(KnownFact)).all()} == {"AAPL", "NVDA"}

    fake.asked.clear()
    again = ingest_finra_short_volume(session, ["AAPL", "NVDA"], days[0], days[-1], fake)
    assert again.facts_created == 0 and again.facts_existing == 6
    assert fake.asked == []  # nothing to fetch: resumed with no downloads
    assert len(session.exec(select(KnownFact)).all()) == 6

    # a symbol added later only fetches what it is missing
    ingest_finra_short_volume(session, ["AAPL", "NVDA", "OTHER"], days[0], days[-1], fake)
    assert len(session.exec(select(KnownFact)).all()) == 9


def test_ingest_counts_days_without_a_file_and_survives_a_failed_day(session):
    days = _weekdays(date(2026, 9, 30), 3)

    class Flaky(FakeFinra):
        def get_short_volume(self, symbols, day):
            if day == days[1]:
                raise DataProviderError("boom")
            return super().get_short_volume(symbols, day)

    fake = Flaky({days[0]: {"AAPL": (1.0, 2.0)}})  # days[2] has no file
    result = ingest_finra_short_volume(session, ["AAPL"], days[0], days[-1], fake)
    assert result.facts_created == 1 and result.days_without_file == 1
    assert len(result.errors) == 1 and "boom" in result.errors[0]


def test_ingest_skips_zero_volume_lines_rather_than_storing_a_fake_ratio(session):
    day = date(2026, 9, 30)
    ingest_finra_short_volume(session, ["AAPL"], day, day, FakeFinra({day: {"AAPL": (0.0, 0.0)}}))
    assert session.exec(select(KnownFact)).all() == []


def test_refresh_looks_back_over_recent_days(session):
    today = date(2026, 10, 1)
    fake = FakeFinra({date(2026, 9, 30): {"AAPL": (1.0, 2.0)}})
    result = refresh_finra_short_volume(session, ["AAPL"], fake, today=today)
    assert result.facts_created == 1
    assert min(fake.asked) <= date(2026, 9, 21)


# ---- the as-of reader ----------------------------------------------------------------


def _seed(session, symbol, days, ratio_for):
    files = {d: {symbol: (ratio_for(i) * 1000.0, 1000.0)} for i, d in enumerate(days)}
    ingest_finra_short_volume(session, [symbol], days[0], days[-1], FakeFinra(files))


def test_a_file_published_after_the_cutoff_is_invisible(session):
    days = _weekdays(date(2026, 9, 30), 30)
    _seed(session, "AAPL", days, lambda i: 0.4)
    cutoff = end_of_local_day_utc(days[-2])  # the last day's file is not public yet
    reading = short_volume_ratio_as_of(session, "AAPL", as_of=cutoff)
    assert reading.latest_trade_date == days[-2]
    with as_of(end_of_local_day_utc(days[-1]) - timedelta(hours=1)):
        assert short_volume_ratio_as_of(session, "AAPL").latest_trade_date == days[-2]
        assert all(
            f.effective_at.date() != days[-1] for f in facts_known_as_of(session, FactKind.FINRA_SHORT_VOLUME, symbol="AAPL")
        )
    assert short_volume_ratio_as_of(session, "AAPL", as_of=end_of_local_day_utc(days[0]) - timedelta(days=1)) is None


def test_ratio_and_baseline_maths(session):
    days = _weekdays(date(2026, 9, 30), 5 + 30)

    # 30 baseline days alternating 0.40/0.50 (median 0.45), then 5 recent days at 0.60
    def ratio_for(i):
        return (0.40 if i % 2 == 0 else 0.50) if i < 30 else 0.60

    _seed(session, "AAPL", days, ratio_for)
    reading = short_volume_ratio_as_of(session, "AAPL", as_of=end_of_local_day_utc(days[-1]))
    assert reading.recent_days == 5 and reading.recent_ratio == pytest.approx(0.60)
    assert reading.baseline_days == 30 and reading.baseline_ratio == pytest.approx(0.45)


def test_recent_ratio_is_volume_weighted(session):
    days = _weekdays(date(2026, 9, 30), 2)
    files = {days[0]: {"AAPL": (100.0, 1000.0)}, days[1]: {"AAPL": (900.0, 1000.0)}}
    ingest_finra_short_volume(session, ["AAPL"], days[0], days[1], FakeFinra(files))
    reading = short_volume_ratio_as_of(session, "AAPL", as_of=end_of_local_day_utc(days[1]))
    assert reading.recent_ratio == pytest.approx(0.5)
    assert reading.baseline_ratio is None  # too little history for a baseline


def test_baseline_needs_enough_days(session):
    days = _weekdays(date(2026, 9, 30), 5 + MIN_BASELINE_DAYS - 1)
    _seed(session, "AAPL", days, lambda i: 0.4)
    assert short_volume_ratio_as_of(session, "AAPL", as_of=end_of_local_day_utc(days[-1])).baseline_ratio is None


def test_reader_rejects_nonsense_windows(session):
    with pytest.raises(ValueError):
        short_volume_ratio_as_of(session, "AAPL", lookback_days=0)


def test_staleness():
    reading = ShortVolumeReading("AAPL", 0.5, 5, 0.4, 30, date(2026, 9, 1))
    assert not reading_is_fresh(reading, now=datetime(2026, 9, 30))
    assert reading_is_fresh(reading, now=datetime(2026, 9, 5))


# ---- the scorer ----------------------------------------------------------------------


def test_scorer_signs_by_direction_and_caps():
    high = 0.40 + HIGH_RATIO_DELTA + 0.01
    assert score_short_volume("long", high, 0.40)[0] == -1
    assert score_short_volume("short", high, 0.40)[0] == 1
    assert score_short_volume("long", 0.99, 0.10)[0] == -1  # capped at one point however extreme
    assert score_short_volume("long", 0.42, 0.40)[0] == 0
    assert score_short_volume("long", 0.20, 0.40)[0] == 0  # low volume is not scored here
    assert score_short_volume(None, high, 0.40)[0] == 0
    assert score_short_volume("long", high, None)[0] == 0
    assert score_short_volume("long", None, 0.4)[0] == 0


def test_scorer_reason_says_volume_is_not_interest():
    _, reason = score_short_volume("long", 0.6, 0.4)
    assert "not short interest" in reason


def test_no_data_is_unavailable_never_a_guess():
    signal = build_short_volume_signal("long", None)
    assert signal.available is False and signal.would_score == 0 and signal.value is None
    stale = build_short_volume_signal("long", ShortVolumeReading("AAPL", 0.6, 5, 0.4, 30, date(2026, 1, 1)), fresh=False)
    assert stale.available is False and stale.would_score == 0


# ---- the mechanism -------------------------------------------------------------------


# ---- plans: recorded, never decisive -------------------------------------------------


def _seed_high_short_volume(session, symbol="AAPL"):
    # History up to now so the reading is fresh: baseline 0.40, recent 0.60.
    days = _weekdays(datetime.now().date(), 5 + 30)
    files = {d: {symbol: ((0.40 if i < 30 else 0.60) * 1000.0, 1000.0)} for i, d in enumerate(days)}
    ingest_finra_short_volume(session, [symbol], days[0], days[-1], FakeFinra(files))


# ---- endpoints -----------------------------------------------------------------------


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def _session_override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    with Session(engine) as s:
        yield TestClient(app), s
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_finra_provider, None)


def test_refresh_and_read_endpoints(client):
    test_client, session = client
    days = _weekdays(datetime.now().date(), 3)
    fake = FakeFinra({d: {"AAPL": (60.0, 100.0)} for d in days})
    app.dependency_overrides[get_finra_provider] = lambda: fake

    resp = test_client.post("/api/signals/finra/refresh", json={"symbols": ["aapl"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols"] == 1 and body["facts_created"] >= 1 and body["errors"] == []

    # cooldown: a second press right away is refused
    assert test_client.post("/api/signals/finra/refresh", json={"symbols": ["AAPL"]}).status_code == 429

    read = test_client.get("/api/signals/finra/aapl", params={"direction": "long"})
    assert read.status_code == 200
    data = read.json()
    assert data["symbol"] == "AAPL" and data["stored_days"] >= 1
    assert data["recent_ratio"] == pytest.approx(0.6)
    assert data["baseline_ratio"] is None  # too little history
    assert data["signal"]["name"] == SIGNAL_NAME and data["signal"]["would_score"] == 0
    assert "not short interest" in data["note"]


def test_read_endpoint_is_read_only_and_an_unknown_symbol_is_unavailable(client):
    test_client, session = client
    resp = test_client.get("/api/signals/finra/NOPE")
    assert resp.status_code == 200
    assert resp.json()["stored_days"] == 0 and resp.json()["signal"]["available"] is False
    assert session.exec(select(KnownFact)).all() == []


def test_read_endpoint_rejects_a_bad_direction(client):
    test_client, _ = client
    assert test_client.get("/api/signals/finra/AAPL", params={"direction": "sideways"}).status_code == 422



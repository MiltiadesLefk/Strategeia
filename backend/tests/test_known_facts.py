"""The point-in-time fact layer (plan.md F-4): app/knowledge/.

The property everything later builds on (QM-1's archive, the SG-* signals, the
watchers, the backtester): a reader can never see a fact that wasn't public at
its cutoff unless it explicitly asks for `include_future=True`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.knowledge import (
    KNOWN_AT_CLOCK_SKEW_TOLERANCE,
    MAX_DEDUPE_KEY_LENGTH,
    SEC_EDGAR_TZ,
    FactKind,
    KnownFact,
    LookAheadError,
    as_of,
    current_as_of,
    end_of_local_day_utc,
    facts_known_as_of,
    is_simulated,
    latest_known,
    make_dedupe_key,
    payload_fingerprint,
    record_fact,
    simulated_as_of,
    source_time_to_utc,
    to_naive_utc,
)
from app.knowledge import store as store_module
from app.timeutil import utcnow_naive

T0 = datetime(2024, 3, 1, 21, 0)  # a simulated "decision moment", naive UTC


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _record(session, key, known_at, *, kind=FactKind.INSIDER_TRADE, symbol="AAPL", **kwargs):
    kwargs.setdefault("fetched_at", datetime(2026, 9, 28, 12, 0))
    return record_fact(session, kind=kind, symbol=symbol, source="test", dedupe_key=key, known_at=known_at, **kwargs)


# --- the look-ahead guard ---------------------------------------------------------------


def test_fact_known_after_the_cutoff_is_invisible(session):
    _record(session, "before", T0 - timedelta(hours=1))
    _record(session, "exactly", T0)
    _record(session, "after", T0 + timedelta(seconds=1))

    keys = [f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE, as_of=T0)]
    assert keys == ["exactly", "before"]  # inclusive cutoff, newest first


def test_simulated_context_is_the_default_cutoff(session):
    _record(session, "before", T0 - timedelta(days=1))
    _record(session, "after", T0 + timedelta(days=1))

    with as_of(T0):
        assert [f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE)] == ["before"]
        assert latest_known(session, FactKind.INSIDER_TRADE).dedupe_key == "before"
    # Live again: both are in the past relative to the real clock.
    assert len(facts_known_as_of(session, FactKind.INSIDER_TRADE)) == 2


def test_explicit_later_cutoff_inside_a_simulation_raises(session):
    """An explicit as_of must not be a way round the simulated moment."""
    _record(session, "after", T0 + timedelta(days=1))
    with as_of(T0):
        with pytest.raises(LookAheadError):
            facts_known_as_of(session, FactKind.INSIDER_TRADE, as_of=T0 + timedelta(days=2))
        with pytest.raises(LookAheadError):
            latest_known(session, FactKind.INSIDER_TRADE, as_of=T0 + timedelta(seconds=1))
        # An earlier explicit cutoff narrows the window and is fine.
        assert facts_known_as_of(session, FactKind.INSIDER_TRADE, as_of=T0 - timedelta(days=1)) == []


def test_include_future_is_the_only_bypass(session):
    _record(session, "after", T0 + timedelta(days=1))
    with as_of(T0):
        assert facts_known_as_of(session, FactKind.INSIDER_TRADE) == []
        assert [f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE, include_future=True)] == [
            "after"
        ]
        assert latest_known(session, FactKind.INSIDER_TRADE, include_future=True).dedupe_key == "after"


def test_live_future_cutoff_is_capped_at_now(session):
    """Nothing can be known later than now: a stray future cutoff in live code
    can't reveal a (mis-stamped) future row."""
    now = utcnow_naive()
    fact = _record(session, "future-stamped", now - timedelta(hours=1), fetched_at=now).fact
    fact.known_at = now + timedelta(days=5)  # bypass record_fact's clamp on purpose
    session.add(fact)
    session.commit()
    assert facts_known_as_of(session, FactKind.INSIDER_TRADE, as_of=now + timedelta(days=10)) == []


# --- dedupe and the known_at rules ------------------------------------------------------


def test_record_is_idempotent(session):
    first = _record(session, "acc-1", T0, payload={"shares": 100})
    second = _record(session, "acc-1", T0, payload={"shares": 100})

    assert first.created is True
    assert second.created is False
    assert second.fact.id == first.fact.id
    assert len(session.exec(select(KnownFact)).all()) == 1


def test_same_key_in_different_kinds_are_different_facts(session):
    _record(session, "k", T0, kind=FactKind.NEWS)
    _record(session, "k", T0, kind=FactKind.SEC_FILING_8K)
    assert len(session.exec(select(KnownFact)).all()) == 2


def test_known_at_never_moves_later(session):
    _record(session, "acc-1", T0)
    again = _record(session, "acc-1", T0 + timedelta(hours=3))
    assert again.known_at_moved_earlier is False
    assert again.fact.known_at == T0
    with as_of(T0):
        assert len(facts_known_as_of(session, FactKind.INSIDER_TRADE)) == 1


def test_known_at_moves_earlier_when_better_evidence_arrives(session):
    """First seen with no source time (fetch time), later backfilled with the
    SEC's acceptance time: the earlier, source-proven time wins."""
    fetched = datetime(2024, 3, 2, 14, 0)
    first = record_fact(session, kind=FactKind.INSIDER_TRADE, source="t", dedupe_key="acc-1", fetched_at=fetched)
    assert (first.fact.known_at, first.fact.known_at_basis) == (fetched, "fetched")

    accepted = datetime(2024, 3, 1, 22, 30)
    again = _record(session, "acc-1", accepted)
    assert again.known_at_moved_earlier is True
    assert (again.fact.known_at, again.fact.known_at_basis) == (accepted, "source")
    assert session.get(KnownFact, first.fact.id).known_at == accepted


def test_changed_payload_under_same_key_keeps_the_original(session, caplog):
    _record(session, "snap", T0, payload={"revenue": 1})
    with caplog.at_level(logging.WARNING, logger="app.knowledge.store"):
        again = _record(session, "snap", T0, payload={"revenue": 2})
    assert again.fact.payload == {"revenue": 1}
    assert "different payload" in caplog.text


def test_payload_fingerprint_makes_a_revision_a_new_fact(session):
    v1, v2 = {"revenue": 1, "eps": 2}, {"eps": 2, "revenue": 3}
    assert payload_fingerprint(v1) == payload_fingerprint({"eps": 2, "revenue": 1})  # key order irrelevant
    _record(session, make_dedupe_key("AAPL", payload_fingerprint(v1)), T0, payload=v1)
    _record(session, make_dedupe_key("AAPL", payload_fingerprint(v2)), T0 + timedelta(days=1), payload=v2)
    with as_of(T0):
        assert latest_known(session, FactKind.INSIDER_TRADE, symbol="AAPL").payload == v1
    assert latest_known(session, FactKind.INSIDER_TRADE, symbol="AAPL").payload == v2


def test_known_at_after_fetch_time_is_clamped(session, caplog):
    fetched = datetime(2026, 9, 28, 12, 0)
    small = _record(session, "small", fetched + timedelta(seconds=30), fetched_at=fetched)
    assert small.fact.known_at == fetched
    with caplog.at_level(logging.WARNING, logger="app.knowledge.store"):
        big = _record(session, "big", fetched + KNOWN_AT_CLOCK_SKEW_TOLERANCE + timedelta(hours=4), fetched_at=fetched)
    assert big.fact.known_at == fetched
    assert "clamped" in caplog.text


def test_no_source_time_means_fetch_time(session):
    before = utcnow_naive()
    fact = record_fact(session, kind=FactKind.NEWS, source="yfinance", dedupe_key="u").fact
    assert before <= fact.known_at == fact.fetched_at <= utcnow_naive()
    assert fact.known_at_basis == "fetched"


def test_fetched_at_is_the_real_clock_even_inside_a_simulation(session):
    with as_of(T0):
        fact = record_fact(session, kind=FactKind.NEWS, source="yfinance", dedupe_key="u").fact
    assert fact.fetched_at > T0 + timedelta(days=365)


def test_concurrent_insert_of_the_same_key_returns_the_winner(session, monkeypatch):
    """Another writer inserts between our existence check and our insert."""
    winner = _record(session, "acc-1", T0).fact
    real_find = store_module._find
    calls = {"n": 0}

    def find_misses_once(s, kind, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(s, kind, key)

    monkeypatch.setattr(store_module, "_find", find_misses_once)
    result = _record(session, "acc-1", T0)
    assert result.created is False
    assert result.fact.id == winner.id


# --- filtering --------------------------------------------------------------------------


def test_symbol_and_kind_filtering(session):
    _record(session, "a1", T0 - timedelta(days=3), symbol="aapl ")
    _record(session, "n1", T0 - timedelta(days=2), symbol="NVDA")
    _record(session, "news1", T0 - timedelta(days=1), kind=FactKind.NEWS, symbol="AAPL")
    _record(session, "fed1", T0 - timedelta(hours=1), kind=FactKind.FED_SPEECH, symbol=None)

    assert [f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE, T0, symbol="AAPL")] == ["a1"]
    assert [f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE, T0, symbol="Nvda")] == ["n1"]
    assert {f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE, T0)} == {"a1", "n1"}
    assert [f.dedupe_key for f in facts_known_as_of(session, [FactKind.NEWS, FactKind.FED_SPEECH], T0)] == [
        "fed1",
        "news1",
    ]
    assert facts_known_as_of(session, FactKind.FED_SPEECH, T0)[0].symbol is None
    assert session.exec(select(KnownFact).where(KnownFact.dedupe_key == "a1")).one().symbol == "AAPL"


def test_since_limit_and_ordering(session):
    for day in range(10):
        _record(session, f"d{day}", T0 - timedelta(days=day))

    recent = facts_known_as_of(session, FactKind.INSIDER_TRADE, T0, since=T0 - timedelta(days=3))
    assert [f.dedupe_key for f in recent] == ["d0", "d1", "d2", "d3"]  # since is inclusive
    assert [f.dedupe_key for f in facts_known_as_of(session, FactKind.INSIDER_TRADE, T0, limit=2)] == ["d0", "d1"]
    assert latest_known(session, FactKind.INSIDER_TRADE, as_of=T0 - timedelta(days=4, hours=1)).dedupe_key == "d5"
    assert latest_known(session, FactKind.INSIDER_TRADE, as_of=T0 - timedelta(days=30)) is None
    with pytest.raises(ValueError):
        facts_known_as_of(session, FactKind.INSIDER_TRADE, T0, limit=0)


def test_effective_at_is_not_a_visibility_cutoff(session):
    """SG-2: a Congress trade is visible from its REPORT date, never its trade date."""
    traded, reported = datetime(2024, 1, 10), datetime(2024, 2, 20, 17, 0)
    _record(session, "ptr-1", reported, kind=FactKind.CONGRESS_TRADE, effective_at=traded)
    assert facts_known_as_of(session, FactKind.CONGRESS_TRADE, as_of=datetime(2024, 2, 1)) == []
    fact = latest_known(session, FactKind.CONGRESS_TRADE, as_of=datetime(2024, 2, 21))
    assert (fact.effective_at, fact.known_at) == (traded, reported)


# --- the as_of context ------------------------------------------------------------------


def test_as_of_defaults_to_now_and_resets():
    assert simulated_as_of() is None and is_simulated() is False
    assert abs(current_as_of() - utcnow_naive()) < timedelta(seconds=5)
    with as_of(T0) as moment:
        assert moment == T0 and current_as_of() == T0 and is_simulated()
    assert simulated_as_of() is None


def test_as_of_nesting_narrows_and_restores():
    with as_of(T0):
        with as_of(T0 - timedelta(days=1)):
            assert current_as_of() == T0 - timedelta(days=1)
            with as_of(T0 - timedelta(days=1)):  # same moment is allowed
                pass
        assert current_as_of() == T0
    assert simulated_as_of() is None


def test_nested_as_of_cannot_move_into_the_future():
    with as_of(T0):
        with pytest.raises(LookAheadError):
            with as_of(T0 + timedelta(minutes=1)):
                pass
        assert current_as_of() == T0
    assert simulated_as_of() is None


def test_as_of_resets_after_an_exception():
    with pytest.raises(RuntimeError):
        with as_of(T0):
            raise RuntimeError("boom")
    assert simulated_as_of() is None


def test_as_of_accepts_tz_aware_moments():
    aware = datetime(2024, 3, 1, 16, 0, tzinfo=SEC_EDGAR_TZ)  # 4 pm EST = 21:00 UTC
    with as_of(aware) as moment:
        assert moment == T0 and moment.tzinfo is None


# --- time handling ----------------------------------------------------------------------


def test_to_naive_utc():
    assert to_naive_utc(T0) is T0  # naive is already UTC by convention
    aware = datetime(2024, 3, 1, 23, 0, tzinfo=timezone(timedelta(hours=2)))
    assert to_naive_utc(aware) == T0
    with pytest.raises(TypeError):
        to_naive_utc(date(2024, 3, 1))


def test_sec_index_page_eastern_time_converts_to_the_json_utc_value():
    """Checked live 2026-09-28 (AAPL Form 4 0001140361-26-037584): the index
    page's "Accepted 2026-09-24 18:30:07" (New York) and the submissions JSON's
    acceptanceDateTime "2026-09-24T22:30:07.000Z" are the same instant."""
    from_page = source_time_to_utc(datetime(2026, 9, 24, 18, 30, 7), SEC_EDGAR_TZ)
    from_json = to_naive_utc(datetime.fromisoformat("2026-09-24T22:30:07.000Z"))
    assert from_page == from_json == datetime(2026, 9, 24, 22, 30, 7)
    # Winter: EST is UTC-5.
    assert source_time_to_utc(datetime(2026, 1, 15, 18, 0), "America/New_York") == datetime(2026, 1, 15, 23, 0)


def test_dst_ambiguous_and_skipped_times_resolve_later():
    # 2026-11-01 01:30 happens twice in New York (EDT 05:30Z, then EST 06:30Z).
    assert source_time_to_utc(datetime(2026, 11, 1, 1, 30), SEC_EDGAR_TZ) == datetime(2026, 11, 1, 6, 30)
    # 2026-03-08 02:30 doesn't exist; the later reading is 07:30Z.
    assert source_time_to_utc(datetime(2026, 3, 8, 2, 30), SEC_EDGAR_TZ) == datetime(2026, 3, 8, 7, 30)


def test_end_of_local_day_for_date_only_sources():
    assert end_of_local_day_utc(date(2026, 9, 24)) == datetime(2026, 9, 25, 3, 59, 59, 999999)
    assert end_of_local_day_utc(date(2026, 1, 15)) == datetime(2026, 1, 16, 4, 59, 59, 999999)


def test_aware_known_at_is_stored_as_naive_utc(session):
    aware = datetime(2026, 9, 24, 18, 30, 7, tzinfo=SEC_EDGAR_TZ)
    fact = _record(session, "acc", aware, effective_at=datetime(2026, 9, 22, tzinfo=timezone.utc)).fact
    session.expire_all()
    stored = session.get(KnownFact, fact.id)
    assert stored.known_at == datetime(2026, 9, 24, 22, 30, 7) and stored.known_at.tzinfo is None
    assert stored.effective_at == datetime(2026, 9, 22) and stored.effective_at.tzinfo is None


# --- payload and validation -------------------------------------------------------------


def test_payload_round_trip(session):
    payload = {"code": "P", "shares": 1000, "price": 180.5, "owner": {"name": "X", "roles": ["CEO"]}, "a": None}
    fact = _record(session, "acc", T0, payload=payload).fact
    payload["shares"] = 1  # mutating the caller's dict afterwards must not change what was stored
    session.expire_all()
    assert session.get(KnownFact, fact.id).payload == {
        "code": "P",
        "shares": 1000,
        "price": 180.5,
        "owner": {"name": "X", "roles": ["CEO"]},
        "a": None,
    }


def test_non_json_payload_is_rejected_at_the_call(session):
    with pytest.raises(TypeError, match="isoformat"):
        _record(session, "acc", T0, payload={"when": datetime(2024, 1, 1)})
    assert session.exec(select(KnownFact)).all() == []


@pytest.mark.parametrize("kind", ["", "News", "news ", "8k", "a-b"])
def test_invalid_kind_rejected(session, kind):
    with pytest.raises(ValueError):
        _record(session, "k", T0, kind=kind)


def test_other_validation(session):
    with pytest.raises(ValueError):
        _record(session, "k", T0, known_at_basis="guess")
    with pytest.raises(ValueError):
        record_fact(session, kind="news", source=" ", dedupe_key="k")
    with pytest.raises(ValueError):
        record_fact(session, kind="news", source="s", dedupe_key="  ")
    assert _record(session, "k", end_of_local_day_utc(date(2024, 1, 1)), known_at_basis="derived").fact.known_at_basis == (
        "derived"
    )


def test_make_dedupe_key():
    assert make_dedupe_key("sec", "0001140361-26-037584", 1) == "sec|0001140361-26-037584|1"
    assert make_dedupe_key("a", None, "b") == "a||b"
    long_key = make_dedupe_key("https://example.com/" + "x" * 500)
    assert long_key.startswith("sha256:") and len(long_key) <= MAX_DEDUPE_KEY_LENGTH
    assert long_key == make_dedupe_key("https://example.com/" + "x" * 500)
    with pytest.raises(ValueError):
        make_dedupe_key()


def test_create_db_and_tables_registers_knownfact(monkeypatch):
    import app.database as database_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(database_module, "engine", engine)
    database_module.create_db_and_tables()
    with engine.connect() as conn:
        tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "knownfact" in tables
    assert "ix_knownfact_kind_symbol_known_at" in indexes

"""Reading insider trades as they were known at a moment: the look-ahead guard,
the window, amendments, clusters, and agreement with the live provider."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_providers.sec_edgar_provider import SecEdgarProvider
from app.data_providers.sec_form4 import ingest_form4_filing
from app.knowledge import LookAheadError, as_of
from app.knowledge.insider_trades import (
    insider_activity_as_of,
    insider_clusters_as_of,
    insider_trades_as_of,
)
from tests.test_sec_form4 import FIXTURES, filing_of, xml_of

# Accepted 2025-05-16: 16:22:34Z (Noseworthy, trade 14 May), 16:28:22Z (Flynn, trade 14 May),
# 22:11:47Z (Rex, trade 16 May).
AFTER_ALL_UNH = datetime(2025, 5, 20)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def load(session, symbol, *names):
    for name in names:
        ingest_form4_filing(session, symbol, filing_of(name), xml=xml_of(name))


def test_a_trade_is_invisible_until_its_filing_was_accepted_even_though_it_happened_earlier(session):
    load(session, "UNH", "unh_000146")  # Rex traded on 16 May; accepted 22:11:47Z that evening
    before = datetime(2025, 5, 16, 22, 11, 46)
    after = datetime(2025, 5, 16, 22, 11, 47)
    assert insider_trades_as_of(session, "UNH", before) == []
    visible = insider_trades_as_of(session, "UNH", after)
    assert len(visible) == 1 and visible[0].transaction_date.isoformat() == "2025-05-16"

    # Earlier transaction date, later filing: bought 14 May, filed 16 May.
    load(session, "UNH", "unh_000140")
    on_the_15th = insider_trades_as_of(session, "UNH", datetime(2025, 5, 15, 23, 59))
    assert on_the_15th == []  # the 14 May purchase was not public on the 15th
    on_the_16th = insider_trades_as_of(session, "UNH", datetime(2025, 5, 16, 17, 0))
    assert [t.owner_name for t in on_the_16th] == ["Noseworthy John H"]


def test_defaults_to_the_simulated_moment_inside_as_of(session):
    load(session, "UNH", "unh_000140", "unh_000142", "unh_000146")
    with as_of(datetime(2025, 5, 16, 16, 25)):
        assert [t.owner_name for t in insider_trades_as_of(session, "UNH")] == ["Noseworthy John H"]
        with pytest.raises(LookAheadError):
            insider_trades_as_of(session, "UNH", datetime(2025, 6, 1))


def test_trade_fields_carry_roles_value_and_the_filing_url(session):
    load(session, "UNH", "unh_000146")
    (trade,) = insider_trades_as_of(session, "UNH", AFTER_ALL_UNH)
    assert (trade.code, trade.shares, trade.price, trade.roles, trade.officer_title) == (
        "P",
        17175.0,
        291.1161,
        ("officer",),
        "President & CFO",
    )
    assert trade.value == pytest.approx(17175 * 291.1161)
    assert trade.known_at == datetime(2025, 5, 16, 22, 11, 47) and not trade.is_10b5_1
    assert trade.filing_url.endswith("wk-form4_1747433501.xml")


def test_window_is_measured_back_from_the_cutoff_on_known_at(session):
    load(session, "UNH", "unh_000140")  # known 2025-05-16 16:22:34
    known = datetime(2025, 5, 16, 16, 22, 34)
    from datetime import timedelta

    assert insider_trades_as_of(session, "UNH", known + timedelta(days=90), window_days=90)  # exactly on the edge
    assert not insider_trades_as_of(session, "UNH", known + timedelta(days=90, seconds=1), window_days=90)
    assert insider_trades_as_of(session, "UNH", known + timedelta(days=1), window_days=1)
    assert not insider_trades_as_of(session, "UNH", known + timedelta(days=2), window_days=1)


def test_codes_default_to_purchases_and_derivative_rows_are_opt_in(session):
    load(session, "AAPL", "aapl_013191")  # M, F, then three derivative M rows; no P or S at all
    when = datetime(2026, 4, 10)
    assert insider_trades_as_of(session, "AAPL", when) == []
    every_code = insider_trades_as_of(session, "AAPL", when, codes=None)
    assert sorted(t.code for t in every_code) == ["F", "M"]  # non-derivative only
    with_derivatives = insider_trades_as_of(session, "AAPL", when, codes=None, include_derivative=True)
    assert len(with_derivatives) == 5
    unpriced = [t for t in every_code if t.code == "M"][0]
    assert unpriced.price is None and unpriced.value is None


def test_the_10b5_1_flag_is_kept_and_can_be_filtered(session):
    load(session, "AAPL", "aapl_orig")
    when = datetime(2022, 9, 1)
    sells = insider_trades_as_of(session, "AAPL", when, codes=("S",))
    assert len(sells) == 2 and all(t.is_10b5_1 for t in sells)
    assert insider_trades_as_of(session, "AAPL", when, codes=("S",), include_10b5_1=False) == []


# ---- amendments ----


def test_an_amendment_replaces_the_original_only_from_its_own_acceptance_time(session):
    load(session, "AAPL", "aapl_orig", "aapl_4a")
    original_only = insider_trades_as_of(session, "AAPL", datetime(2022, 8, 21), codes=("S",))
    assert [t.accession for t in original_only] == ["0000320193-22-000076"] * 2
    assert "November 16, 2020" in original_only[0].payload["footnotes"][0]  # the uncorrected plan date

    amended = insider_trades_as_of(session, "AAPL", datetime(2022, 8, 23), codes=("S",))
    assert [t.accession for t in amended] == ["0000320193-22-000078"] * 2  # counted once, as amended
    assert "November 5, 2021" in amended[0].payload["footnotes"][0]
    assert amended[0].form == "4/A"


def test_amendment_does_not_double_count_in_the_activity_summary(session):
    load(session, "AAPL", "aapl_orig", "aapl_4a")
    activity = insider_activity_as_of(session, "AAPL", datetime(2022, 9, 1))
    assert activity.sell_count == 2
    assert activity.sell_value == pytest.approx(66390 * 174.66 + 30345 * 175.60)


def test_an_ambiguous_amendment_replaces_nothing(session):
    # Two original filings by the same insider for the same period on the same day, plus an
    # amendment: which one was corrected is unknowable, so both stay rather than dropping a real trade.
    from dataclasses import replace

    load(session, "AAPL", "aapl_orig")
    twin = replace(filing_of("aapl_orig"), accession="0000320193-22-000077")
    ingest_form4_filing(session, "AAPL", twin, xml=xml_of("aapl_orig"))
    load(session, "AAPL", "aapl_4a")
    sells = insider_trades_as_of(session, "AAPL", datetime(2022, 9, 1), codes=("S",))
    assert len(sells) == 6


# ---- activity summary ----


def test_activity_is_none_when_nothing_was_ever_loaded_or_known(session):
    assert insider_activity_as_of(session, "UNH", AFTER_ALL_UNH) is None
    load(session, "UNH", "unh_000146")
    assert insider_activity_as_of(session, "UNH", datetime(2025, 5, 16, 12, 0)) is None  # not yet filed
    quiet = insider_activity_as_of(session, "UNH", datetime(2026, 5, 1), window_days=90)
    assert quiet is not None and (quiet.buy_count, quiet.sell_count) == (0, 0)  # known, but outside the window


def test_activity_matches_the_live_providers_parse_of_the_same_filings(session):
    load(session, "UNH", "unh_000140", "unh_000142", "unh_000146")
    load(session, "AAPL", "aapl_orig", "aapl_013191")
    for symbol, names, when in [
        ("UNH", ["unh_000140", "unh_000142", "unh_000146"], datetime(2025, 5, 20)),
        ("AAPL", ["aapl_orig"], datetime(2022, 9, 1)),
        ("AAPL", ["aapl_013191"], datetime(2026, 4, 10)),
    ]:
        live = [0, 0, 0.0, 0.0]
        for name in names:
            parsed = SecEdgarProvider._parse_form4((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))
            for i, value in enumerate(parsed):
                live[i] += value
        mine = insider_activity_as_of(session, symbol, when)
        assert (mine.buy_count, mine.sell_count) == (live[0], live[1]), name
        assert mine.buy_value == pytest.approx(live[2]) and mine.sell_value == pytest.approx(live[3])
        assert mine.window_days == 90 and mine.symbol == symbol


# ---- clusters ----


def test_cluster_of_three_insiders_and_when_it_became_visible(session):
    load(session, "UNH", "unh_000140", "unh_000142", "unh_000146")
    clusters = insider_clusters_as_of(session, "UNH", AFTER_ALL_UNH)
    assert len(clusters) == 1
    c = clusters[0]
    assert (c.insider_count, c.trade_count) == (3, 3)
    assert (c.start_date.isoformat(), c.end_date.isoformat()) == ("2025-05-14", "2025-05-16")
    assert c.roles == ("director", "officer")
    assert c.total_shares == 300 + 1533 + 17175
    assert c.total_value == pytest.approx(300 * 312.1563 + 1533 * 320.80 + 17175 * 291.1161)
    assert c.unpriced_trades == 0 and not c.any_10b5_1
    assert c.insiders == ("FLYNN TIMOTHY PATRICK", "Noseworthy John H", "REX JOHN F")
    # The second distinct insider's filing (Flynn, 16:28:22Z) is what completed the cluster.
    assert c.visible_from == datetime(2025, 5, 16, 16, 28, 22)


def test_no_cluster_before_the_second_insider_filed(session):
    load(session, "UNH", "unh_000140", "unh_000142", "unh_000146")
    assert insider_clusters_as_of(session, "UNH", datetime(2025, 5, 16, 16, 25)) == []
    two = insider_clusters_as_of(session, "UNH", datetime(2025, 5, 16, 17, 0))
    assert len(two) == 1 and two[0].insider_count == 2 and two[0].trade_count == 2


def test_purchases_more_than_14_days_apart_do_not_cluster(session):
    from dataclasses import replace

    load(session, "UNH", "unh_000140")
    late = xml_of("unh_000146").replace(b"2025-05-16</value>", b"2025-06-20</value>")  # Rex buys 5 weeks later
    ingest_form4_filing(session, "UNH", replace(filing_of("unh_000146")), xml=late)
    assert insider_clusters_as_of(session, "UNH", AFTER_ALL_UNH) == []
    assert len(insider_trades_as_of(session, "UNH", AFTER_ALL_UNH)) == 2


def test_one_insider_buying_twice_is_not_a_cluster(session):
    from dataclasses import replace

    load(session, "UNH", "unh_000140")
    again = replace(filing_of("unh_000140"), accession="0000731766-25-000150")
    ingest_form4_filing(session, "UNH", again, xml=xml_of("unh_000140"))
    assert len(insider_trades_as_of(session, "UNH", AFTER_ALL_UNH)) == 2
    assert insider_clusters_as_of(session, "UNH", AFTER_ALL_UNH) == []

"""The confidence calibration report: statistics helpers, the table, the IC,
exclusions, and the read-only endpoint. Everything uses hand-computable trades
in an in-memory database; nothing touches the network or the real data."""

from __future__ import annotations

import math
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_session
from app.main import app
from app.portfolio.calibration import (
    CONFIDENCE_BANDS,
    MIN_TRADES_FOR_READING,
    VERDICT_NEGATIVE,
    VERDICT_NO_CLEAR_RELATIONSHIP,
    VERDICT_NO_VARIATION,
    VERDICT_NOT_ENOUGH_DATA,
    VERDICT_POSITIVE,
    band_label,
    compute_calibration,
    confidence_points,
)
from app.portfolio.models import EquitySnapshot, PaperPosition, TradePlanRecord
from app.portfolio.signal_stats import (
    average_ranks,
    bootstrap_mean_interval,
    rank_correlation,
    rank_correlation_p_value,
    spearman,
    spearman_interval,
    wilson_interval,
)

POINTS_MAX = 16


def pct(points: int) -> int:
    """The stored confidence percentage for a number of evidence points."""
    return round(points / POINTS_MAX * 100)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def add_trade(
    session: Session,
    points: int,
    r: float | None,
    *,
    direction: str = "long",
    pnl: float | None = None,
    status: str = "closed",
    with_plan: bool = True,
    **components: int | None,
) -> PaperPosition:
    plan_id = None
    if with_plan:
        plan = TradePlanRecord(
            symbol="TEST",
            direction=direction,
            confidence_score=pct(points),
            status="executed",
            **components,
        )
        session.add(plan)
        session.commit()
        session.refresh(plan)
        plan_id = plan.id
    position = PaperPosition(
        trade_plan_id=plan_id,
        symbol="TEST",
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0,
        tp1=110.0,
        tp2=120.0,
        shares=10,
        status=status,
        realized_r=r,
        realized_pnl=(r * 100.0 if r is not None else None) if pnl is None else pnl,
    )
    session.add(position)
    session.commit()
    return position


# --- statistics helpers -------------------------------------------------------------


def test_wilson_interval_matches_hand_calculation():
    low, high = wilson_interval(6, 10)
    assert low == pytest.approx(0.3127, abs=1e-3)
    assert high == pytest.approx(0.8318, abs=1e-3)
    low, high = wilson_interval(2, 4)
    assert low == pytest.approx(0.150, abs=2e-3)
    assert high == pytest.approx(0.850, abs=2e-3)


def test_wilson_interval_is_not_zero_width_at_the_extremes():
    low, high = wilson_interval(0, 5)
    assert low == 0.0 and 0.3 < high < 0.5  # 0 wins in 5 does not prove a 0% win rate
    low, high = wilson_interval(5, 5)
    assert high == 1.0 and 0.5 < low < 0.7
    assert wilson_interval(0, 0) is None


def test_average_ranks_share_the_average_of_tied_positions():
    assert list(average_ranks([10, 20, 20, 30])) == [1.0, 2.5, 2.5, 4.0]
    assert list(average_ranks([5, 5, 5])) == [2.0, 2.0, 2.0]


def test_spearman_matches_a_known_textbook_value():
    # The worked example from the standard Spearman reference: rho = -29/165.
    iq = [86, 97, 99, 100, 101, 103, 106, 110, 112, 113]
    tv = [2, 20, 28, 27, 50, 29, 7, 17, 6, 12]
    assert spearman(iq, tv) == pytest.approx(-29 / 165, abs=1e-12)


def test_spearman_handles_perfect_ties_and_undefined_cases():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # a constant side has no rank order
    assert spearman([1], [1]) is None
    assert spearman([], []) is None
    with pytest.raises(ValueError):
        spearman([1, 2], [1])


def test_exact_permutation_p_value_for_a_perfect_five_pair_sample():
    # Of the 120 orderings, exactly 2 (identical and reversed) are as extreme.
    assert rank_correlation_p_value([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == pytest.approx(2 / 120)


def test_permutation_p_value_is_tiny_for_perfect_and_large_for_random():
    xs = list(range(30))
    assert rank_correlation_p_value(xs, [x * 2.0 for x in xs]) < 0.001
    rng = random.Random(7)
    noise = [rng.random() for _ in xs]
    p = rank_correlation_p_value(xs, noise)
    assert p is not None and p > 0.05


def test_permutation_p_value_is_deterministic():
    xs = list(range(25))
    rng = random.Random(1)
    ys = [x + rng.gauss(0, 8) for x in xs]
    assert rank_correlation_p_value(xs, ys) == rank_correlation_p_value(xs, ys)


def test_spearman_interval_widens_as_the_sample_shrinks_and_needs_four_pairs():
    assert spearman_interval(0.5, 3) is None
    wide = spearman_interval(0.5, 10)
    narrow = spearman_interval(0.5, 200)
    assert wide[1] - wide[0] > narrow[1] - narrow[0]
    assert narrow[0] < 0.5 < narrow[1]
    perfect = spearman_interval(1.0, 20)  # must not blow up on atanh(1)
    assert perfect[1] <= 1.0 and perfect[0] > 0.9


def test_rank_correlation_reports_distinct_values():
    rc = rank_correlation([1, 1, 1, 1], [1, 2, 3, 4])
    assert rc.rho is None and rc.distinct_x == 1 and rc.distinct_y == 4


def test_bootstrap_interval_brackets_the_mean_and_is_deterministic():
    values = [2.0, -1.0, 1.5, -1.0, 0.5, -1.0, 2.5, -1.0]
    low, high = bootstrap_mean_interval(values)
    mean = sum(values) / len(values)
    assert low < mean < high
    assert bootstrap_mean_interval(values) == (low, high)
    assert bootstrap_mean_interval([1.0]) is None
    # No spread, no uncertainty to report.
    assert bootstrap_mean_interval([0.5, 0.5, 0.5]) == (0.5, 0.5)


# --- the calibration table ------------------------------------------------------------


def test_empty_database_says_there_is_nothing_to_calibrate(session):
    report = compute_calibration(session)
    assert report.closed_trades == 0 and report.analyzed_trades == 0
    assert report.reliable is False
    assert "No closed trades" in report.headline
    assert report.overall is None
    assert [b.n for b in report.bands] == [0] * len(CONFIDENCE_BANDS)
    assert all(b.win_rate is None and b.avg_r is None for b in report.bands)
    assert report.ic.verdict == VERDICT_NOT_ENOUGH_DATA and report.ic.ic is None
    assert all(c.verdict == VERDICT_NOT_ENOUGH_DATA for c in report.components)


def test_one_trade_is_reported_but_flagged_and_has_no_interval_on_r(session):
    add_trade(session, 9, 1.5)
    report = compute_calibration(session)
    band = next(b for b in report.bands if b.n == 1)
    assert band.label == "9-10 pts" and band.small_sample is True
    assert band.win_rate == 100.0 and band.avg_r == 1.5
    assert band.avg_r_low is None and band.avg_r_high is None  # one number has no spread
    assert band.win_rate_low < 100.0  # a single win does not prove a 100% win rate
    assert report.reliable is False
    assert "Only 1 closed trade" in report.headline
    assert report.ic.verdict == VERDICT_NOT_ENOUGH_DATA


def test_band_table_matches_hand_computed_numbers(session):
    # Band 9-10: R = +2, -1, +1.5, -1 -> 2 wins of 4, mean R 0.375, P&L 150.
    for r in (2.0, -1.0, 1.5, -1.0):
        add_trade(session, 9, r)
    add_trade(session, 10, 0.5)  # also band 9-10 -> n=5, wins 3, mean R (0.375*4+0.5)/5 = 0.4, P&L 200
    # Band 7-8: one loser.
    add_trade(session, 8, -1.0)
    # Band 11+: two winners.
    add_trade(session, 12, 2.0)
    add_trade(session, 16, 3.0)
    # Band <= 6: nothing.
    report = compute_calibration(session)
    by_label = {b.label: b for b in report.bands}
    assert [b.label for b in report.bands] == ["6 pts or fewer", "7-8 pts", "9-10 pts", "11+ pts"]

    mid = by_label["9-10 pts"]
    assert (mid.n, mid.wins) == (5, 3)
    assert mid.win_rate == pytest.approx(60.0)
    assert mid.avg_r == pytest.approx(0.4)
    assert mid.total_pnl == pytest.approx(200.0)
    wilson = wilson_interval(3, 5)
    assert mid.win_rate_low == pytest.approx(wilson[0] * 100) and mid.win_rate_high == pytest.approx(wilson[1] * 100)
    assert mid.avg_r_low < mid.avg_r < mid.avg_r_high
    assert mid.small_sample is True

    low = by_label["7-8 pts"]
    assert (low.n, low.wins, low.win_rate, low.avg_r, low.total_pnl) == (1, 0, 0.0, -1.0, -100.0)
    high = by_label["11+ pts"]
    assert (high.n, high.win_rate, high.avg_r, high.total_pnl) == (2, 100.0, 2.5, 500.0)
    empty = by_label["6 pts or fewer"]
    assert empty.n == 0 and empty.win_rate is None and empty.total_pnl == 0.0

    assert report.overall.n == 8 and report.overall.wins == 5
    assert sum(b.n for b in report.bands) == report.analyzed_trades == 8


def test_win_is_decided_by_pnl_like_the_dashboard_win_rate(session):
    # A trade can have a small positive R but a negative net P&L after fees; the
    # app's win rate counts P&L > 0, and so must the table.
    add_trade(session, 9, 0.01, pnl=-0.5)
    add_trade(session, 9, -0.2, pnl=3.0)
    report = compute_calibration(session)
    band = next(b for b in report.bands if b.n == 2)
    assert band.wins == 1


def test_band_edges_land_in_the_right_band_and_label_correctly(session):
    for points in (0, 6, 7, 8, 9, 10, 11, 16):
        add_trade(session, points, 1.0)
    report = compute_calibration(session)
    assert [b.n for b in report.bands] == [2, 2, 2, 2]
    assert band_label(0, 6, POINTS_MAX) == "6 pts or fewer"
    assert band_label(11, 16, POINTS_MAX) == "11+ pts"
    assert band_label(7, 8, POINTS_MAX) == "7-8 pts"
    assert band_label(9, 9, POINTS_MAX) == "9 pts"


def test_confidence_points_inverts_every_reachable_percentage():
    for points in range(POINTS_MAX + 1):
        plan = TradePlanRecord(symbol="X", confidence_score=pct(points))
        assert confidence_points(plan, POINTS_MAX) == points
    assert confidence_points(TradePlanRecord(symbol="X", confidence_score=250), POINTS_MAX) == POINTS_MAX


def test_gap_in_configured_bands_is_counted_not_dropped(session, monkeypatch):
    monkeypatch.setattr("app.portfolio.calibration.CONFIDENCE_BANDS", ((0, 6), (9, 16)))
    add_trade(session, 8, 1.0)  # falls in the 7-8 gap
    add_trade(session, 10, 1.0)
    report = compute_calibration(session)
    assert report.excluded.outside_bands == 1
    assert report.analyzed_trades == 1 and report.closed_trades == 2


# --- exclusions -------------------------------------------------------------------------


def test_trades_without_a_plan_or_r_are_excluded_and_counted(session):
    add_trade(session, 9, 1.0)
    add_trade(session, 9, 1.0, with_plan=False)  # manually opened position: no plan
    add_trade(session, 9, None)  # closed but no realised R recorded
    add_trade(session, 9, 1.0, status="open")  # still open: not part of the sample at all
    report = compute_calibration(session)
    assert report.closed_trades == 3
    assert report.analyzed_trades == 1
    assert report.excluded.no_linked_plan == 1
    assert report.excluded.missing_r == 1
    assert report.excluded.total == 2
    assert "2 of 3 closed trades were left out" in report.headline
    assert "no linked plan" in report.headline and "no recorded R" in report.headline


def test_position_pointing_at_a_missing_plan_counts_as_no_linked_plan(session):
    position = add_trade(session, 9, 1.0, with_plan=False)
    position.trade_plan_id = 9999
    session.add(position)
    session.commit()
    report = compute_calibration(session)
    assert report.excluded.no_linked_plan == 1 and report.analyzed_trades == 0


# --- the information coefficient ----------------------------------------------------------


def seed_correlated(session, n: int, *, reverse: bool = False) -> None:
    """n trades whose R rises (or falls) strictly with the confidence points."""
    for i in range(n):
        points = 5 + (i % 12)  # 5..16
        r = (points - 10) * 0.3 + i * 1e-4  # tiny tie-breaker so R is not tied
        add_trade(session, points, -r if reverse else r)


def test_perfectly_ordered_trades_give_a_positive_ic_with_a_finding(session):
    seed_correlated(session, 36)
    report = compute_calibration(session)
    assert report.reliable is True
    assert report.ic.n == 36
    assert report.ic.ic > 0.95
    assert report.ic.p_value < 0.01
    assert report.ic.ci_low > 0.8 and report.ic.ci_high <= 1.0
    assert report.ic.verdict == VERDICT_POSITIVE


def test_reversed_trades_give_a_negative_ic(session):
    seed_correlated(session, 36, reverse=True)
    report = compute_calibration(session)
    assert report.ic.ic < -0.95 and report.ic.verdict == VERDICT_NEGATIVE


def test_random_results_are_not_called_a_finding(session):
    rng = random.Random(3)
    for _ in range(40):
        add_trade(session, rng.randint(5, 16), rng.gauss(0, 1))
    report = compute_calibration(session)
    assert report.ic.n == 40
    assert report.ic.ci_low < 0 < report.ic.ci_high or report.ic.p_value > 0.05
    assert report.ic.verdict == VERDICT_NO_CLEAR_RELATIONSHIP


def test_a_strong_ic_on_a_small_sample_is_still_not_enough_data(session):
    seed_correlated(session, MIN_TRADES_FOR_READING - 1)
    report = compute_calibration(session)
    assert report.ic.ic > 0.9  # the number is shown...
    assert report.ic.verdict == VERDICT_NOT_ENOUGH_DATA  # ...but never as a finding
    assert report.reliable is False
    assert "noise" in report.headline


def test_constant_confidence_has_no_variation(session):
    for r in (1.0, -1.0, 2.0, -1.0) * 6:
        add_trade(session, 9, r)
    report = compute_calibration(session)
    assert report.ic.ic is None and report.ic.verdict == VERDICT_NO_VARIATION


def test_ic_is_split_by_direction(session):
    for i in range(24):
        points = 5 + (i % 12)
        direction = "long" if i % 2 == 0 else "short"
        # longs improve with points; shorts are unrelated (alternating results)
        r = (points - 10) * 0.3 + i * 1e-4 if direction == "long" else (1.0 if i % 4 == 1 else -1.0)
        add_trade(session, points, r, direction=direction)
    report = compute_calibration(session)
    long_ic, short_ic = report.ic_by_direction
    assert long_ic.key == "long" and long_ic.n == 12
    assert short_ic.key == "short" and short_ic.n == 12
    assert long_ic.ic > 0.9
    assert long_ic.verdict == VERDICT_NOT_ENOUGH_DATA  # 12 < the minimum, even though the number is large
    assert abs(short_ic.ic) < long_ic.ic


def test_components_report_their_own_n_and_skip_old_rows_without_a_value(session):
    # 30 trades; the news score drives R, the technical score is noise, the
    # options score is never recorded on the first 10 (an older plan).
    rng = random.Random(11)
    for i in range(30):
        news = rng.choice([-2, -1, 0, 1, 2])
        r = news * 0.8 + rng.gauss(0, 0.2)
        add_trade(
            session,
            5 + i % 12,
            r,
            news_score=news,
            technical_score=rng.randint(0, 6),
            options_score=None if i < 10 else 0,
        )
    report = compute_calibration(session)
    by_key = {c.key: c for c in report.components}
    news = by_key["news_score"]
    assert news.n == 30 and news.ic > 0.8
    assert news.verdict == VERDICT_POSITIVE
    assert news.p_value_adjusted is not None and news.p_value_adjusted >= news.p_value
    assert by_key["technical_score"].verdict == VERDICT_NO_CLEAR_RELATIONSHIP
    options = by_key["options_score"]
    assert options.n == 20 and options.n_nonzero == 0
    assert options.verdict == VERDICT_NO_VARIATION
    # Components never recorded at all: n=0, "not enough data", never a made-up zero.
    insider = by_key["insider_score"]
    assert insider.n == 0 and insider.ic is None and insider.verdict == VERDICT_NOT_ENOUGH_DATA
    assert report.components_tested == 2  # news and technical were the only testable ones


def test_every_component_field_exists_on_the_plan_model():
    from app.portfolio.calibration import SCORE_COMPONENTS

    for field_name, _label in SCORE_COMPONENTS:
        assert field_name in TradePlanRecord.model_fields


# --- the endpoint ----------------------------------------------------------------------------


@pytest.fixture()
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)


def test_endpoint_on_an_empty_database(client):
    response = client.get("/api/portfolio/calibration")
    assert response.status_code == 200
    body = response.json()
    assert body["closed_trades"] == 0 and body["reliable"] is False
    assert body["overall"] is None
    assert body["ic"]["verdict"] == VERDICT_NOT_ENOUGH_DATA and body["ic"]["ic"] is None
    assert len(body["bands"]) == len(CONFIDENCE_BANDS)
    assert body["excluded"] == {"no_linked_plan": 0, "missing_r": 0, "outside_bands": 0, "total": 0}


def test_endpoint_returns_the_report_and_writes_nothing(session, client):
    seed_correlated(session, 30)
    add_trade(session, 9, 1.0, with_plan=False)
    positions_before = [(p.id, p.status, p.realized_r) for p in session.exec(select(PaperPosition)).all()]
    response = client.get("/api/portfolio/calibration")
    assert response.status_code == 200
    body = response.json()
    assert body["closed_trades"] == 31 and body["analyzed_trades"] == 30
    assert body["excluded"]["no_linked_plan"] == 1 and body["excluded"]["total"] == 1
    assert body["ic"]["n"] == 30 and body["ic"]["verdict"] == VERDICT_POSITIVE
    assert sum(b["n"] for b in body["bands"]) == 30
    assert {c["key"] for c in body["components"]} >= {"technical_score", "news_score", "ai_overlay_score"}
    assert math.isfinite(body["ic"]["ci_low"])
    # Read-only: no equity snapshot, no position change.
    assert session.exec(select(EquitySnapshot)).all() == []
    assert [(p.id, p.status, p.realized_r) for p in session.exec(select(PaperPosition)).all()] == positions_before


def test_endpoint_requires_auth_when_the_api_is_not_open(monkeypatch):
    from app.api import deps

    class Closed:
        allow_unauthenticated_api = False
        api_shared_secret = "s3cret"
        auth_username = "admin"
        auth_password = "pw"
        session_secret = "x"
        session_lifetime_days = 7

    monkeypatch.setattr(deps, "get_infra_settings", lambda: Closed())
    response = TestClient(app).get("/api/portfolio/calibration")
    assert response.status_code == 401

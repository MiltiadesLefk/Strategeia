"""The scored parts added after the original ten (analysis/live_evidence.py)."""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.analysis.financial_health_scoring import score_financial_health
from app.analysis.live_evidence import EXTRA_MAX_POINTS, EXTRA_PARTS, evaluate_extra_evidence, extra_scores_from_json
from app.analysis.shadow_signals import ShadowSignal
from app.analysis.valuation_scoring import score_valuation
from app.config import AppSettings
from app.data_providers.base import CompanyOverview, FinancialYear
from app.services import trade_plan_service
from app.services.trade_plan_service import MAX_SCORE_FOR_CONFIDENCE
from tests.test_trade_plan_service import FakeConfiguredLLMProvider, FakeUptrendDataProvider


def years(*pairs):
    return [FinancialYear(2020 + i, revenue, net_income) for i, (revenue, net_income) in enumerate(pairs)]


def overview(pe, eps=5.0):
    return CompanyOverview("X", "X Corp", 1e11, pe, 1e10, eps, 100.0, 200.0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ------------------------------------------------------------------ financial health


def test_profitable_and_improving_supports_a_long_and_argues_against_a_short():
    healthy = years((100, 12), (120, 18))
    assert score_financial_health("long", healthy)[0] == 1
    assert score_financial_health("short", healthy)[0] == -1


def test_a_loss_supports_a_short():
    sliding = years((100, 5), (90, -8))
    assert score_financial_health("short", sliding)[0] == 1
    assert score_financial_health("long", sliding)[0] == -1


def test_mixed_or_unreadable_financials_score_zero():
    assert score_financial_health("long", years((100, 8), (120, 9)))[0] == 0  # profitable, thin margin, not weak
    assert score_financial_health("long", years((100, 12)))[0] == 0  # one year only
    assert score_financial_health(None, years((100, 12), (120, 18)))[0] == 0


# ------------------------------------------------------------------ valuation


def test_cheap_and_expensive_at_the_extremes_are_signed_by_direction():
    assert score_valuation("long", overview(12.0), [])[0] == 1
    assert score_valuation("short", overview(12.0), [])[0] == -1
    assert score_valuation("short", overview(90.0), years((100, 5), (105, 6)))[0] == 1
    assert score_valuation("long", overview(90.0), years((100, 5), (105, 6)))[0] == -1


def test_a_fast_grower_is_allowed_a_high_multiple():
    assert score_valuation("long", overview(90.0), years((100, 5), (160, 20)))[0] == 0


def test_ordinary_multiples_and_missing_data_score_zero():
    assert score_valuation("long", overview(30.0), [])[0] == 0
    assert score_valuation("long", None, [])[0] == 0
    assert score_valuation(None, overview(10.0), [])[0] == 0


# ------------------------------------------------------------------ the bundle


def test_the_maximum_grew_by_exactly_the_parts_that_were_added():
    assert len(EXTRA_PARTS) == 13 and EXTRA_MAX_POINTS == 15  # the two caution flags are penalty-only: not in the maximum
    assert MAX_SCORE_FOR_CONFIDENCE == 32  # the original 17 (insiders now cap 2) + 15: the default bar (16% = 5 points) depends on it
    assert AppSettings().min_confidence_for_trade == 16


def test_no_stored_data_scores_zero_everywhere(session):
    extra = evaluate_extra_evidence("long", session, "AAA", None, [])
    assert extra.total == 0 and extra.reasons == [] and set(extra.points) == {part[0] for part in EXTRA_PARTS}


def test_a_part_that_raises_scores_zero_instead_of_breaking_the_plan(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("app.analysis.live_evidence.build_congress_signal", boom)
    extra = evaluate_extra_evidence("long", session, "AAA", None, [])
    assert extra.points["congress"] == 0


def test_json_round_trip_tolerates_junk():
    assert extra_scores_from_json('{"congress": 1, "funds": -1}') == {"congress": 1, "funds": -1}
    assert extra_scores_from_json(None) is None
    assert extra_scores_from_json("not json") is None
    assert extra_scores_from_json("[1]") is None


# ------------------------------------------------------------------ in a plan


def _plan(session, monkeypatch, **overrides):
    settings = AppSettings(
        telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=False, auto_execute_trade_plans=True,
        min_confidence_for_trade=0, **overrides,
    )
    monkeypatch.setattr("app.services.trade_plan_service.load_app_settings", lambda: settings)
    return trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), FakeConfiguredLLMProvider(), session
    )


def test_smart_money_buying_adds_a_point_to_a_long_and_is_explained(session, monkeypatch):
    baseline = _plan(session, monkeypatch)
    monkeypatch.setattr(
        "app.analysis.live_evidence.build_congress_signal",
        lambda direction, sess, symbol: ShadowSignal("congress_buying", "+3", 1, "3 member(s) bought and 0 sold", True),
    )
    boosted = _plan(session, monkeypatch)
    assert boosted.extra_scores["congress"] == 1 and baseline.extra_scores["congress"] == 0
    assert boosted.confidence_points == baseline.confidence_points + 1
    assert "congress" in boosted.signal_reasons.lower()
    # It adds a point; it still cannot move a level or a size.
    for field in ("direction", "entry", "stop", "tp1", "tp2", "suggested_shares"):
        assert getattr(boosted, field) == getattr(baseline, field), field


def test_smart_money_selling_costs_a_long_a_point(session, monkeypatch):
    baseline = _plan(session, monkeypatch)
    monkeypatch.setattr(
        "app.analysis.live_evidence.build_congress_signal",
        lambda direction, sess, symbol: ShadowSignal("congress_buying", "-3", -1, "0 member(s) bought and 3 sold", True),
    )
    assert _plan(session, monkeypatch).confidence_points == baseline.confidence_points - 1


# ------------------------------------------------------------------ news read by AI labels


def _cards(*specs):
    return [
        {"materiality": m, "sentiment": s, "is_about_this_company": True} for m, s in specs
    ]


def _news(monkeypatch, cards, direction="long"):
    from app.analysis.news_card_scoring import ai_news_score

    monkeypatch.setattr("app.analysis.news_card_scoring.recent_cards", lambda session, symbol: cards)
    return ai_news_score(object(), "AAA", direction, cap=2)


def test_ai_labelled_news_scores_by_net_weight_signed_for_direction(monkeypatch):
    good = _cards(("high", "positive"))  # net +2: one point
    assert _news(monkeypatch, good)[:2] == (True, 1)
    assert _news(monkeypatch, good, "short")[:2] == (True, -1)
    very_good = _cards(("high", "positive"), ("high", "positive"))  # net +4: two points, capped at 2
    assert _news(monkeypatch, very_good)[:2] == (True, 2)


def test_light_or_immaterial_labelled_news_is_available_but_scores_zero(monkeypatch):
    assert _news(monkeypatch, _cards(("medium", "positive")))[:2] == (True, 0)  # net +1 is under the threshold
    assert _news(monkeypatch, _cards(("low", "negative")))[:2] == (True, 0)  # low materiality does not count


def test_no_labelled_news_is_unavailable_so_keywords_stay_in_charge(monkeypatch):
    assert _news(monkeypatch, [])[0] is False


# ------------------------------------------------------------------ the second batch: price, options, consensus, 5% owners, DCF


def _bars(closes, volumes=None):
    n = len(closes)
    return __import__("pandas").DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes or [1000.0] * n}
    )


def test_relative_strength_leader_supports_a_long_and_a_laggard_a_short():
    from app.analysis.price_evidence import score_relative_strength

    market = _bars([100.0 + 0.1 * i for i in range(100)])
    leader = _bars([100.0 + 0.4 * i for i in range(100)])
    assert score_relative_strength("long", leader, market)[0] == 1
    assert score_relative_strength("short", leader, market)[0] == -1
    laggard = _bars([100.0 - 0.2 * i for i in range(100)])
    assert score_relative_strength("short", laggard, market)[0] == 1
    assert score_relative_strength("long", laggard, market)[0] == -1
    assert score_relative_strength("long", market, market)[0] == 0  # keeping pace: nothing
    assert score_relative_strength("long", leader, None)[0] == 0  # no market bars


def test_volume_trend_accumulation_and_distribution():
    from app.analysis.price_evidence import score_volume_trend, up_down_volume_ratio

    closes = [100.0]
    volumes = [1000.0]
    for i in range(30):
        up = i % 2 == 0
        closes.append(closes[-1] + (1 if up else -1))
        volumes.append(3000.0 if up else 1000.0)  # heavy on up days
    accumulating = _bars(closes, volumes)
    assert up_down_volume_ratio(accumulating) > 2
    assert score_volume_trend("long", accumulating)[0] == 1 and score_volume_trend("short", accumulating)[0] == -1
    distributing = _bars(closes, [1000.0 if v == 3000.0 else 3000.0 for v in volumes])
    assert score_volume_trend("short", distributing)[0] == 1
    assert score_volume_trend("long", _bars([100.0] * 40))[0] == 0  # no up or down days: no ratio


def test_options_open_interest_and_analyst_consensus():
    from datetime import date

    from app.analysis.options_consensus_evidence import score_analyst_consensus, score_options_open_interest
    from app.data_providers.base import EarningsEstimate, EarningsHistoryEntry, OptionContract, OptionsChain

    def contract(oi):
        return OptionContract(strike=100.0, last_price=1.0, bid=1.0, ask=1.1, volume=10.0, open_interest=oi, implied_volatility=0.3, in_the_money=False)

    bullish = OptionsChain("X", "2099-01-15", [], 100.0, [contract(8000.0)], [contract(2000.0)])  # put/call OI 0.25
    bearish = OptionsChain("X", "2099-01-15", [], 100.0, [contract(2000.0)], [contract(8000.0)])
    assert score_options_open_interest("long", bullish)[0] == 1 and score_options_open_interest("short", bullish)[0] == -1
    assert score_options_open_interest("short", bearish)[0] == 1
    thin = OptionsChain("X", "2099-01-15", [], 100.0, [contract(100.0)], [contract(50.0)])
    assert score_options_open_interest("long", thin)[0] == 0 and score_options_open_interest("long", None)[0] == 0

    history = [EarningsHistoryEntry(date(2026, 7, 1), 1.9, 2.0, 5.0)]
    rising = EarningsEstimate(date(2026, 11, 1), "Q3", 2.4, None)  # +20% against the last 2.00
    falling = EarningsEstimate(date(2026, 11, 1), "Q3", 1.8, None)  # -10%
    assert score_analyst_consensus("long", rising, history)[0] == 1 and score_analyst_consensus("short", rising, history)[0] == -1
    assert score_analyst_consensus("short", falling, history)[0] == 1
    assert score_analyst_consensus("long", EarningsEstimate(date(2026, 11, 1), "Q3", 2.05, None), history)[0] == 0
    assert score_analyst_consensus("long", rising, [])[0] == 0  # no reported EPS to compare with


def test_five_percent_owners_count_both_ways():
    from datetime import datetime
    from types import SimpleNamespace

    from app.analysis.fund_signals import ownership_net_change

    now = datetime(2026, 10, 1)

    def rec(cik, when, schedule, amend, pct, name="Holder"):
        return SimpleNamespace(
            filer_cik=cik, filer_name=name, accession=f"{cik}{when}", known_at=when, schedule=schedule, is_amendment=amend, percent=pct
        )

    new_13d = [rec("1", datetime(2026, 9, 10), "13D", False, 9.0)]
    assert ownership_net_change(new_13d, now)[0] == 2  # an activist arrives: buying
    raised = [rec("1", datetime(2026, 5, 1), "13D", False, 9.0), rec("1", datetime(2026, 9, 10), "13D", True, 11.0)]
    assert ownership_net_change(raised, now)[0] == 1  # the old 13D is outside the window; the raise counts
    cut = [rec("1", datetime(2026, 5, 1), "13G", False, 9.0), rec("1", datetime(2026, 9, 10), "13G", True, 7.0)]
    assert ownership_net_change(cut, now)[0] == -1
    exited = [rec("1", datetime(2026, 5, 1), "13G", False, 9.0), rec("1", datetime(2026, 9, 10), "13G", True, 3.0)]
    assert ownership_net_change(exited, now)[0] == -2  # fell under the 5% line: sold out
    routine = [rec("1", datetime(2026, 9, 10), "13G", False, 8.0)]
    assert ownership_net_change(routine, now)[0] == 0  # a new passive 13G is routine


def test_dcf_and_peers_add_a_valuation_point():
    from types import SimpleNamespace

    from app.analysis.valuation_scoring import score_dcf_and_peers, score_valuation

    def valuation(upside, subject_pe, median_pe):
        dcf = SimpleNamespace(upside_pct=upside)
        pe = SimpleNamespace(name="P/E", subject=subject_pe, median=median_pe, count=5)
        return SimpleNamespace(available=True, dcf=dcf, comps=SimpleNamespace(multiples=[pe]))

    cheap = valuation(40.0, 10.0, 20.0)
    assert score_dcf_and_peers("long", cheap)[0] == 1 and score_dcf_and_peers("short", cheap)[0] == -1
    rich = valuation(-40.0, 40.0, 20.0)
    assert score_dcf_and_peers("short", rich)[0] == 1
    assert score_dcf_and_peers("long", valuation(5.0, 20.0, 20.0))[0] == 0  # fair on both
    assert score_dcf_and_peers("long", valuation(40.0, 40.0, 20.0))[0] == 0  # one cheap, one expensive: nets out
    assert score_dcf_and_peers("long", SimpleNamespace(available=False))[0] == 0
    # with a cheap P/E as well, the two valuation points add to the cap of two
    points, reasons = score_valuation("long", overview(10.0), [], cheap)
    assert points == 2 and len(reasons) == 2


def test_the_committee_is_given_the_market_context_and_the_rest(session):
    from app.committee.datapack import gather_data
    from tests.test_committee import FakeData

    pack = gather_data("ACME", FakeData(), session)
    market = pack.sections["market"]
    assert "Weekly chart" in market and "Broad market" in market and "return" in market  # weekly, SPY, relative strength
    assert "Open interest" not in (pack.sections["options"] or "")  # the fake has no chain: absent, not invented
    assert "5% holders" in pack.sections["insider"]
    assert "Upcoming market-wide releases" in (pack.sections["news"] or "") or pack.sections["news"] is not None

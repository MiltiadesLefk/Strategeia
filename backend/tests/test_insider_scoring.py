"""Insider (Form 4) scoring.

The load-bearing decision here is the asymmetry: buying scores, selling never
does. Executives sell constantly for diversification, taxes and pre-scheduled
10b5-1 plans — NVDA showed $653m of insider selling and zero buying over a
routine 90-day window. Scoring that as bearish would be scoring the payroll.
"""

from __future__ import annotations

from app.analysis.insider_scoring import (
    INSIDER_SCORE_CAP,
    MIN_NET_BUY_VALUE,
    SellSplit,
    score_insider_activity,
)
from app.data_providers.base import InsiderActivity


def _activity(**overrides) -> InsiderActivity:
    defaults = dict(
        symbol="ACME", window_days=90, buy_count=0, sell_count=0, buy_value=0.0, sell_value=0.0
    )
    defaults.update(overrides)
    return InsiderActivity(**defaults)


def test_meaningful_insider_buying_supports_a_long():
    activity = _activity(buy_count=3, buy_value=MIN_NET_BUY_VALUE * 4)
    score, reasons = score_insider_activity("long", activity)
    assert score == 1  # $400k net: the first tier
    assert reasons and "insider purchase" in reasons[0]
    assert score_insider_activity("long", _activity(buy_count=3, buy_value=3_000_000.0))[0] == INSIDER_SCORE_CAP  # $1M+: the second


def test_the_same_buying_contradicts_a_short():
    activity = _activity(buy_count=3, buy_value=MIN_NET_BUY_VALUE * 4)
    score, reasons = score_insider_activity("short", activity)
    assert score == -1
    assert "argues against a short" in reasons[0]


def test_heavy_selling_now_counts_but_at_the_cautious_weight_without_plan_flags():
    """Selling counts both ways. Without the archive's 10b5-1 flag every sale the provider counted is weighted as a plan
    sale (a quarter of its value), so $653M reads as $163M: the second tier, against a long and for a short."""
    activity = _activity(sell_count=17, sell_value=653_000_000.0)
    assert score_insider_activity("long", activity)[0] == -INSIDER_SCORE_CAP
    assert score_insider_activity("short", activity)[0] == INSIDER_SCORE_CAP
    modest = _activity(sell_count=3, sell_value=8_000_000.0)  # $2M weighted: under the first tier
    assert score_insider_activity("long", modest) == (0, [])


def test_buying_below_the_threshold_is_noise():
    activity = _activity(buy_count=1, buy_value=MIN_NET_BUY_VALUE / 10)
    assert score_insider_activity("long", activity) == (0, [])


def test_buying_swamped_by_selling_does_not_score():
    """net_value is what matters: a token purchase alongside a far larger sale
    is not a vote of confidence."""
    activity = _activity(buy_count=1, buy_value=50_000.0, sell_count=2, sell_value=5_000_000.0)
    assert score_insider_activity("long", activity) == (0, [])


def test_missing_activity_contributes_nothing():
    """Crypto, non-registrants and failed fetches are absences, not signals."""
    assert score_insider_activity("long", None) == (0, [])


def test_no_direction_contributes_nothing():
    activity = _activity(buy_count=5, buy_value=MIN_NET_BUY_VALUE * 10)
    assert score_insider_activity(None, activity) == (0, [])


def test_net_value_is_buys_minus_sells():
    activity = _activity(buy_value=1_000_000.0, sell_value=250_000.0)
    assert activity.net_value == 750_000.0


# ---- discretionary selling (outside 10b5-1 plans) counts, plan sales do not --------------------


def test_big_discretionary_selling_argues_against_a_long_and_supports_a_short():
    activity = _activity(sell_count=17, sell_value=653_000_000.0)
    split = SellSplit(3, 297_940_000.0, 14, 355_000_000.0)
    long_score, long_reasons = score_insider_activity("long", activity, split)
    short_score, short_reasons = score_insider_activity("short", activity, split)
    assert long_score == -2 and "by their own choice" in long_reasons[0] and "argues against a long" in long_reasons[0]
    assert short_score == 2 and "supports a short" in short_reasons[0]


def test_plan_sales_count_for_a_quarter_and_discretionary_sales_in_full():
    activity = _activity(sell_count=4, sell_value=40_000_000.0)
    assert score_insider_activity("long", activity, SellSplit(0, 0.0, 4, 40_000_000.0))[0] == -1  # 40M x 0.25 = 10M
    assert score_insider_activity("long", activity, SellSplit(4, 40_000_000.0, 0, 0.0))[0] == -1  # 40M: first tier
    assert score_insider_activity("long", activity, SellSplit(4, 60_000_000.0, 0, 0.0))[0] == -2  # 60M: second tier
    assert score_insider_activity("long", activity, SellSplit(1, 1_000_000.0, 0, 0.0)) == (0, [])  # too small


def test_selling_that_does_not_exceed_the_buying_scores_nothing_net():
    heavy_buyer = _activity(buy_count=3, buy_value=20_000_000.0, sell_count=2, sell_value=9_000_000.0)
    assert score_insider_activity("long", heavy_buyer, SellSplit(2, 9_000_000.0, 0, 0.0))[0] == 2  # net buying wins
    modest_buyer = _activity(buy_count=1, buy_value=2_000_000.0, sell_count=2, sell_value=9_000_000.0)
    assert score_insider_activity("long", modest_buyer, SellSplit(2, 9_000_000.0, 0, 0.0))[0] == -1  # sold more than bought: $7M net


def test_discretionary_selling_is_read_from_the_archive_without_plan_sales(tmp_path):
    from datetime import datetime

    from sqlmodel import Session, SQLModel, create_engine

    from app.analysis.insider_scoring import discretionary_sells, insider_sell_split
    from app.knowledge import FactKind, record_fact

    engine = create_engine(f"sqlite:///{tmp_path / 'k.db'}")
    SQLModel.metadata.create_all(engine)
    now = datetime.utcnow()
    with Session(engine) as session:
        assert discretionary_sells(session, "NVDA") is None  # nothing stored: unknown, not zero

        def sale(i, value, plan):
            record_fact(
                session, kind=FactKind.INSIDER_TRADE, source="test", symbol="NVDA", dedupe_key=f"s{i}", known_at=now,
                effective_at=now, payload={
                    "code": "S", "table": "non_derivative", "value": value, "shares": 1, "price": value,
                    "is_10b5_1": plan, "transaction_date": now.date().isoformat(), "accession": f"a{i}", "row_index": 0,
                    "form": "4", "acquired_disposed": "D",
                },
            )

        sale(1, 6_000_000.0, False)
        sale(2, 90_000_000.0, True)  # a scheduled plan sale: never counted
        session.commit()
        assert discretionary_sells(session, "NVDA") == (1, 6_000_000.0)
        split = insider_sell_split(session, "NVDA")
        assert (split.plan_count, split.plan_value) == (1, 90_000_000.0)  # plan sales are counted, and kept apart

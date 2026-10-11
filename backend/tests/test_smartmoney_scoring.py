from datetime import datetime, timedelta

from app.smartmoney import scoring as sc
from app.smartmoney.scoring import (
    Filing8K, FundChange, HouseTrade, InsiderBuy, OwnershipFiling, score_smart_money, should_reflag,
)

NOW = datetime(2026, 10, 11, 12, 0)
RECENT = NOW - timedelta(days=2)
OLD = NOW - timedelta(days=15)


def score(**kw):
    args = dict(insider_buys=[], house_trades=[], fund_changes=[], ownership_filings=[], filings_8k=[])
    args.update(kw)
    return score_smart_money(now=NOW, **args)


def buy(name="a", usd=50_000, filed=RECENT, ceo=False, officer=True, fam=None, plan=False):
    return InsiderBuy(name, fam or name, ceo, officer, usd, filed, plan)


def house(member="m", kind="buy", low=15_001, filed=RECENT, followed=False):
    return HouseTrade(member, kind, low, low * 2, filed, followed)


def test_empty_is_zero():
    r = score()
    assert r.total == 0 and r.components == []


def test_insider_base_ceo_and_size_tiers():
    assert score(insider_buys=[buy(usd=50_000)]).total == 1
    assert score(insider_buys=[buy(usd=50_000, ceo=True)]).total == 2
    assert score(insider_buys=[buy(usd=100_000)]).total == 2
    assert score(insider_buys=[buy(usd=500_000)]).total == 3
    assert score(insider_buys=[buy(usd=1_000_000, ceo=True)]).total == 1 + 1 + 3  # highest tier only


def test_insider_size_sums_a_buyers_purchases():
    r = score(insider_buys=[buy(usd=60_000), buy(usd=60_000)])
    assert r.total == 2  # one buyer, $120k -> +1 +1


def test_insider_cluster_bonus_at_three():
    two = score(insider_buys=[buy("a"), buy("b")])
    three = score(insider_buys=[buy("a"), buy("b"), buy("c")])
    assert two.total == 2 and three.total == 3 + 2


def test_joint_family_counts_as_one_buyer():
    r = score(insider_buys=[buy("fund1", fam="F"), buy("fund2", fam="F"), buy("fund3", fam="F")])
    assert r.total == 2 and len(r.components) == 1  # one buyer: base 1 + $150k tier 1, no cluster bonus
    r2 = score(insider_buys=[buy("x", usd=10_000, fam="F"), buy("y", usd=10_000, fam="F"), buy("z", usd=10_000, fam="F")])
    assert r2.total == 1 and len(r2.components) == 1


def test_non_officers_and_10b5_1_ignored():
    assert score(insider_buys=[buy(officer=False)]).total == 0
    assert score(insider_buys=[buy(plan=True)]).total == 0


def test_insider_cap_is_ten():
    buys = [buy(f"p{i}", usd=2_000_000, ceo=True) for i in range(6)]
    assert score(insider_buys=buys).total == 10


def test_recency_and_window():
    assert score(insider_buys=[buy(filed=OLD)]).total == 0.5
    assert score(insider_buys=[buy(filed=NOW - timedelta(days=7))]).total == 1
    assert score(insider_buys=[buy(filed=NOW - timedelta(days=31))]).total == 0
    assert score(insider_buys=[buy(filed=NOW + timedelta(hours=1))]).total == 0  # not known yet


def test_house_brackets():
    assert score(house_trades=[house(low=1_001)]).total == 0
    assert score(house_trades=[house(low=15_001)]).total == 1
    assert score(house_trades=[house(low=100_001)]).total == 2
    assert score(house_trades=[house(low=500_001)]).total == 3


def test_house_extra_member_and_ignored_member_not_counted():
    assert score(house_trades=[house("a"), house("b")]).total == 1 + 1 + 1
    assert score(house_trades=[house("a"), house("b", low=1_001)]).total == 1


def test_house_sells_net_off():
    r = score(house_trades=[house("a", low=100_001), house("b", kind="sell")])
    assert r.total == 1


def test_house_cap_is_six():
    trades = [house(f"m{i}", low=500_001) for i in range(5)]
    assert score(house_trades=trades).total == 6


def test_followed_member_multiplier():
    base = score(house_trades=[house(low=100_001, followed=True)])
    assert base.total == 3  # 2 x 1.5 default
    r = score_smart_money(
        now=NOW, insider_buys=[], house_trades=[house(low=100_001, followed=True)], fund_changes=[],
        ownership_filings=[], filings_8k=[], followed_member_multiplier=1.0,
    )
    assert r.total == 2
    unfollowed = score(house_trades=[house(low=100_001)])
    assert unfollowed.total == 2


def test_ownership_filings():
    assert score(ownership_filings=[OwnershipFiling("13D", False, RECENT)]).total == 3
    assert score(ownership_filings=[OwnershipFiling("13G", False, RECENT)]).total == 1
    assert score(ownership_filings=[OwnershipFiling("13D", True, RECENT), OwnershipFiling("13G", True, RECENT)]).total == 0


def test_funds_points_and_cap():
    assert score(fund_changes=[FundChange("A", "new", RECENT)]).total == 2
    assert score(fund_changes=[FundChange("A", "add", RECENT)]).total == 1
    many = [FundChange(f"F{i}", "new", RECENT) for i in range(4)]
    assert score(fund_changes=many).total == 3


def test_8k_negative_items():
    r = score(filings_8k=[Filing8K(["2.02", "5.02"], RECENT), Filing8K(["1.03"], OLD)])
    assert r.total == -1.5  # 5.02 recent -1, 1.03 old -0.5, 2.02 not negative
    assert score(filings_8k=[Filing8K(["2.02", "7.01"], RECENT)]).total == 0


def test_components_explain_total():
    r = score(insider_buys=[buy(ceo=True)], ownership_filings=[OwnershipFiling("13G", False, RECENT)])
    assert round(sum(c.points for c in r.components), 2) == r.total
    assert all(c.reason for c in r.components)


def test_insider_sells_are_not_scored_input():
    # The scorer has no sell input at all in this build (NEXT BUILD: insider sells).
    import inspect
    assert "insider_sells" not in inspect.signature(score_smart_money).parameters


def test_constants():
    assert sc.SMART_MONEY_MIN_SCORE == 3 and sc.REFLAG_DAYS == 30 and sc.REFLAG_SCORE_RISE == 2
    assert sc.LIQUIDITY_MIN_PRICE == 3.0 and sc.LIQUIDITY_MIN_DOLLAR_VOLUME == 1_000_000
    assert sc.REVIEW_EXPIRY_DAYS == 14


def test_reflag_guard():
    assert should_reflag(None, None, 4, NOW)
    assert not should_reflag(4, NOW - timedelta(days=5), 5, NOW)
    assert should_reflag(4, NOW - timedelta(days=5), 6, NOW)
    assert should_reflag(4, NOW - timedelta(days=30), 4, NOW)
    assert not should_reflag(4, NOW - timedelta(days=29), 5.9, NOW)

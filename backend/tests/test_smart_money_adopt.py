"""Smart-money finds join the universe (services/smart_money_adopt_service.py) and the overview filters.
Synthetic filings only (the seeded AAA insider cluster), the data and the alert are faked."""

from __future__ import annotations

import pytest

from app.analysis.quality_gate import GateResult
from app.config import AppSettings
from app.services import smart_money_adopt_service as adopt

from tests.test_smart_money import _simulated_now, client, engine, seeded, session  # noqa: F401 - fixtures


@pytest.fixture
def wired(monkeypatch):
    calls = []
    monkeypatch.setattr(adopt, "is_simulated", lambda: False)  # the fixture's simulated moment only serves the data reads
    monkeypatch.setattr(adopt, "in_saved_universe", lambda s: s in {"BBB"})
    monkeypatch.setattr("app.services.hold_buy_service.check_quality", lambda dp, s, **k: GateResult(False, []))
    monkeypatch.setattr(adopt, "adopt_found_stock", lambda session, settings, dp, symbol, lane, detail, gate: calls.append((symbol, lane, detail)) or True)
    return calls


def test_a_scored_buy_is_analysed_and_added_but_a_known_stock_is_skipped(session, seeded, wired):  # noqa: F811
    added = adopt.adopt_smart_money_finds(AppSettings(), object(), session)
    assert "AAA" in added and "BBB" not in added  # BBB is already in the universe
    assert wired[0][1] == "smart money" and "smart-money score" in wired[0][2]


def test_off_at_zero_and_a_high_bar_adds_nothing(session, seeded, wired):  # noqa: F811
    assert adopt.adopt_smart_money_finds(AppSettings(smart_money_adopt_max_per_run=0), object(), session) == []
    assert adopt.adopt_smart_money_finds(AppSettings(smart_money_adopt_min_score=30), object(), session) == []


def test_the_per_run_cap_holds(session, seeded, wired):  # noqa: F811
    assert len(adopt.adopt_smart_money_finds(AppSettings(smart_money_adopt_max_per_run=1), object(), session)) == 1


def test_overview_filters_by_side_symbol_and_value(client, seeded):  # noqa: F811
    both = client.get("/api/smart-money/overview?days=30").json()
    assert both["totals"]["insider"]["buys"] >= 2 and both["totals"]["insider"]["sells"] >= 1
    sells = client.get("/api/smart-money/overview?days=30&side=sells").json()
    assert sells["totals"]["insider"]["buys"] == 0 and all(r["total"] < 0 for r in sells["ranked"])
    one = client.get("/api/smart-money/overview?days=30&symbol=AAA").json()
    assert [r["symbol"] for r in one["ranked"]] == ["AAA"]
    big = client.get("/api/smart-money/overview?days=30&min_value=100000000").json()
    assert big["totals"]["insider"]["buys"] == 0


def test_rate_all_rates_a_find_and_adds_it_only_when_it_reaches_the_bar(session, seeded, wired, monkeypatch):  # noqa: F811
    from app.analysis.quality_gate import GateCheck

    passed = {"AAA": False}
    monkeypatch.setattr("app.services.rating_service.rate_stock",
                        lambda dp, s, bar, sess=None: GateResult(passed[s], [GateCheck("rating", passed[s], "x")]))
    assert adopt.adopt_smart_money_finds(AppSettings(), object(), session, run_id="r1", rate=True) == []  # under the bar
    passed["AAA"] = True
    assert "AAA" in adopt.adopt_smart_money_finds(AppSettings(), object(), session, run_id="r2", rate=True)

"""Valuation tab: the maths on fixed numbers, missing-data behaviour, the peer
table and the endpoint. No network: a fake provider serves hand-made figures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.analysis import valuation as val
from app.api.deps import get_data_provider, get_llm_provider
from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    FinancialsData,
    FinancialYear,
    QuoteData,
)
from app.llm_providers.null_provider import NullLLMProvider
from app.main import app
from app.schemas.valuation_schemas import DcfAssumptions
from app.services import valuation_service as svc


def assumptions(**kw):
    base = dict(growth_pct=10.0, net_margin_pct=20.0, discount_rate_pct=10.0, terminal_growth_pct=2.0, years=3)
    base.update(kw)
    return DcfAssumptions(**base)


def test_dcf_matches_hand_calculation():
    r = val.run_dcf(1000.0, assumptions(), shares=100.0, price=10.0)
    revenues = [1100.0, 1210.0, 1331.0]
    earnings = [e * 0.2 for e in revenues]
    pv = sum(e / 1.1 ** (i + 1) for i, e in enumerate(earnings))
    tv = earnings[-1] * 1.02 / 0.08
    expected = pv + tv / 1.1**3
    assert r.equity_value == pytest.approx(expected)
    assert r.value_per_share == pytest.approx(expected / 100)
    assert r.upside_pct == pytest.approx((expected / 100 / 10 - 1) * 100)
    assert len(r.projection) == 3
    assert len(r.sensitivity) == 9
    centre = [c for c in r.sensitivity if c.discount_rate_pct == 10.0 and c.terminal_growth_pct == 2.0][0]
    assert centre.value_per_share == pytest.approx(expected / 100)


def test_no_shares_means_no_per_share_value():
    r = val.run_dcf(1000.0, assumptions(), shares=None, price=10.0)
    assert r.value_per_share is None and r.upside_pct is None
    assert all(c.value_per_share is None for c in r.sensitivity)


def test_validate_rejects_rate_gap_and_ranges():
    assert val.validate_inputs(5, 10, 3.0, 2.5, 5) is not None
    assert val.validate_inputs(500, 10, 10, 2, 5) is not None
    assert val.validate_inputs(5, 10, 10, 2, 99) is not None
    assert val.validate_inputs(5, 10, 10, 2, 5) is None


def test_default_growth_clamped_and_missing():
    g, note = val.default_growth_pct([100.0, 400.0])
    assert g == val.DEFAULT_GROWTH_CAP_PCT and "limited" in note
    g, _ = val.default_growth_pct([100.0])
    assert g is None
    g, _ = val.default_growth_pct([-5.0, 100.0])
    assert g is None


def test_multiple_summary_skips_negative_and_missing():
    s = val.summarise_multiple("P/E", 20.0, [10.0, 30.0, -5.0, None, 20.0], 2.0)
    assert s.count == 3 and s.median == 20.0 and s.low == 10.0 and s.high == 30.0
    assert s.implied_price == 40.0
    empty = val.summarise_multiple("P/E", None, [None, -1.0], 2.0)
    assert empty.count == 0 and empty.median is None and empty.implied_price is None


class FakeProvider:
    name = "fake"

    def __init__(self, fail_financials=False, fail_overview=False):
        self.fail_financials = fail_financials
        self.fail_overview = fail_overview

    def get_company_overview(self, symbol):
        if self.fail_overview:
            raise AllProvidersFailedError("down")
        if symbol == "AAPL":
            return CompanyOverview(symbol, "Subject Inc", 1000.0, 25.0, 400.0, 4.0, None, None)
        # peers: P/E 10..., market cap = 2x sales
        n = sum(ord(c) for c in symbol) % 5 + 1
        return CompanyOverview(symbol, f"Peer {symbol}", 200.0, 10.0 * n, 100.0, 1.0, None, None)

    def get_financials(self, symbol):
        if self.fail_financials:
            raise AllProvidersFailedError("none")
        return FinancialsData(symbol, [FinancialYear(2023, 300.0, 30.0), FinancialYear(2024, 330.0, 33.0), FinancialYear(2025, 363.0, 36.3)])

    def get_quote(self, symbol):
        return QuoteData(symbol, 10.0, 0.0, 0.0, 0.0)


def test_service_builds_dcf_and_comps():
    r = svc.get_valuation(FakeProvider(), "AAPL")
    assert r.available and r.dcf is not None
    a = r.dcf.assumptions
    assert a.growth_pct == pytest.approx(10.0) and a.net_margin_pct == pytest.approx(10.0)
    assert r.dcf.shares == pytest.approx(100.0)  # market cap 1000 / price 10
    assert r.comps is not None and 0 < len(r.comps.peers) <= svc.MAX_PEERS
    assert all(p.symbol != "AAPL" for p in r.comps.peers)
    pe = [m for m in r.comps.multiples if m.name == "P/E"][0]
    assert pe.count == len(r.comps.peers) and pe.implied_price == pytest.approx(pe.median * 4.0)
    assert any("EV/EBITDA" in n for n in r.notes)
    assert r.summary is None


def test_user_overrides_win_and_bad_input_is_explained():
    r = svc.get_valuation(FakeProvider(), "AAPL", growth_pct=3.0, net_margin_pct=12.0)
    assert r.dcf.assumptions.growth_pct == 3.0 and r.dcf.assumptions.net_margin_pct == 12.0
    bad = svc.get_valuation(FakeProvider(), "AAPL", discount_rate_pct=2.0, terminal_growth_pct=2.0)
    assert bad.dcf is None and any("DCF not computed" in n for n in bad.notes)


def test_missing_financials_needs_manual_inputs_and_never_guesses():
    r = svc.get_valuation(FakeProvider(fail_financials=True), "AAPL")
    assert r.dcf is None and any("enter them yourself" in n for n in r.notes)
    manual = svc.get_valuation(FakeProvider(fail_financials=True), "AAPL", growth_pct=5.0, net_margin_pct=10.0)
    assert manual.dcf is not None


def test_crypto_and_provider_outage_are_unavailable():
    assert not svc.get_valuation(FakeProvider(), "BTC-USD").available
    out = svc.get_valuation(FakeProvider(fail_overview=True), "AAPL")
    assert not out.available and out.reason


def test_explain_uses_rule_text_without_an_llm():
    r = svc.get_valuation(FakeProvider(), "AAPL", llm=NullLLMProvider())
    assert r.summary and r.summary_source == "rules"
    assert "per share" in r.summary


@pytest.fixture
def client():
    app.dependency_overrides[get_data_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: NullLLMProvider()
    yield TestClient(app)
    app.dependency_overrides.pop(get_data_provider, None)
    app.dependency_overrides.pop(get_llm_provider, None)


def test_endpoint(client):
    res = client.get("/api/valuation/aapl?growth=4&margin=15&explain=true")
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "AAPL" and body["dcf"]["assumptions"]["growth_pct"] == 4.0
    assert body["summary_source"] == "rules"
    assert client.get("/api/valuation/AAPL?years=50").status_code == 422
    assert client.get("/api/valuation/bad%20sym!").status_code in (404, 422)

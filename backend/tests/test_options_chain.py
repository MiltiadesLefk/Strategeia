"""Options chain view: the maths on a fixture chain, the empty states, the provider
layer and the endpoint. No network: a fake provider serves a hand-built chain."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis.expected_move import compute_expected_move_pct
from app.api.deps import get_data_provider
from app.data_providers.base import (
    AllProvidersFailedError,
    DataProviderError,
    OptionContract,
    OptionsChain,
    QuoteData,
)
from app.data_providers.composite_provider import CompositeDataProvider
from app.data_providers.yfinance_provider import option_contracts_from_frame
from app.main import app
from app.services import options_chain_service as svc

TODAY = date(2026, 10, 1)
EXPIRATION = (TODAY + timedelta(days=30)).isoformat()
LATER = (TODAY + timedelta(days=60)).isoformat()


def leg(strike, oi=None, vol=None, iv=None, itm=None, last=1.0, bid=0.9, ask=1.1):
    return OptionContract(
        strike=strike,
        last_price=last,
        bid=bid,
        ask=ask,
        volume=vol,
        open_interest=oi,
        implied_volatility=iv,
        in_the_money=itm,
        contract_symbol=f"X{strike}",
    )


def fixture_chain(spot=100.0, expiration=EXPIRATION) -> OptionsChain:
    calls = [
        leg(90, oi=10, vol=0, iv=0.50, itm=True),
        leg(95, oi=100, vol=40, iv=0.34, itm=True),
        leg(100, oi=200, vol=100, iv=0.30),
        leg(105, oi=300, vol=60, iv=0.28),
        leg(110, oi=10, vol=0, iv=0.00001),  # a dead quote: the ~0 IV must read as missing
    ]
    puts = [
        leg(90, oi=10, vol=5, iv=0.45),
        leg(95, oi=300, vol=100, iv=0.40),
        leg(100, oi=200, vol=120, iv=0.34),
        leg(105, oi=100, vol=30, iv=0.31, itm=True),
        leg(110, oi=10, vol=0, iv=0.35, itm=True),
    ]
    return OptionsChain("AAA", expiration, [expiration, LATER], spot, calls, puts)


class FakeProvider:
    name = "fake"

    def __init__(self, chain=None, quote_price=None, raises=None):
        self.chain = chain
        self.quote_price = quote_price
        self.raises = raises
        self.calls: list[tuple[str, str | None]] = []

    def get_options_chain(self, symbol, expiration=None):
        self.calls.append((symbol, expiration))
        if self.raises:
            raise self.raises
        if self.chain is None:
            return None
        if expiration is not None and expiration != self.chain.expiration:
            return OptionsChain(
                self.chain.symbol, expiration, self.chain.expirations, self.chain.spot, self.chain.calls, self.chain.puts
            )
        return self.chain

    def get_quote(self, symbol):
        if self.quote_price is None:
            raise AllProvidersFailedError("no quote")
        return QuoteData(symbol, self.quote_price, 0.0, 1.0, 1.0)


# ---------------------------------------------------------------- the maths


def test_summary_ratios_and_totals():
    chain = fixture_chain()
    summary = svc.build_summary(chain, 100.0, 30)
    assert summary.call_volume == 200 and summary.put_volume == 255
    assert summary.put_call_volume_ratio == pytest.approx(255 / 200)
    assert summary.call_open_interest == 620 and summary.put_open_interest == 620
    assert summary.put_call_oi_ratio == pytest.approx(1.0)


def test_ratio_is_none_when_no_calls_traded():
    chain = fixture_chain()
    chain.calls = [leg(c.strike, oi=c.open_interest, vol=None, iv=c.implied_volatility) for c in chain.calls]
    summary = svc.build_summary(chain, 100.0, 30)
    assert summary.call_volume == 0
    assert summary.put_call_volume_ratio is None


def test_atm_strike_iv_and_expected_move():
    summary = svc.build_summary(fixture_chain(), 101.0, 30)
    assert summary.atm_strike == 100
    # mean of the 0.30 call and the 0.34 put at the strike
    assert summary.atm_implied_volatility == pytest.approx(0.32)
    expected = compute_expected_move_pct(0.32, 30)
    assert summary.expected_move_pct == pytest.approx(expected)
    assert summary.expected_move_dollars == pytest.approx(101.0 * expected / 100.0)


def test_atm_strike_tie_goes_to_the_lower_strike():
    assert svc.atm_strike([95, 100, 105], 102.5) == 100


def test_atm_iv_uses_whichever_side_has_a_usable_quote():
    calls = [leg(100, iv=0.00001)]
    puts = [leg(100, iv=0.40)]
    assert svc.atm_implied_volatility(calls, puts, 100) == pytest.approx(0.40)
    assert svc.atm_implied_volatility([leg(100, iv=None)], [leg(100, iv=0.0)], 100) is None


def test_max_pain_matches_a_hand_calculation():
    # Total payout at 95 / 100 / 105 is 2000 / 1000 / 2000 on the fixture's open interest.
    chain = fixture_chain()
    assert svc.max_pain_strike(chain.calls, chain.puts, 100.0) == 100


def test_max_pain_none_without_open_interest():
    calls = [leg(100, oi=None), leg(105, oi=0)]
    assert svc.max_pain_strike(calls, [leg(100, oi=None)]) is None


def test_max_pain_tie_prefers_strike_nearest_price():
    calls = [leg(90, oi=100)]
    puts = [leg(110, oi=100)]
    # Any strike between the two pays 100 * 20 = 2000 in total; nearest to 108 is 110.
    assert svc.max_pain_strike(calls, puts, 108.0) == 110


def test_iv_skew_puts_richer():
    summary = svc.build_summary(fixture_chain(), 100.0, 30)
    # Puts in the 3-10% band below 100 are the 90 and 95 strikes (0.45, 0.40: mean 0.425); calls in the
    # band above are the 105 (0.28) and the 110 whose ~0 IV is a dead quote and does not count.
    assert summary.iv_skew_points == pytest.approx(14.5)
    assert summary.iv_skew_label == "puts richer"


def test_iv_skew_calls_richer_balanced_and_missing():
    base = fixture_chain()
    calls_rich = OptionsChain("AAA", EXPIRATION, [], 100.0, [leg(105, iv=0.50)], [leg(95, iv=0.30)])
    assert svc.build_summary(calls_rich, 100.0, 30).iv_skew_label == "calls richer"
    balanced = OptionsChain("AAA", EXPIRATION, [], 100.0, [leg(105, iv=0.300)], [leg(95, iv=0.305)])
    assert svc.build_summary(balanced, 100.0, 30).iv_skew_label == "balanced"
    one_sided = OptionsChain("AAA", EXPIRATION, [], 100.0, base.calls, [])
    summary = svc.build_summary(one_sided, 100.0, 30)
    assert summary.iv_skew_points is None and summary.iv_skew_label is None


def test_no_spot_means_no_atm_move_or_skew():
    summary = svc.build_summary(fixture_chain(spot=None), None, 30)
    assert summary.atm_strike is None and summary.expected_move_pct is None and summary.iv_skew_points is None
    # Ratios and max pain do not need a price.
    assert summary.put_call_volume_ratio is not None and summary.max_pain_strike is not None


def test_expiring_today_has_no_expected_move():
    summary = svc.build_summary(fixture_chain(), 100.0, 0)
    assert summary.atm_implied_volatility is not None
    assert summary.expected_move_pct is None


# ---------------------------------------------------------------- the view


def test_view_rows_centre_on_the_money_and_flag_itm():
    view = svc.get_options_view(FakeProvider(fixture_chain()), "aaa", strikes_each_side=1, today=TODAY)
    assert view.available and view.symbol == "AAA"
    assert [r.strike for r in view.rows] == [95, 100, 105]
    assert [r.is_atm for r in view.rows] == [False, True, False]
    assert view.strikes_total == 5 and view.strikes_shown == 3
    assert view.days_to_expiration == 30
    # in_the_money is taken from the source when it gave one, and worked out from the price otherwise.
    row100 = view.rows[1]
    assert row100.call.in_the_money is False and row100.put.in_the_money is False
    assert view.rows[0].call.in_the_money is True
    # A dead ~0 IV is shown as missing, not as 0%.
    chain_view = svc.get_options_view(FakeProvider(fixture_chain()), "AAA", strikes_each_side=10, today=TODAY)
    assert chain_view.rows[-1].call.implied_volatility is None
    assert chain_view.summary.call_volume == 200  # the summary uses the whole chain, not the shown rows


def test_view_never_invents_greeks():
    view = svc.get_options_view(FakeProvider(fixture_chain()), "AAA", today=TODAY)
    assert svc.NOTE_NO_GREEKS in view.notes
    assert not any("delta" in name or "gamma" in name for name in type(view.rows[0].call).model_fields)


def test_view_falls_back_to_a_quote_for_the_price():
    chain = fixture_chain(spot=None)
    view = svc.get_options_view(FakeProvider(chain, quote_price=100.0), "AAA", today=TODAY)
    assert view.spot == 100.0 and view.summary.atm_strike == 100
    no_price = svc.get_options_view(FakeProvider(chain), "AAA", today=TODAY)
    assert no_price.spot is None and svc.NOTE_NO_SPOT in no_price.notes


def test_view_selects_a_listed_expiration():
    provider = FakeProvider(fixture_chain())
    view = svc.get_options_view(provider, "AAA", expiration=LATER, today=TODAY)
    assert view.expiration == LATER and view.days_to_expiration == 60
    assert provider.calls == [("AAA", None), ("AAA", LATER)]
    assert view.expirations == [EXPIRATION, LATER]


def test_view_rejects_an_unlisted_expiration():
    with pytest.raises(svc.UnknownExpirationError):
        svc.get_options_view(FakeProvider(fixture_chain()), "AAA", expiration="2030-01-18", today=TODAY)


def test_crypto_has_a_clean_no_options_state_and_never_calls_the_provider():
    provider = FakeProvider(fixture_chain())
    view = svc.get_options_view(provider, "BTC-USD")
    assert not view.available and view.reason == svc.REASON_CRYPTO and view.rows == []
    assert provider.calls == []


@pytest.mark.parametrize(
    "provider",
    [FakeProvider(None), FakeProvider(raises=DataProviderError("down")), FakeProvider(raises=NotImplementedError())],
)
def test_no_chain_is_an_empty_state_not_an_error(provider):
    view = svc.get_options_view(provider, "AAA")
    assert not view.available and view.reason == svc.REASON_NO_OPTIONS


def test_zero_volume_adds_a_note():
    chain = fixture_chain()
    chain.calls = [leg(c.strike, oi=c.open_interest, vol=None, iv=c.implied_volatility) for c in chain.calls]
    view = svc.get_options_view(FakeProvider(chain), "AAA", today=TODAY)
    assert svc.NOTE_ZERO_VOLUME in view.notes


# ---------------------------------------------------------------- provider layer


def test_yfinance_frame_conversion_keeps_blanks_blank():
    frame = pd.DataFrame(
        {
            "contractSymbol": ["A", "B", "C"],
            "strike": [105.0, 100.0, float("nan")],
            "lastPrice": [1.5, float("nan"), 2.0],
            "bid": [1.4, 0.0, 1.0],
            "ask": [1.6, 0.1, 1.2],
            "volume": [float("nan"), 12, 3],
            "openInterest": [10, 20, 30],
            "impliedVolatility": [0.3, 0.2, 0.1],
            "inTheMoney": [False, True, True],
        }
    )
    contracts = option_contracts_from_frame(frame)
    assert [c.strike for c in contracts] == [100.0, 105.0]  # sorted, the NaN strike dropped
    assert contracts[0].last_price is None and contracts[0].volume == 12
    assert contracts[1].volume is None and contracts[1].in_the_money is False
    assert option_contracts_from_frame(pd.DataFrame()) == []
    assert option_contracts_from_frame(None) == []


class _Raises:
    name = "raises"

    def get_options_chain(self, symbol, expiration=None):
        raise NotImplementedError


class _Fails:
    name = "fails"

    def get_options_chain(self, symbol, expiration=None):
        raise DataProviderError("boom")


def test_composite_passes_through_and_returns_none_on_total_failure():
    chain = fixture_chain()
    assert CompositeDataProvider([_Raises(), FakeProvider(chain)]).get_options_chain("AAA", None) is chain
    assert CompositeDataProvider([_Raises(), _Fails()]).get_options_chain("AAA") is None


def test_every_real_provider_declares_the_method():
    from app.data_providers.finnhub_provider import FinnhubProvider
    from app.data_providers.nasdaq_provider import NasdaqProvider
    from app.data_providers.sec_edgar_provider import SecEdgarProvider
    from app.data_providers.stooq_provider import StooqProvider
    from app.data_providers.yfinance_provider import YFinanceProvider

    for cls in (FinnhubProvider, NasdaqProvider, SecEdgarProvider, StooqProvider, YFinanceProvider):
        assert callable(getattr(cls, "get_options_chain")), cls
    # The providers that cannot supply a chain say so, so the composite moves on.
    with pytest.raises(NotImplementedError):
        StooqProvider().get_options_chain("AAA")


def test_the_chain_survives_the_persistent_cache_codec():
    from app.data_providers import cache_codec

    chain = fixture_chain()
    assert cache_codec.decode_value(cache_codec.encode_value(chain)) == chain


# ---------------------------------------------------------------- endpoint


@pytest.fixture
def client():
    provider = FakeProvider(fixture_chain())
    app.dependency_overrides[get_data_provider] = lambda: provider
    yield TestClient(app), provider
    app.dependency_overrides.pop(get_data_provider, None)


def test_endpoint_returns_the_chain_and_is_read_only(client):
    http, provider = client
    resp = http.get("/api/options/aaa?strikes=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] and body["symbol"] == "AAA" and len(body["rows"]) == 5
    assert body["summary"]["max_pain_strike"] == 100
    assert body["expirations"] == [EXPIRATION, LATER]
    # Only reads: nothing but chain lookups reached the provider.
    assert all(name == "AAA" for name, _ in provider.calls)


def test_endpoint_validates_input(client):
    http, _ = client
    assert http.get("/api/options/aaa?expiration=tomorrow").status_code == 422
    assert http.get("/api/options/aaa?expiration=2030-01-18").status_code == 422
    assert http.get("/api/options/aaa?strikes=0").status_code == 422
    assert http.get("/api/options/aaa?strikes=500").status_code == 422
    assert http.get("/api/options/bad$symbol").status_code == 422


def test_endpoint_crypto_is_a_clean_empty_view(client):
    http, _ = client
    resp = http.get("/api/options/BTC-USD")
    assert resp.status_code == 200
    assert resp.json()["available"] is False and "no listed options" in resp.json()["reason"]


def test_endpoint_has_no_write_methods(client):
    http, _ = client
    assert http.post("/api/options/AAA").status_code == 405
    assert http.delete("/api/options/AAA").status_code == 405

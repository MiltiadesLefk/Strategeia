"""SEC EDGAR Form 4 parsing — no network.

The two things that will break this provider in the wild: EDGAR's
primaryDocument points at an XSL-rendered HTML view that contains none of the
data tags, and Form 4 transaction codes mix real open-market trades with
compensation machinery.
"""

from __future__ import annotations

import pytest

from app.data_providers.cache import clear_cache
from app.data_providers.sec_edgar_provider import _XSL_PREFIX_RE, SecEdgarProvider


@pytest.fixture(autouse=True)
def _isolate_cache():
    clear_cache()
    yield
    clear_cache()


def _form4(entries: list[tuple[str, str, str]]) -> str:
    """entries are (code, shares, price)."""
    body = "".join(
        f"""
        <transactionAmounts>
          <transactionShares><value>{shares}</value></transactionShares>
          <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
        <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
        """
        for code, shares, price in entries
    )
    return f"<ownershipDocument>{body}</ownershipDocument>"


def test_xsl_prefix_is_stripped_to_reach_the_raw_xml():
    """primaryDocument is 'xslF345X06/form4.xml' — the rendered HTML view,
    which has no transactionCode tags at all. The raw XML is the same path
    without that directory."""
    assert _XSL_PREFIX_RE.sub("", "xslF345X06/form4.xml") == "form4.xml"
    assert _XSL_PREFIX_RE.sub("", "form4.xml") == "form4.xml"


def test_open_market_purchase_is_counted_as_a_buy():
    buys, sells, buy_value, sell_value = SecEdgarProvider._parse_form4(_form4([("P", "1000", "50.00")]))
    assert (buys, sells) == (1, 0)
    assert buy_value == pytest.approx(50_000.0)
    assert sell_value == 0.0


def test_open_market_sale_is_counted_as_a_sell():
    buys, sells, buy_value, sell_value = SecEdgarProvider._parse_form4(_form4([("S", "1438", "317.23")]))
    assert (buys, sells) == (0, 1)
    assert sell_value == pytest.approx(1438 * 317.23)


@pytest.mark.parametrize("code", ["M", "A", "F", "G", "C"])
def test_compensation_codes_are_ignored(code):
    """M (option exercise), A (grant), F (tax withholding) and friends are
    payroll mechanics, not a decision to take a position. Counting them as
    buys would show constant 'insider buying' at every company paying in
    equity."""
    assert SecEdgarProvider._parse_form4(_form4([(code, "5000", "10.00")])) == (0, 0, 0.0, 0.0)


def test_mixed_filing_separates_buys_from_sells():
    xml = _form4([("P", "100", "10.00"), ("S", "200", "20.00"), ("M", "999", "1.00")])
    buys, sells, buy_value, sell_value = SecEdgarProvider._parse_form4(xml)
    assert (buys, sells) == (1, 1)
    assert buy_value == pytest.approx(1_000.0)
    assert sell_value == pytest.approx(4_000.0)


def test_missing_price_counts_the_trade_but_not_its_value():
    """Some filings report share counts with no price. Scoring that as a $0
    trade would be wrong in the other direction."""
    buys, sells, buy_value, _ = SecEdgarProvider._parse_form4(_form4([("P", "100", "")]))
    assert buys == 1
    assert buy_value == 0.0


def test_empty_document_yields_nothing():
    assert SecEdgarProvider._parse_form4("<ownershipDocument></ownershipDocument>") == (0, 0, 0.0, 0.0)


def test_non_registrant_returns_none_without_hitting_the_network(monkeypatch):
    """A crypto pair has no CIK — that must be an absence, not an error, and
    must not cost a filing fetch."""
    provider = SecEdgarProvider()
    monkeypatch.setattr(SecEdgarProvider, "_ticker_to_cik", lambda self: {"AAPL": 320193})

    def explode(self, url):
        raise AssertionError(f"should not have fetched {url}")

    monkeypatch.setattr(SecEdgarProvider, "_fetch", explode)
    assert provider.get_insider_activity("BTC-USD") is None


def test_market_data_methods_fall_through_to_other_providers():
    """EDGAR is not a quote source. NotImplementedError is the composite's
    'try the next one' signal."""
    provider = SecEdgarProvider()
    for call in (
        lambda: provider.get_quote("AAPL"),
        lambda: provider.get_ohlcv("AAPL"),
        lambda: provider.get_news("AAPL"),
        lambda: provider.get_company_overview("AAPL"),
    ):
        with pytest.raises(NotImplementedError):
            call()

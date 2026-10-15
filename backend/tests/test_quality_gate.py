from app.analysis.quality_gate import MA_DAYS, evaluate_quality_gate
from app.data_providers.base import CompanyOverview, FinancialYear


def ov(pe=20.0, eps=5.0):
    return CompanyOverview("X", "X", 1e9, pe, 1e8, eps, 50.0, 120.0)


GOOD_YEARS = [FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 114e6, 14e6)]
RISING = [float(i) for i in range(1, 251)]


def verdict(**kw):
    args = dict(price=260.0, closes=RISING, overview=ov(), financial_years=GOOD_YEARS) | kw
    return evaluate_quality_gate(**args)


def test_good_stock_passes_with_a_reason_per_check():
    r = verdict()
    assert r.passed and [c.name for c in r.checks] == ["trend", "growth", "profit", "valuation"]
    assert all(c.note for c in r.checks)


def test_each_failure_is_named():
    assert verdict(price=100.0).failed_reasons[0].startswith("trend")
    assert verdict(financial_years=[FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 90e6, 14e6)]).failed_reasons[0].startswith("growth")
    assert verdict(financial_years=[FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 114e6, -1e6)]).failed_reasons[0].startswith("profit")
    assert verdict(overview=ov(pe=90.0)).failed_reasons[0].startswith("valuation")


def test_missing_data_fails_never_guesses():
    r = verdict(closes=RISING[: MA_DAYS - 1], overview=None, financial_years=[])
    assert not r.passed and len(r.failed_reasons) == 4


def test_dip_needs_ten_percent_under_the_three_month_high():
    from app.analysis.quality_gate import DIP_LOOKBACK, is_dip

    flat = [100.0] * DIP_LOOKBACK
    assert not is_dip(flat)
    assert not is_dip(flat[:-1] + [95.0])
    assert is_dip(flat[:-1] + [90.0])
    assert not is_dip([50.0] * (DIP_LOOKBACK - 1))  # not enough history
    assert verdict().dip is False


def test_price_only_runs_the_trend_check_alone_and_never_passes_on_missing_data():
    r = verdict(price_only=True, overview=None, financial_years=[])  # what a backtest has: no fundamentals at all
    assert r.passed and [c.name for c in r.checks] == ["trend"]
    assert not verdict(price_only=True, price=100.0, overview=None, financial_years=[]).passed
    assert not verdict(price_only=True, closes=RISING[:50], overview=None, financial_years=[]).passed  # too few bars
    assert not verdict(price_only=True, require_trend=False, price=100.0).passed  # the trend can't be switched off

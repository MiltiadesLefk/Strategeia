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


def test_growth_bar_is_a_setting_and_acceleration_is_optional():
    slow = [FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 103e6, 14e6)]  # +3%
    assert verdict(financial_years=slow).failed_reasons[0].startswith("growth")  # default bar 5%
    assert verdict(financial_years=slow, min_growth_pct=0.0).passed
    three = [FinancialYear(2023, 80e6, 8e6), FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 114e6, 14e6)]  # 25% then 14%
    assert [c.name for c in verdict(financial_years=three).checks] == ["trend", "growth", "profit", "valuation"]
    r = verdict(financial_years=three, require_acceleration=True)
    assert r.failed_reasons[0].startswith("acceleration")
    assert verdict(financial_years=[three[0], FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 130e6, 14e6)], require_acceleration=True).passed
    assert verdict(require_acceleration=True).failed_reasons[0].startswith("acceleration")  # 2 years: missing data fails


def test_ma200_slope_and_extension_are_optional_and_fail_on_missing_data():
    assert [c.name for c in verdict().checks] == ["trend", "growth", "profit", "valuation"]  # defaults add no check
    assert verdict(price_only=True).checks == verdict(price_only=True, max_extension_above_ma200_pct=0.0, require_rising_ma200=False).checks
    falling = [float(i) for i in range(250, 0, -1)]
    assert verdict(price=300.0, closes=falling, require_rising_ma200=True).failed_reasons[0].startswith("ma_slope")
    assert verdict(require_rising_ma200=True).passed
    assert verdict(closes=RISING[:210], require_rising_ma200=True).failed_reasons[0].startswith("ma_slope")  # < 220 bars
    assert verdict(price_only=True, require_rising_ma200=True).passed
    assert verdict(max_extension_above_ma200_pct=30).failed_reasons[0].startswith("extension")  # 260 vs avg 150
    assert verdict(max_extension_above_ma200_pct=80).passed
    assert verdict(price=100.0, max_extension_above_ma200_pct=30).failed_reasons[0].startswith("trend")


def test_volume_check_is_off_by_default_and_fails_missing_data_only_when_on():
    assert [c.name for c in verdict(volume_ratio=0.2).checks] == ["trend", "growth", "profit", "valuation"]  # off: ignored
    assert verdict(block_on_distribution=True, volume_ratio=1.0).passed  # neutral passes the veto
    assert verdict(block_on_distribution=True, volume_ratio=0.7).failed_reasons[0].startswith("volume")  # <= 1/1.3
    assert verdict(block_on_distribution=True, volume_ratio=None).failed_reasons[0].startswith("volume")
    assert verdict(require_accumulation=True, volume_ratio=1.0).failed_reasons[0].startswith("volume")  # strict
    assert verdict(require_accumulation=True, volume_ratio=1.3).passed
    assert [c.name for c in verdict(price_only=True, block_on_distribution=True, volume_ratio=0.1).checks] == ["trend"]


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

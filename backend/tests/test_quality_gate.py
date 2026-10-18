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


def _oi(o0, o1, r1=114e6):
    return [FinancialYear(2024, 100e6, 10e6, o0), FinancialYear(2025, r1, 14e6, o1)]


def test_operating_leverage_is_optional_and_never_guesses():
    assert [c.name for c in verdict(financial_years=_oi(10e6, 20e6)).checks] == ["trend", "growth", "profit", "valuation"]
    assert verdict(financial_years=_oi(10e6, 20e6), require_operating_leverage=True).passed  # +100% vs +14%
    assert verdict(financial_years=_oi(10e6, 11e6), require_operating_leverage=True).failed_reasons[0].startswith("operating_leverage")
    assert not verdict(financial_years=GOOD_YEARS, require_operating_leverage=True).passed  # None fails
    assert not verdict(financial_years=_oi(10e6, None), require_operating_leverage=True).passed
    assert not verdict(financial_years=_oi(10e6, 20e6, r1=90e6), min_growth_pct=-50, require_operating_leverage=True).passed  # shrinking revenue
    assert not verdict(financial_years=GOOD_YEARS[:1], require_operating_leverage=True).passed
    assert verdict(price_only=True, require_operating_leverage=True).passed


def test_operating_leverage_zero_or_negative_prior_base():
    for base in (0.0, -5e6):
        assert verdict(financial_years=_oi(base, 3e6), require_operating_leverage=True).passed  # swung to profit
        assert not verdict(financial_years=_oi(base, base), require_operating_leverage=True).passed
        assert not verdict(financial_years=_oi(base, -1e6), require_operating_leverage=True).passed


def test_financial_year_operating_income_round_trips():
    from app.data_providers.cache_codec import decode_value, encode_value
    fy = [FinancialYear(2024, 1.0, 2.0, 3.0), FinancialYear(2025, 1.0, 2.0)]
    assert decode_value(encode_value(fy)) == fy


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


def _health(de=0.5, cr=2.0, fcf=5e6):
    return [FinancialYear(2024, 100e6, 10e6), FinancialYear(2025, 114e6, 14e6, None, de, cr, fcf)]


def test_financial_health_is_optional_and_never_guesses():
    assert "financial_health" not in [c.name for c in verdict(financial_years=_health()).checks]
    assert verdict(financial_years=_health(), require_financial_health=True).passed
    for bad in (_health(de=1.5), _health(cr=0.8), _health(fcf=-1e6), _health(fcf=0.0)):
        assert verdict(financial_years=bad, require_financial_health=True).failed_reasons[0].startswith("financial_health")
    assert "free cash flow not reported" in verdict(financial_years=_health(fcf=None), require_financial_health=True).failed_reasons[0]
    assert not verdict(financial_years=GOOD_YEARS, require_financial_health=True).passed  # nothing reported
    assert not verdict(financial_years=GOOD_YEARS[:1], require_financial_health=True).passed
    assert verdict(price_only=True, require_financial_health=True).passed


def test_balance_sheet_rating_part():
    from app.analysis.balance_sheet_scoring import score_balance_sheet

    assert score_balance_sheet("long", _health())[0] == 1
    assert score_balance_sheet("long", _health(fcf=-1e6))[0] == -1
    assert score_balance_sheet("long", _health(de=2.5))[0] == -1
    assert score_balance_sheet("long", _health(de=1.5))[0] == 0  # between the bars
    assert score_balance_sheet("long", _health(cr=None))[0] == 0  # missing never guesses
    assert score_balance_sheet("long", GOOD_YEARS)[0] == 0
    assert score_balance_sheet(None, _health())[0] == 0

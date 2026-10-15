from __future__ import annotations

from datetime import date, datetime


# Same shape as every other confluence dimension: small, named, capped, and
# — unlike most of the others — deliberately ONE-DIRECTIONAL. This is a risk
# flag, not a bullish/bearish opinion: options-implied volatility says how
# BIG a move the market expects, never which way. Modelled on score_vix_regime
# (only ever a penalty, never a bonus for "calm").
EXPECTED_MOVE_SCORE_CAP = 1

# How many multiples of the stock's own recent realized range the
# options-implied expected move has to exceed before it's "elevated" rather
# than routine. 1.5x is a deliberately loose bar — this fires only when the
# options market is pricing meaningfully more movement than the stock's own
# recent behavior would suggest, which is closer to "the market is bracing
# for something specific" than to background options-market noise.
ELEVATED_RATIO_THRESHOLD = 1.5

# Below this many calendar days to expiration, IV quotes get noisy (thin
# 0DTE-adjacent markets, pin risk) — same threshold reasoning as
# YFinanceProvider.OPTIONS_MIN_DAYS_TO_EXPIRATION, applied again here because
# this module doesn't get to see which expiration a caller chose to skip.
MIN_DAYS_FOR_RELIABLE_READ = 5


def days_to_expiration(expiration: str, as_of: date | None = None) -> int | None:
    """Calendar days between `as_of` (default: today) and an "YYYY-MM-DD"
    expiration string. None on anything unparseable rather than guessing."""
    try:
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (exp_date - (as_of or date.today())).days


def compute_expected_move_pct(atm_implied_volatility: float, days: int) -> float | None:
    """Options-implied expected move over `days`, as a +/- percentage of price.

    Standard options-desk approximation: IV is quoted annualized, so it scales
    down to a shorter horizon by the square root of time (variance is additive
    over time, so standard deviation — which IV is — scales with sqrt(t), not
    t itself). `atm_implied_volatility` is a decimal fraction (0.30 = 30%
    annualized), matching what OptionsSummary/yfinance's `impliedVolatility`
    field returns.

    This is not a prediction of direction, or even that the move WILL happen
    — it's a read of what the options market is currently paying to hedge,
    expressed in the same units (+/-%) a stop or a target already uses.
    """
    if atm_implied_volatility <= 0 or days <= 0:
        return None
    return atm_implied_volatility * (days / 365.0) ** 0.5 * 100


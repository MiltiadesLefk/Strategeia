from __future__ import annotations

from datetime import date, datetime

from app.data_providers.base import OptionsSummary

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


def score_expected_move(
    direction: str | None,
    options: OptionsSummary | None,
    atr_pct: float | None,
) -> tuple[int, list[str]]:
    """Compares the options market's implied expected move (over the time to
    its nearest usable expiration) against the stock's own ATR-implied
    "normal" move over that same horizon. When the options market is pricing
    in materially more movement than the stock's recent realized behavior —
    ELEVATED_RATIO_THRESHOLD or more — that is real information extracted
    from current prices: something is being priced in, whether or not this
    scan happens to know what.

    Deliberately never a bonus: an ordinary/low implied move contributes 0,
    same as a calm VIX reading contributes 0. Missing options coverage
    (crypto, thin names), a missing ATR read, or a too-near expiration all
    contribute 0 rather than being guessed at.
    """
    if options is None or options.atm_implied_volatility is None or not atr_pct:
        return 0, []

    days = days_to_expiration(options.expiration)
    if days is None or days < MIN_DAYS_FOR_RELIABLE_READ:
        return 0, []

    expected_move_pct = compute_expected_move_pct(options.atm_implied_volatility, days)
    if expected_move_pct is None:
        return 0, []

    # ATR is a DAILY figure; scale it to the same horizon the same way IV is
    # scaled — sqrt(time) — so the two "expected move over N days" numbers are
    # actually comparable rather than comparing a daily figure to a multi-week one.
    realized_move_pct = atr_pct * (days ** 0.5)
    if realized_move_pct <= 0:
        return 0, []

    ratio = expected_move_pct / realized_move_pct
    if ratio < ELEVATED_RATIO_THRESHOLD:
        return 0, []

    # direction is accepted for signature consistency with every other scorer
    # in this module family, but genuinely unused: this is a magnitude/risk
    # read, not a directional opinion, so it penalizes a long and a short on
    # the same setup identically — see the module docstring.
    del direction
    reason = (
        f"options market pricing a {expected_move_pct:.1f}% move by {options.expiration} — "
        f"{ratio:.1f}x this stock's own recent range for that horizon"
    )
    return -EXPECTED_MOVE_SCORE_CAP, [reason]

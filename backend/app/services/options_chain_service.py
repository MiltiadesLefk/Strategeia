"""Options chain view: one expiration's calls and puts, plus a few numbers
worked out from the chain's own fields.

Read-only. Nothing is stored and nothing is invented: a figure the chain cannot
support (no volume, no usable implied volatility, no open interest) is null, and
there are no Greeks because the free source does not supply them.

Idea from OpenTerminal's OptionsWidget (MIT, ErTasselli/OpenTerminal): an options
table around the money with the nearest expiration first. That widget reads
TradingView's scanner; this one reads the app's own data providers instead, and
the summary maths (ratios, max pain, expected move, skew) is new code.
"""

from __future__ import annotations

from datetime import date

from app.analysis.expected_move import compute_expected_move_pct, days_to_expiration
from app.data_providers.base import (
    AllProvidersFailedError,
    DataProvider,
    DataProviderError,
    OptionContract,
    OptionsChain,
)
from app.schemas.options_schemas import (
    OptionLeg,
    OptionsChainResponse,
    OptionsSummaryOut,
    OptionStrikeRow,
)

# How many strikes either side of the at-the-money strike the table shows by
# default (the summary always uses the whole chain).
DEFAULT_STRIKES_EACH_SIDE = 10
MAX_STRIKES_EACH_SIDE = 60

# Free quotes carry implied volatilities of ~0 on contracts nobody is quoting
# (a stale print at the bid-ask floor). Below one volatility point the number
# says "no market" rather than "calm", so it is treated as missing.
MIN_USABLE_IV = 0.01

# The skew compares puts and calls this far from the price: close enough to be
# actively quoted, far enough to be out of the money. Bands are fractions of
# spot. Without Greeks a fixed moneyness band stands in for the usual
# 25-delta comparison.
SKEW_BAND_NEAR = 0.03
SKEW_BAND_FAR = 0.10
# Volatility points of put-minus-call IV that count as a lean either way.
SKEW_LEAN_POINTS = 1.0

# Why a view is empty, in plain words (shown as-is in the UI).
REASON_CRYPTO = "Crypto pairs have no listed options."
REASON_NO_OPTIONS = "No listed options were found for this symbol (many smaller companies have none)."
NOTE_NO_GREEKS = "Greeks are not shown: the free data source does not supply them."
NOTE_ZERO_VOLUME = "Volume is zero or blank across this expiration (market closed or thinly traded), so the volume ratio is not available."
NOTE_EXPIRING = "This expiration is today or already past, so no expected move is computed."
NOTE_NO_SPOT = "No current price was available, so the at-the-money strike, expected move and skew are not computed."


class UnknownExpirationError(ValueError):
    """The requested expiration is not one the source lists for this symbol."""


def _usable_iv(value: float | None) -> float | None:
    return value if value is not None and value >= MIN_USABLE_IV else None


def _total(contracts: list[OptionContract], field: str) -> float:
    return float(sum(getattr(c, field) or 0.0 for c in contracts))


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def atm_strike(strikes: list[float], spot: float) -> float | None:
    """The listed strike nearest the price (the lower one on an exact tie)."""
    if not strikes:
        return None
    return min(sorted(strikes), key=lambda k: abs(k - spot))


def atm_implied_volatility(calls: list[OptionContract], puts: list[OptionContract], strike: float) -> float | None:
    """Mean of the usable call and put IV at the strike; whichever one exists if
    only one does; None when neither is usable."""
    legs = (
        next((c for c in calls if c.strike == strike), None),
        next((p for p in puts if p.strike == strike), None),
    )
    ivs = [iv for leg in legs if leg is not None and (iv := _usable_iv(leg.implied_volatility)) is not None]
    return sum(ivs) / len(ivs) if ivs else None


def max_pain_strike(calls: list[OptionContract], puts: list[OptionContract], spot: float | None = None) -> float | None:
    """The strike at which the total payout to option holders at expiry is
    smallest, weighting every contract by its open interest. The usual "max
    pain" figure: a description of where open interest is stacked, not a
    forecast. None when there is no open interest at all. A tie goes to the
    strike nearest the price, then the lower one."""
    if _total(calls, "open_interest") + _total(puts, "open_interest") <= 0:
        return None
    candidates = sorted({c.strike for c in calls} | {p.strike for p in puts})

    def payout(at: float) -> float:
        call_pay = sum(max(0.0, at - c.strike) * (c.open_interest or 0.0) for c in calls)
        put_pay = sum(max(0.0, p.strike - at) * (p.open_interest or 0.0) for p in puts)
        return call_pay + put_pay

    reference = spot if spot is not None else candidates[len(candidates) // 2]
    return min(candidates, key=lambda k: (payout(k), abs(k - reference), k))


def iv_skew(calls: list[OptionContract], puts: list[OptionContract], spot: float) -> float | None:
    """Mean put IV below the price minus mean call IV above it (both inside the
    skew bands), in volatility points. None unless both sides have a usable
    quote: one side alone says nothing about a difference."""

    def mean_iv(contracts: list[OptionContract], low: float, high: float) -> float | None:
        ivs = [iv for c in contracts if low <= c.strike <= high and (iv := _usable_iv(c.implied_volatility)) is not None]
        return sum(ivs) / len(ivs) if ivs else None

    put_iv = mean_iv(puts, spot * (1 - SKEW_BAND_FAR), spot * (1 - SKEW_BAND_NEAR))
    call_iv = mean_iv(calls, spot * (1 + SKEW_BAND_NEAR), spot * (1 + SKEW_BAND_FAR))
    if put_iv is None or call_iv is None:
        return None
    return (put_iv - call_iv) * 100.0


def build_summary(chain: OptionsChain, spot: float | None, days: int | None) -> OptionsSummaryOut:
    call_volume = _total(chain.calls, "volume")
    put_volume = _total(chain.puts, "volume")
    call_oi = _total(chain.calls, "open_interest")
    put_oi = _total(chain.puts, "open_interest")

    strike = atm = None
    expected_pct = expected_dollars = skew = None
    skew_label = None
    if spot is not None and spot > 0:
        strike = atm_strike(sorted({c.strike for c in chain.calls} | {p.strike for p in chain.puts}), spot)
        if strike is not None:
            atm = atm_implied_volatility(chain.calls, chain.puts, strike)
        if atm is not None and days is not None:
            expected_pct = compute_expected_move_pct(atm, days)
            if expected_pct is not None:
                expected_dollars = spot * expected_pct / 100.0
        skew = iv_skew(chain.calls, chain.puts, spot)
        if skew is not None:
            skew_label = "puts richer" if skew >= SKEW_LEAN_POINTS else "calls richer" if skew <= -SKEW_LEAN_POINTS else "balanced"

    return OptionsSummaryOut(
        call_volume=call_volume,
        put_volume=put_volume,
        put_call_volume_ratio=_ratio(put_volume, call_volume),
        call_open_interest=call_oi,
        put_open_interest=put_oi,
        put_call_oi_ratio=_ratio(put_oi, call_oi),
        atm_strike=strike,
        atm_implied_volatility=atm,
        expected_move_pct=expected_pct,
        expected_move_dollars=expected_dollars,
        max_pain_strike=max_pain_strike(chain.calls, chain.puts, spot),
        iv_skew_points=skew,
        iv_skew_label=skew_label,
    )


def _leg(contract: OptionContract | None, is_call: bool, spot: float | None) -> OptionLeg | None:
    if contract is None:
        return None
    itm = contract.in_the_money
    if itm is None and spot is not None:
        itm = spot > contract.strike if is_call else spot < contract.strike
    return OptionLeg(
        last_price=contract.last_price,
        bid=contract.bid,
        ask=contract.ask,
        volume=contract.volume,
        open_interest=contract.open_interest,
        implied_volatility=_usable_iv(contract.implied_volatility),
        in_the_money=itm,
        contract_symbol=contract.contract_symbol,
    )


def build_rows(chain: OptionsChain, spot: float | None, each_side: int) -> tuple[list[OptionStrikeRow], int]:
    """One row per strike, calls and puts side by side, limited to `each_side`
    strikes either side of the at-the-money one. Returns (rows, strikes total).
    With no price to centre on, the middle of the chain is used."""
    calls = {c.strike: c for c in chain.calls}
    puts = {p.strike: p for p in chain.puts}
    strikes = sorted(set(calls) | set(puts))
    if not strikes:
        return [], 0
    centre = atm_strike(strikes, spot) if spot is not None and spot > 0 else strikes[len(strikes) // 2]
    index = strikes.index(centre)
    shown = strikes[max(0, index - each_side) : index + each_side + 1]
    rows = [
        OptionStrikeRow(
            strike=k,
            is_atm=(spot is not None and k == centre),
            call=_leg(calls.get(k), True, spot),
            put=_leg(puts.get(k), False, spot),
        )
        for k in shown
    ]
    return rows, len(strikes)


def _empty(symbol: str, reason: str) -> OptionsChainResponse:
    return OptionsChainResponse(symbol=symbol, available=False, reason=reason)


def get_options_view(
    provider: DataProvider,
    symbol: str,
    expiration: str | None = None,
    strikes_each_side: int = DEFAULT_STRIKES_EACH_SIDE,
    today: date | None = None,
) -> OptionsChainResponse:
    """The chain for `symbol` and one expiration (the nearest when None).

    Raises UnknownExpirationError for an expiration the source does not list.
    A symbol with no options (or a source that failed) is a normal empty view
    with a reason, not an error."""
    symbol = symbol.upper()
    if symbol.endswith("-USD"):
        return _empty(symbol, REASON_CRYPTO)

    try:
        chain = provider.get_options_chain(symbol, None)
    except (AllProvidersFailedError, DataProviderError, NotImplementedError):
        chain = None
    if chain is None:
        return _empty(symbol, REASON_NO_OPTIONS)

    if expiration is not None and expiration != chain.expiration:
        if expiration not in chain.expirations:
            raise UnknownExpirationError(f"{expiration} is not a listed expiration for {symbol}")
        try:
            chain = provider.get_options_chain(symbol, expiration)
        except (AllProvidersFailedError, DataProviderError, NotImplementedError):
            chain = None
        if chain is None:
            return _empty(symbol, REASON_NO_OPTIONS)

    spot = chain.spot
    if spot is None:
        try:
            spot = float(provider.get_quote(symbol).price)
        except (AllProvidersFailedError, DataProviderError, NotImplementedError):
            spot = None

    days = days_to_expiration(chain.expiration, today)
    each_side = max(1, min(strikes_each_side, MAX_STRIKES_EACH_SIDE))
    rows, total = build_rows(chain, spot, each_side)
    summary = build_summary(chain, spot, days)

    notes = [NOTE_NO_GREEKS]
    if summary.call_volume <= 0:
        notes.append(NOTE_ZERO_VOLUME)
    if days is not None and days <= 0:
        notes.append(NOTE_EXPIRING)
    if spot is None:
        notes.append(NOTE_NO_SPOT)

    return OptionsChainResponse(
        symbol=symbol,
        available=True,
        expiration=chain.expiration,
        expirations=chain.expirations,
        days_to_expiration=days,
        spot=spot,
        strikes_total=total,
        strikes_shown=len(rows),
        summary=summary,
        rows=rows,
        notes=notes,
    )

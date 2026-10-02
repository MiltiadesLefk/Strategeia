from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class OptionLeg(BaseModel):
    """One listed contract, as the source quoted it. A blank stays null: a
    missing bid is not a bid of zero. There are no Greeks on purpose: the free
    source does not supply them and they would have to be invented."""

    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    implied_volatility: float | None = None
    in_the_money: bool | None = None
    contract_symbol: str | None = None


class OptionStrikeRow(BaseModel):
    strike: float
    # True for the one strike nearest the current price.
    is_atm: bool
    call: OptionLeg | None = None
    put: OptionLeg | None = None


class OptionsSummaryOut(BaseModel):
    """Everything here is computed from the chain's own fields, over the WHOLE
    expiration (not just the strikes shown in the table)."""

    call_volume: float
    put_volume: float
    # Put volume / call volume; null when no calls traded.
    put_call_volume_ratio: float | None = None
    call_open_interest: float
    put_open_interest: float
    put_call_oi_ratio: float | None = None
    atm_strike: float | None = None
    # Decimal fraction (0.30 = 30% annualised), the mean of the call and put at the ATM strike.
    atm_implied_volatility: float | None = None
    # +/- percent of price over the days to expiration (IV x sqrt(time)); a size, not a direction.
    expected_move_pct: float | None = None
    expected_move_dollars: float | None = None
    # The strike at which option writers would pay out least, from open interest.
    max_pain_strike: float | None = None
    # Volatility points (put IV minus call IV) for strikes a few percent either side of the price.
    iv_skew_points: float | None = None
    iv_skew_label: Literal["puts richer", "calls richer", "balanced"] | None = None


class OptionsChainResponse(BaseModel):
    symbol: str
    # False when there is nothing to show; `reason` says why in plain words.
    available: bool
    reason: str | None = None
    expiration: str | None = None
    expirations: list[str] = []
    days_to_expiration: int | None = None
    spot: float | None = None
    # Total strikes in the expiration, and how many the table shows.
    strikes_total: int = 0
    strikes_shown: int = 0
    summary: OptionsSummaryOut | None = None
    rows: list[OptionStrikeRow] = []
    notes: list[str] = []

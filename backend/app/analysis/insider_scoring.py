from __future__ import annotations

from app.data_providers.base import InsiderActivity

# Same shape as every other scoring dimension here: small, named, capped, and
# direction-aware, so it can inform a decision without ever being able to make
# one on its own (see trade_plan_service.MAX_SCORE_FOR_CONFIDENCE).
INSIDER_SCORE_CAP = 1

# Below this, a cluster of insider buying is too small to mean anything — a
# director picking up a few thousand dollars of stock is noise, not conviction.
MIN_NET_BUY_VALUE = 100_000.0


def score_insider_activity(direction: str | None, activity: InsiderActivity | None) -> tuple[int, list[str]]:
    """Scores open-market insider BUYING only. Selling is never penalised.

    This asymmetry is the whole point, and it is deliberate. Executives sell
    constantly and for reasons that have nothing to do with their view of the
    business: diversification, tax bills, and pre-scheduled 10b5-1 plans set up
    months earlier. Treating that as bearish would mark almost every
    equity-compensating company bearish almost all the time — NVDA shows
    $653m of insider selling and zero buying over a routine 90-day window, and
    reading that as a signal would be reading the payroll.

    Buying is different: an insider has no obligation to buy, gets no tax or
    liquidity benefit from it, and is trading against their own concentration
    risk. It is the one direction that requires a reason.

    So this returns a bonus for meaningful net buying and 0 otherwise — the
    same one-directional shape as score_vix_regime, which only ever penalises
    and never rewards. A missing read (crypto, a non-registrant, a failed
    fetch) also contributes 0 rather than being guessed at.
    """
    if direction is None or activity is None:
        return 0, []
    if activity.buy_count == 0 or activity.net_value < MIN_NET_BUY_VALUE:
        return 0, []

    sign = -1 if direction == "short" else 1
    buyers = f"{activity.buy_count} insider purchase{'s' if activity.buy_count != 1 else ''}"
    reason = (
        f"{buyers} totalling ${activity.net_value:,.0f} net in the last "
        f"{activity.window_days} days"
    )
    if direction == "short":
        reason += " — insiders buying against this short"
    return sign * INSIDER_SCORE_CAP, [reason]

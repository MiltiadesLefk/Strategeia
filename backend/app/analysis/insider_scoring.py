from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from app.data_providers.base import InsiderActivity

# Direction-aware and capped like every other scoring dimension here, so it can inform a decision without
# ever being able to make one on its own (see trade_plan_service.MAX_SCORE_FOR_CONFIDENCE). Two points
# either way: the first for a meaningful move, the second for a very large one.
INSIDER_SCORE_CAP = 2

# Buying: net open-market purchases (bought minus sold) by insiders. Below the first tier a director picking
# up a few thousand dollars of stock is noise, not conviction.
MIN_NET_BUY_VALUE = 100_000.0
STRONG_NET_BUY_VALUE = 1_000_000.0

# Selling counts too, weighted by how much of a choice it was. A sale made by the insider's own decision
# counts in full; a sale under a 10b5-1 plan (scheduled months earlier, so it says little about today's view)
# counts for PLAN_SELL_WEIGHT of its value. The weighted total, less what insiders bought, is compared with
# these tiers.
PLAN_SELL_WEIGHT = 0.25
MIN_WEIGHTED_SELL_VALUE = 5_000_000.0
STRONG_WEIGHTED_SELL_VALUE = 50_000_000.0

# Kept for the Committee's data and older callers: the discretionary-sale floor the first version used.
MIN_DISCRETIONARY_SELL_VALUE = MIN_WEIGHTED_SELL_VALUE


@dataclass(frozen=True)
class SellSplit:
    """Open-market insider sales over the window, split by whether they were made under a 10b5-1 plan."""

    discretionary_count: int
    discretionary_value: float
    plan_count: int
    plan_value: float

    @property
    def weighted_value(self) -> float:
        return self.discretionary_value + PLAN_SELL_WEIGHT * self.plan_value


def insider_sell_split(session: Session | None, symbol: str, window_days: int = 90) -> SellSplit | None:
    """Sales split into discretionary and plan, from the dated Form 4 archive as known now. None when no insider
    filing for the symbol was ever stored (never loaded: not the same as "nobody sold"). Reads through the
    guarded reader, so a backtest sees only what was public."""
    if session is None:
        return None
    from app.knowledge import FactKind, facts_known_as_of
    from app.knowledge.insider_trades import OPEN_MARKET_SELL, insider_trades_as_of

    if not facts_known_as_of(session, FactKind.INSIDER_TRADE, symbol=symbol, limit=1):
        return None
    sales = insider_trades_as_of(session, symbol, window_days=window_days, codes=(OPEN_MARKET_SELL,))
    discretionary = [t for t in sales if not t.is_10b5_1]
    planned = [t for t in sales if t.is_10b5_1]
    return SellSplit(
        len(discretionary), sum(t.value or 0.0 for t in discretionary), len(planned), sum(t.value or 0.0 for t in planned)
    )


def discretionary_sells(session: Session | None, symbol: str, window_days: int = 90) -> tuple[int, float] | None:
    """(count, value) of the discretionary sales alone, or None when the archive has nothing for the symbol."""
    split = insider_sell_split(session, symbol, window_days)
    return None if split is None else (split.discretionary_count, split.discretionary_value)


def _buy_points(activity: InsiderActivity | None) -> int:
    if activity is None or activity.buy_count == 0 or activity.net_value < MIN_NET_BUY_VALUE:
        return 0
    return 2 if activity.net_value >= STRONG_NET_BUY_VALUE else 1


def _sell_points(activity: InsiderActivity | None, split: SellSplit | None) -> tuple[int, float]:
    """(points, weighted value net of purchases). Without the archive's plan flag every sale the provider
    counted is treated as a plan sale (the cautious weight), never as a discretionary one."""
    bought = activity.buy_value if activity is not None else 0.0
    if split is not None:
        weighted = split.weighted_value
    elif activity is not None:
        weighted = PLAN_SELL_WEIGHT * activity.sell_value
    else:
        return 0, 0.0
    net = weighted - bought
    if net >= STRONG_WEIGHTED_SELL_VALUE:
        return 2, net
    return (1, net) if net >= MIN_WEIGHTED_SELL_VALUE else (0, net)


def score_insider_activity(
    direction: str | None,
    activity: InsiderActivity | None,
    sell_split: SellSplit | None = None,
) -> tuple[int, list[str]]:
    """Insider buying AND selling, signed for the trade's direction.

    Buying is the stronger signal, since an insider gets no tax or liquidity benefit from it and trades against
    their own concentration risk. Selling is weaker, since executives sell for diversification, taxes and
    scheduled 10b5-1 plans, so it is weighted (see PLAN_SELL_WEIGHT) and has to be large. Both feed one net:
    buying points minus selling points, up to INSIDER_SCORE_CAP either way. Net buying supports a long and
    argues against a short; net selling does the reverse. A missing read (crypto, a non-registrant, a failed
    fetch) contributes 0 rather than being guessed at.
    """
    if direction not in ("long", "short"):
        return 0, []
    buy = _buy_points(activity)
    sell, net_sell = _sell_points(activity, sell_split)
    net = buy - sell
    if net == 0:
        return 0, []
    window = activity.window_days if activity is not None else 90
    if net > 0:
        reason = (
            f"{activity.buy_count} insider purchase{'s' if activity.buy_count != 1 else ''} totalling "
            f"${activity.net_value:,.0f} net in the last {window} days"
        )
    else:
        if sell_split is not None:
            reason = (
                f"insiders sold ${sell_split.discretionary_value:,.0f} by their own choice and "
                f"${sell_split.plan_value:,.0f} under 10b5-1 plans in the last {window} days"
            )
        else:
            reason = f"insiders sold ${activity.sell_value:,.0f} in the last {window} days (plan flags not loaded)"
    bullish = net > 0
    supports = bullish == (direction == "long")
    points = abs(net) if supports else -abs(net)
    verb = "supports" if supports else "argues against"
    return max(-INSIDER_SCORE_CAP, min(INSIDER_SCORE_CAP, points)), [f"{reason}, which {verb} a {direction}"]

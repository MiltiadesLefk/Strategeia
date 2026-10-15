"""What a hold backtest can honestly rebuild, recorded with every run: only the price checks of the live
quality gate exist for a past date. Older runs stored a score-points shape (profiles `price_only` and
`price_plus_dated_data`); readers still accept it, nothing writes it any more."""

from __future__ import annotations

from typing import Any

PROFILE_HOLD_PRICE_ONLY = "hold_price_only"


def describe_hold_coverage(buy_only_dips: bool = False) -> dict[str, Any]:
    """The coverage record of a hold run: the live gate's trend check (and the dip rule when it is on)
    is all a past date can know."""
    checks_run = [{"check": "trend", "label": "Close above the 200-day average"}]
    if buy_only_dips:
        checks_run.append({"check": "dip", "label": "Buy only in a dip (10% under the 3-month high)"})
    not_run = [
        ("growth", "Revenue growth", "no dated net-income or revenue history is served to a backtest"),
        ("profit", "Profitable", "no dated net-income history is served to a backtest; a missing figure is never counted as a pass"),
        ("valuation", "Valuation (P/E)", "no dated P/E history is served to a backtest"),
        ("news_discovery", "News discovery of tickers outside the list", "not in v1"),
        ("big_news", "Big-news buy lane", "not in v1; no dated headline archive to replay"),
        ("dip_add", "Adding to a held dip", "not in v1"),
        ("sell_plan", "Sell plan and rebuy watch", "not in v1; the only exit is the disaster stop on a final daily close (a backtest always uses hold_stop_mode close: the live touch check has no replay)"),
    ]
    if not buy_only_dips:
        not_run.insert(3, ("dip", "Dip rule", "switched off for this run (hold_buy_only_dips)"))
    return {
        "profile": PROFILE_HOLD_PRICE_ONLY,
        "summary": (
            "Price-only approximation of the live quality gate (hold run): only the trend check"
            + (" and the dip rule are" if buy_only_dips else " is")
            + " run. Optimistic: today's index members (survivors), daily bars, no costs beyond slippage."
        ),
        "checks_run": checks_run,
        "checks_not_run": [{"check": c, "label": label, "reason": reason} for c, label, reason in not_run],
        "optimistic": True,
        "min_confidence_note": "min_confidence_for_trade is irrelevant to a hold run: nothing is scored",
        # Same keys the older readers use, empty or None, so the score-points views have nothing to misread.
        "included": {"fundamentals": False, "insiders": False, "earnings": False},
        "achievable_points": None,
        "live_points_max": None,
        "active_parts": [],
        "inactive_parts": [{"part": c, "label": label, "live_points_max": None, "reason": reason} for c, label, reason in not_run],
        "bar_reachable": None,
    }

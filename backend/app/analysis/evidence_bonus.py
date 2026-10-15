"""Evidence bonus for a universe stock, pure rules (no I/O, no AI): news and smart money for THAT stock add a
small capped number of points. Missing data is 0, never a penalty; insider/Congress SELLS never count. The bonus only
ranks candidates and gates out a negative one; it never lets a stock past a failed fundamentals check
(the owner's studies found no proven edge for these signals, so it stays small)."""

from __future__ import annotations

from dataclasses import dataclass

BONUS_CAP = 3
BONUS_FLOOR = -1
INSIDER_CLUSTER_POINTS = 2  # two or more insiders buying within the cluster window
INSIDER_BUY_POINTS = 1  # a lone open-market insider buy
CONGRESS_BUY_POINTS = 1  # at least one member's buy filed in the window
BIG_NEWS_POINTS = 1
NEGATIVE_NEWS_POINTS = BONUS_FLOOR  # a negative hit sets the floor, whatever else is positive


@dataclass(frozen=True)
class EvidenceBonus:
    points: int = 0
    parts: tuple[str, ...] = ()

    def describe(self) -> str:
        return f"evidence {self.points:+d}" + (": " + ", ".join(self.parts) if self.parts else "")


def compute_bonus(
    insider_buys: int = 0, insider_cluster: bool = False, congress_buys: int = 0, big_news: bool = False, negative_news: bool = False,
) -> EvidenceBonus:
    """Counts in, bonus out. Sells are not inputs on purpose."""
    if negative_news:
        return EvidenceBonus(NEGATIVE_NEWS_POINTS, ("negative news",))
    points, parts = 0, []
    if insider_cluster:
        points += INSIDER_CLUSTER_POINTS
        parts.append("insider cluster buy")
    elif insider_buys > 0:
        points += INSIDER_BUY_POINTS
        parts.append("insider buy")
    if congress_buys > 0:
        points += CONGRESS_BUY_POINTS
        parts.append("Congress buy")
    if big_news:
        points += BIG_NEWS_POINTS
        parts.append("big news")
    return EvidenceBonus(max(BONUS_FLOOR, min(BONUS_CAP, points)), tuple(parts))

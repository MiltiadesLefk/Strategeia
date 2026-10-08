"""Members of Congress buying the stock, as a silent signal (see analysis/shadow_signals.py).

What it reads: how many DIFFERENT House members bought and how many sold this
stock in trade reports filed over the last 45 days. The net of those two counts is
the evidence: several members buying independently says more than one member (or a
spouse's broker) buying once.

Direction-signed like the app's other confluence checks: net buying supports a
long (+1) and argues against a short (-1); net selling does the opposite; with no
clear direction it scores 0. It needs a net of at least MIN_NET_MEMBERS different
members to move at all, because a single member's report is the noise this signal's
own evidence base warns about. The points are recorded on every plan but never added
to confidence until a backtest shows the signal beats luck.

Known weaknesses, stated up front:
  * Disclosure lag. A member has up to 45 days to file, so a trade seen here is
    usually weeks old by the time it can be seen. The window is therefore measured
    on the FILING date (what was public), not the trade date.
  * Amounts are ranges and are not used at all: only the count of members counts.
  * Spouse and dependent-child trades are included, because the report does not
    separate them cleanly from the member's own decisions.
  * House only: the Senate's site refuses scripted access, so no Senate trade is
    counted.

No stored Congress data at all is reported as unavailable (it was never loaded),
which is different from "loaded, and nothing in the window".
"""

from __future__ import annotations

from sqlmodel import Session

from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
from app.knowledge.congress_trades import has_congress_data, net_buyers_as_of

SIGNAL_NAME = "congress_buying"
# Largest swing this signal would add in either direction.
CONGRESS_SCORE_CAP = 2
# How far back reports count (days before now, on the filing date).
WINDOW_DAYS = 45
# Net distinct members needed (buyers minus sellers) before the signal scores.
MIN_NET_MEMBERS = 2
# A net of this many different members is a strong reading: the second point.
STRONG_NET_MEMBERS = 4


def score_congress_net(direction: str | None, buyers: int, sellers: int) -> tuple[int, str]:
    """(points, reason) for the member counts."""
    net = buyers - sellers
    counts = f"{buyers} member(s) bought and {sellers} sold in reports filed in the last {WINDOW_DAYS} days"
    if abs(net) < MIN_NET_MEMBERS:
        return 0, f"{counts}: a net of {abs(net)} is below the {MIN_NET_MEMBERS} needed to count."
    if direction not in ("long", "short"):
        return 0, f"{counts}, but there is no clear direction to sign it by."
    supports_long = net > 0
    size = CONGRESS_SCORE_CAP if abs(net) >= STRONG_NET_MEMBERS else 1
    points = size if supports_long == (direction == "long") else -size
    verb = "supports" if points > 0 else "argues against"
    leaning = "net buying" if supports_long else "net selling"
    return points, f"{counts}: {leaning} {verb} a {direction}."


def build_congress_signal(direction: str | None, session: Session | None, symbol: str) -> ShadowSignal:
    if session is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No database session to read Congress trades from.", available=False)
    if not has_congress_data(session):
        return ShadowSignal(SIGNAL_NAME, None, 0, "No Congress trade reports stored.", available=False)
    counts = net_buyers_as_of(session, symbol, window_days=WINDOW_DAYS)
    points, reason = score_congress_net(direction, counts.buyers, counts.sellers)
    return ShadowSignal(SIGNAL_NAME, f"{counts.net:+d}", points, reason, available=True)


@shadow_signal(SIGNAL_NAME)
def _congress_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_congress_signal(context.direction, context.session, context.symbol)

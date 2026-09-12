from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# The bundled universe is US equities plus a handful of `-USD` crypto pairs,
# so "is there anything to look at right now" is a US-session question for
# everything except crypto, which never closes.
US_MARKET_TZ = ZoneInfo("America/New_York")
US_MARKET_OPEN = time(9, 30)
US_MARKET_CLOSE = time(16, 0)

# Daily bars for a session aren't final the instant the bell rings; give the
# provider a little room to settle the last bar before the post-close sweep
# stops running.
POST_CLOSE_GRACE = timedelta(minutes=30)

# yfinance spells 24/7 instruments as `<BASE>-USD` (BTC-USD, ETH-USD, ...).
CRYPTO_SUFFIX = "-USD"


def is_always_on(symbol: str) -> bool:
    """True for instruments with no session at all (crypto pairs). Used to
    keep marking positions that can still move while equities are shut."""
    return symbol.upper().endswith(CRYPTO_SUFFIX)


def is_us_market_open(now: datetime | None = None) -> bool:
    """Regular US cash session, DST-aware via zoneinfo.

    Deliberately does NOT know about market holidays: the only consequence of
    running on Thanksgiving is a handful of wasted provider calls that find an
    unchanged bar, which is a cost question rather than a correctness one. A
    holiday calendar would be another dependency to keep current for no
    behavioural gain.
    """
    now_et = (now or datetime.now(US_MARKET_TZ)).astimezone(US_MARKET_TZ)
    if now_et.weekday() >= 5:  # Saturday/Sunday
        return False
    return US_MARKET_OPEN <= now_et.time() <= US_MARKET_CLOSE


def is_within_post_close_grace(now: datetime | None = None) -> bool:
    """True in the window just after the close, so the final daily bar gets
    marked once it settles rather than waiting for the next session."""
    now_et = (now or datetime.now(US_MARKET_TZ)).astimezone(US_MARKET_TZ)
    if now_et.weekday() >= 5:
        return False
    close_at = now_et.replace(
        hour=US_MARKET_CLOSE.hour, minute=US_MARKET_CLOSE.minute, second=0, microsecond=0
    )
    return close_at < now_et <= close_at + POST_CLOSE_GRACE

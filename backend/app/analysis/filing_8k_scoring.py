"""Recent 8-K announcements as a silent signal (see analysis/shadow_signals.py).

What it reads: whether the company filed an 8-K in the last few days that lists
an item code of the "bad news" kind: bankruptcy (1.03), a material impairment
(2.06), a delisting notice (3.01), a statement that earlier financials can no
longer be relied on (4.02), or a director/officer change (5.02). Item 5.02 is
the weakest of these: the code covers appointments and pay changes as well as
departures, and the filing's text is not read here, so it is a hint, not a
verdict.

Direction-signed like the app's other confluence checks: bad news argues
against a long (-1) and mildly supports a short (+1); with no clear direction it
scores 0. The points are recorded on every plan but never added to confidence
until a backtest shows the signal beats luck.

No stored 8-K data for the company at all is reported as unavailable (it was
never loaded), which is different from "loaded, and nothing in the window".
"""

from __future__ import annotations

from sqlmodel import Session

from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
from app.data_providers.sec_8k import StoredFiling8K, filings_8k_as_of, has_8k_data

SIGNAL_NAME = "sec_8k_negative_items"
# Largest swing this signal would add in either direction.
FILING_8K_SCORE_CAP = 1
# How recent a filing must be to count as news rather than history.
RECENT_WINDOW_DAYS = 3
# Item codes read as negative-leaning (see the module note on 5.02).
NEGATIVE_LEANING_ITEMS = frozenset({"1.03", "2.06", "3.01", "4.02", "5.02"})


def score_negative_8k(direction: str | None, filings: list[StoredFiling8K]) -> tuple[int, str, list[str]]:
    """(points, reason, matched item codes) for the recent filings."""
    matched = sorted({code for f in filings for code in f.items if code in NEGATIVE_LEANING_ITEMS})
    if not matched:
        return 0, f"No negative-leaning 8-K item in the last {RECENT_WINDOW_DAYS} days.", []
    listed = ", ".join(matched)
    if direction not in ("long", "short"):
        return 0, f"8-K item(s) {listed} filed recently, but there is no clear direction to sign them by.", matched
    if direction == "long":
        return -FILING_8K_SCORE_CAP, f"8-K item(s) {listed} filed in the last {RECENT_WINDOW_DAYS} days argue against a long.", matched
    return FILING_8K_SCORE_CAP, f"8-K item(s) {listed} filed in the last {RECENT_WINDOW_DAYS} days mildly support a short.", matched


def build_8k_signal(direction: str | None, session: Session | None, symbol: str) -> ShadowSignal:
    if session is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No database session to read 8-K filings from.", available=False)
    if not has_8k_data(session, symbol):
        return ShadowSignal(SIGNAL_NAME, None, 0, "No 8-K filings stored for this symbol.", available=False)
    filings = filings_8k_as_of(session, symbol, window_days=RECENT_WINDOW_DAYS)
    points, reason, matched = score_negative_8k(direction, filings)
    return ShadowSignal(SIGNAL_NAME, ",".join(matched) if matched else "none", points, reason, available=True)


@shadow_signal(SIGNAL_NAME)
def _filing_8k_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_8k_signal(context.direction, context.session, context.symbol)

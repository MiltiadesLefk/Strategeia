"""Recent 8-K announcements as a silent signal (a ShadowSignal reading).

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


from app.data_providers.sec_8k import StoredFiling8K

SIGNAL_NAME = "sec_8k_negative_items"
# Largest swing this signal would add in either direction.
FILING_8K_SCORE_CAP = 1
# How recent a filing must be to count as news rather than history.
RECENT_WINDOW_DAYS = 3
# Item codes read as negative-leaning (see the module note on 5.02).
NEGATIVE_LEANING_ITEMS = frozenset({"1.03", "2.06", "3.01", "4.02", "5.02"})



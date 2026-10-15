"""What the evidence parts added to a swing plan's confidence score after the original ten were called, and how a
stored plan's per-part points and notes are read back. Nothing scores with them any more (swing trading is
retired); old plan rows, the calibration report and the dashboard's last plan still read them.
"""

from __future__ import annotations

import json

# key, label, cap, penalty_only. Order is the display order.
EXTRA_PARTS: tuple[tuple[str, str, int, bool], ...] = (
    ("congress", "Congress", 2, False),
    ("funds", "Funds (13F)", 2, False),
    ("ownership", "5% owners", 2, False),
    ("short_volume", "Short volume", 1, False),
    ("filing_8k", "8-K filings", 1, False),
    ("financial_health", "Financials", 1, False),
    ("valuation", "Valuation", 2, False),
    ("analyst_consensus", "Consensus", 1, False),
    ("relative_strength", "Vs market", 1, False),
    ("volume_trend", "Volume trend", 1, False),
    ("options_oi", "Options OI", 1, False),
    ("fed_window", "Fed window", 1, True),
    ("post_mentions", "Posts", 1, True),
)
EXTRA_LABELS = {key: label for key, label, _cap, _penalty in EXTRA_PARTS}
EXTRA_CAPS = {key: cap for key, _label, cap, _penalty in EXTRA_PARTS}
PENALTY_ONLY = frozenset(key for key, _label, _cap, penalty in EXTRA_PARTS if penalty)
# Caution flags never add to what a plan can earn, so they stay out of the achievable maximum.
EXTRA_MAX_POINTS = sum(cap for _key, _label, cap, penalty in EXTRA_PARTS if not penalty)
# Parts a backtest can rebuild from prices alone.
PRICE_ONLY_PARTS = ("relative_strength", "volume_trend")


def extra_scores_from_json(raw: str | None) -> dict[str, int] | None:
    """The stored per-part points, or None for a plan made before these parts existed."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}


def extra_notes_from_json(raw: str | None) -> dict[str, str] | None:
    """The stored per-part explanations, or None for a plan made before they were kept."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    notes = data.get("_why") if isinstance(data, dict) else None
    if not isinstance(notes, dict):
        return None
    return {str(k): str(v) for k, v in notes.items()}


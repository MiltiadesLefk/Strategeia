"""Running totals of what the Claude CLI reports for each call (tokens in the reply's `usage`), since the process started.
A run (Rate all) takes a snapshot before and after and keeps the difference. Counts only; no cost, nothing decided from it."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_totals = {"calls": 0, "tokens_in": 0, "tokens_out": 0}


def record(envelope: dict) -> None:
    usage = envelope.get("usage") if isinstance(envelope, dict) else None
    if not isinstance(usage, dict):
        return
    tokens_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    with _lock:
        _totals["calls"] += 1
        _totals["tokens_in"] += tokens_in
        _totals["tokens_out"] += int(usage.get("output_tokens") or 0)


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_totals)

"""How much of the Claude subscription's 5-hour and 7-day windows is used, read from Anthropic's (undocumented) OAuth
usage endpoint with the token of the local Claude login. Display only: nothing decides anything from it. Any failure
(no token, a token without the right scope, the endpoint changing) is None, never a guess."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_SECONDS = 60
_lock = threading.Lock()
_cache: tuple[float, dict | None] = (0.0, None)


def _tokens() -> list[str]:
    """The login file's token first (it can read usage), then the setup-token env var (may lack the scope)."""
    found: list[str] = []
    try:
        data = json.loads((Path.home() / ".claude" / ".credentials.json").read_text(encoding="utf-8"))
        found.append(data["claudeAiOauth"]["accessToken"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        found.append(os.environ["CLAUDE_CODE_OAUTH_TOKEN"])
    return found


def _fetch() -> dict | None:
    for token in _tokens():
        try:
            r = httpx.get(USAGE_URL, timeout=10, headers={
                "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code/2.1"})
            if r.status_code != 200:
                continue
            body = r.json()
            return {
                "five_hour_pct": float(body["five_hour"]["utilization"]), "five_hour_resets_at": body["five_hour"].get("resets_at"),
                "seven_day_pct": float(body["seven_day"]["utilization"]), "seven_day_resets_at": body["seven_day"].get("resets_at"),
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            logger.info("Claude usage read failed", exc_info=True)
    return None


def get_session_usage(fresh: bool = False) -> dict | None:
    """The window percentages, or None when they cannot be read. Cached for a minute unless `fresh`."""
    global _cache
    with _lock:
        if not fresh and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
    value = _fetch()
    with _lock:
        _cache = (time.monotonic(), value)
    return value

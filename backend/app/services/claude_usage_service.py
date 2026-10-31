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


def _credential_files() -> list[Path]:
    """The login file the CLI keeps fresh: `CLAUDE_CREDENTIALS_FILE` (a read-only mount in Docker), else the home folder's."""
    files = [Path(os.environ["CLAUDE_CREDENTIALS_FILE"])] if os.environ.get("CLAUDE_CREDENTIALS_FILE") else []
    return files + [Path.home() / ".claude" / ".credentials.json"]


def _tokens() -> list[str]:
    """The login file's token first (it can read usage), then the setup-token env var (may lack the scope)."""
    found: list[str] = []
    for path in _credential_files():
        try:
            found.append(json.loads(path.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"])
            break
        except (OSError, ValueError, KeyError, TypeError):
            continue
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

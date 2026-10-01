# Copyright (c) 2026 OpenTerminal contributors (MIT License; full text in THIRD_PARTY_NOTICES.md)
# Adapted from ErTasselli/OpenTerminal@95618ee server/src/providers/fred.ts;
# changes: ported TypeScript -> Python, reads the CSV with a real parser and
# both header spellings, skips FRED's "." missing-value marker, takes a date
# range, validates the series id, uses a polite User-Agent and a timeout, and
# is cached with a stale-on-error fallback.
"""FRED (Federal Reserve Economic Data) series, with no API key.

FRED serves any series as a public CSV download:

    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2026-01-01

    observation_date,DGS10
    2026-09-21,4.96
    2026-09-22,.

The header's first column is `observation_date` today and was `DATE` before:
both are accepted. A day with no observation (a market holiday) is written as
".", which is skipped: it is a gap, not a zero. Anything that is not a CSV (an
HTML error page answered with 200) is an error, never an empty series.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
from collections.abc import Callable
from datetime import date, datetime

import httpx

from app.data_providers import health
from app.data_providers.base import DataProviderError
from app.data_providers.cache import cached
from app.data_providers.macro_series import MacroSeries
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
REQUEST_TIMEOUT_SECONDS = 20.0
USER_AGENT = "Strategeia/1.0 (personal paper-trading research; contact via repository)"
# Daily series change once a day; 6 hours is plenty fresh and keeps repeat page views off FRED.
FRED_TTL_SECONDS = 6 * 3600
# Series ids are short upper-case/digit strings. Validated because the id goes into a URL.
_SERIES_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_DATE_HEADERS = {"observation_date", "date"}

# (url, params) -> (status_code, text). Injectable so tests never touch the network.
HttpGet = Callable[[str, dict[str, str]], tuple[int, str]]


def _default_http_get(url: str, params: dict[str, str]) -> tuple[int, str]:
    try:
        response = httpx.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise DataProviderError(f"fred request failed: {type(exc).__name__}") from exc
    return response.status_code, response.text


def parse_fred_csv(text: str, series_id: str) -> list[tuple[date, float]]:
    """Points from a FRED CSV body; raises DataProviderError when it isn't one."""
    rows = list(csv.reader(io.StringIO(text.strip())))
    if not rows or len(rows[0]) < 2 or rows[0][0].strip().lower() not in _DATE_HEADERS:
        raise DataProviderError(f"fred returned something other than a CSV for {series_id}")
    points: list[tuple[date, float]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        raw_date, raw_value = row[0].strip(), row[1].strip()
        # "." is FRED's marker for no observation; blank is the same thing.
        if not raw_date or raw_value in (".", ""):
            continue
        try:
            day = datetime.strptime(raw_date, "%Y-%m-%d").date()
            value = float(raw_value)
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        points.append((day, value))
    points.sort(key=lambda p: p[0])
    return points


class FredProvider:
    name = "fred"

    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    @cached(FRED_TTL_SECONDS)
    def get_series(self, series_id: str, start: date | None = None, end: date | None = None) -> MacroSeries:
        """One FRED series between `start` and `end` (both optional, inclusive).
        The catalogue's friendly name and unit are filled in by
        `app.data_providers.macro.get_macro_series`."""
        if not _SERIES_ID.match(series_id or ""):
            raise DataProviderError(f"not a valid FRED series id: {series_id!r}")
        series_id = series_id.upper()
        params = {"id": series_id}
        if start is not None:
            params["cosd"] = start.isoformat()
        if end is not None:
            params["coed"] = end.isoformat()
        with health.track("fred", "get_series"):
            status, text = self._http_get(FRED_CSV_URL, params)
            if status == 404:
                raise DataProviderError(f"fred has no series {series_id}")
            if status != 200:
                raise DataProviderError(f"fred returned HTTP {status} for {series_id}")
            points = parse_fred_csv(text, series_id)
            if not points:
                raise DataProviderError(f"fred returned no observations for {series_id} in the requested range")
        return MacroSeries(
            series_id=series_id,
            name=series_id,
            unit="",
            source="fred",
            frequency="",
            points=points,
            last_updated=utcnow_naive(),
        )

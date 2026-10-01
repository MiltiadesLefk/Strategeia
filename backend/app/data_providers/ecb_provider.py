# Copyright (c) 2026 OpenTerminal contributors (MIT License; full text in THIRD_PARTY_NOTICES.md)
# Adapted from ErTasselli/OpenTerminal@95618ee server/src/providers/ecb.ts;
# changes: ported TypeScript -> Python, reads the CSV with a real parser (the
# ECB's text columns contain commas inside quotes, which a split on "," breaks),
# keeps its look-up of TIME_PERIOD/OBS_VALUE by header name, understands monthly
# and quarterly period labels, takes a start date, validates the flow and key,
# and is cached with a stale-on-error fallback.
"""ECB Data Portal (SDMX REST) series, with no API key.

    https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata&lastNObservations=3

Every dataflow has its own column layout, so the date and value are found by
header name (TIME_PERIOD, OBS_VALUE), never by position. Rows with an empty
value (a provisional or missing observation) are skipped: a blank is not a
zero. The TIME_PERIOD label depends on the frequency: a day ("2026-10-01"), a
month ("2026-09", read as the 1st) or a quarter ("2026-Q3", read as the 1st
day of the quarter).
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

ECB_DATA_URL = "https://data-api.ecb.europa.eu/service/data"
REQUEST_TIMEOUT_SECONDS = 25.0
USER_AGENT = "Strategeia/1.0 (personal paper-trading research; contact via repository)"
# Reference rates post once a day and policy rates change a few times a year:
# 6 hours is fresh enough for both.
ECB_TTL_SECONDS = 6 * 3600
# Without a start date, ask for this many of the newest observations (about two
# years of daily rates; far more than the whole history of a policy-rate series).
DEFAULT_LAST_OBSERVATIONS = 520
_FLOW = re.compile(r"^[A-Za-z0-9_]{1,20}$")
_KEY = re.compile(r"^[A-Za-z0-9_.+]{1,80}$")

# (url, params) -> (status_code, text). Injectable so tests never touch the network.
HttpGet = Callable[[str, dict[str, str]], tuple[int, str]]


def _default_http_get(url: str, params: dict[str, str]) -> tuple[int, str]:
    try:
        response = httpx.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise DataProviderError(f"ecb request failed: {type(exc).__name__}") from exc
    return response.status_code, response.text


def parse_period(label: str) -> date | None:
    """The first day of an ECB TIME_PERIOD label, or None if it isn't one."""
    label = (label or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
            return datetime.strptime(label, "%Y-%m-%d").date()
        if re.fullmatch(r"\d{4}-\d{2}", label):
            return datetime.strptime(label, "%Y-%m").date()
        quarter = re.fullmatch(r"(\d{4})-Q([1-4])", label)
        if quarter:
            return date(int(quarter.group(1)), (int(quarter.group(2)) - 1) * 3 + 1, 1)
    except ValueError:
        return None
    return None


def parse_ecb_csv(text: str, description: str) -> list[tuple[date, float]]:
    """Points from an ECB `csvdata` body; raises DataProviderError when the
    body has no TIME_PERIOD/OBS_VALUE columns (an error page, a changed format)."""
    rows = list(csv.reader(io.StringIO(text.strip())))
    if not rows:
        raise DataProviderError(f"ecb returned an empty body for {description}")
    header = [h.strip() for h in rows[0]]
    if "TIME_PERIOD" not in header or "OBS_VALUE" not in header:
        raise DataProviderError(f"ecb returned something other than series CSV for {description}")
    time_idx, value_idx = header.index("TIME_PERIOD"), header.index("OBS_VALUE")
    points: dict[date, float] = {}
    for row in rows[1:]:
        if len(row) <= max(time_idx, value_idx):
            continue
        day = parse_period(row[time_idx])
        raw = row[value_idx].strip()
        # A missing observation is never a zero.
        if day is None or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value):
            points[day] = value
    return sorted(points.items())


class EcbProvider:
    name = "ecb"

    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    @cached(ECB_TTL_SECONDS)
    def get_series(self, flow: str, key: str, start: date | None = None) -> MacroSeries:
        """One ECB series: `flow` is the dataflow ("EXR"), `key` the series key
        within it ("D.USD.EUR.SP00.A"). With `start` the series begins at that
        date; without it, the newest DEFAULT_LAST_OBSERVATIONS points."""
        if not _FLOW.match(flow or "") or not _KEY.match(key or ""):
            raise DataProviderError(f"not a valid ECB series reference: {flow!r}/{key!r}")
        params = {"format": "csvdata"}
        if start is not None:
            params["startPeriod"] = start.isoformat()
        else:
            params["lastNObservations"] = str(DEFAULT_LAST_OBSERVATIONS)
        description = f"{flow}/{key}"
        with health.track("ecb", "get_series"):
            status, text = self._http_get(f"{ECB_DATA_URL}/{flow}/{key}", params)
            if status == 404:
                # The portal answers 404 when the key matches no series OR the range has no data.
                raise DataProviderError(f"ecb has no data for {description} in the requested range")
            if status != 200:
                raise DataProviderError(f"ecb returned HTTP {status} for {description}")
            points = parse_ecb_csv(text, description)
            if not points:
                raise DataProviderError(f"ecb returned no observations for {description}")
        return MacroSeries(
            series_id=description,
            name=description,
            unit="",
            source="ecb",
            frequency="",
            points=points,
            last_updated=utcnow_naive(),
        )

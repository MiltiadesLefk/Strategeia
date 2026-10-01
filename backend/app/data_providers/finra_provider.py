# Copyright (c) 2026 OpenTerminal contributors (MIT License; full text in THIRD_PARTY_NOTICES.md)
# Adapted from ErTasselli/OpenTerminal@95618eed server/src/providers/finra.ts;
# changes: ported TypeScript -> Python, parses with a header/trailer-tolerant
# reader, looks up several symbols, walks a date range, keeps each day's file
# on disk (published files never change), and treats "no file" as a normal
# answer for weekends and holidays.
"""FINRA Reg SHO daily short-sale volume files.

FINRA publishes one pipe-delimited file per trading day covering every
US-listed symbol, free and without a key:

    https://cdn.finra.org/equity/regsho/daily/CNMSshvol20260930.txt
    Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

This is short-sale VOLUME (how much of the day's trading was a short sale),
not short INTEREST (how many shares are currently sold short). Market makers
sell short constantly as part of providing liquidity, so a high ratio is a
weak, noisy hint and never proof of bearish positioning.

Not part of the composite provider chain: it is not per-symbol market data, it
feeds a dated-facts stream (see app/signals/finra.py). Missing days (weekends,
holidays, a file not yet posted) answer 403 or 404 from the CDN and come back
here as `None`; any other failure raises `DataProviderError`. Nothing is ever
guessed.
"""

from __future__ import annotations

import gzip
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.data_providers import health
from app.data_providers.base import DataProviderError

logger = logging.getLogger(__name__)

FINRA_DAILY_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"
REQUEST_TIMEOUT_SECONDS = 30.0
# Be polite to a free public CDN: a small pause between uncached downloads.
DOWNLOAD_PACING_SECONDS = 0.5
# A missing file for a day older than this is permanent (weekend/holiday), so
# the miss is remembered on disk. Younger misses may just be "not posted yet".
PERMANENT_MISS_AFTER_DAYS = 10
# The name FINRA downloads are listed under in the Data sources health.
HEALTH_NAME = "finra"
USER_AGENT = "Strategeia/1.0 (personal paper-trading research; contact via repository)"

# (status_code, text) for a URL. Injectable so tests never touch the network.
HttpGet = Callable[[str], tuple[int, str]]


@dataclass(frozen=True)
class ShortVolumeRow:
    """One symbol's short-sale volume for one trading day."""

    symbol: str
    trade_date: date
    short_volume: float
    short_exempt_volume: float
    total_volume: float

    @property
    def ratio(self) -> float | None:
        """Short volume / total volume, or None when total is not positive."""
        if self.total_volume <= 0:
            return None
        return self.short_volume / self.total_volume


def _parse_float(text: str) -> float | None:
    try:
        value = float(text.strip())
    except ValueError:
        return None
    # nan/inf parse as floats but are never real volumes.
    return value if value == value and abs(value) != float("inf") else None


def parse_short_volume_file(text: str) -> dict[str, ShortVolumeRow]:
    """Parse one day's file into {symbol: row}.

    Tolerant on purpose: the header line, blank lines, a trailer (some days end
    with a record-count line), short or non-numeric rows and negative volumes
    are skipped rather than failing the whole file. A symbol listed twice keeps
    its first row.
    """
    rows: dict[str, ShortVolumeRow] = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 5:
            continue
        date_text, symbol = parts[0].strip(), parts[1].strip().upper()
        if not symbol or len(date_text) != 8 or not date_text.isdigit():
            continue  # the header ("Date|Symbol|...") and trailer lines land here
        try:
            trade_date = date(int(date_text[:4]), int(date_text[4:6]), int(date_text[6:]))
        except ValueError:
            continue
        short_volume, exempt, total = _parse_float(parts[2]), _parse_float(parts[3]), _parse_float(parts[4])
        if short_volume is None or total is None or short_volume < 0 or total < 0:
            continue
        rows.setdefault(
            symbol,
            ShortVolumeRow(symbol, trade_date, short_volume, exempt if exempt is not None and exempt >= 0 else 0.0, total),
        )
    return rows


def _default_http_get(url: str) -> tuple[int, str]:
    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise DataProviderError(f"FINRA request failed: {exc}") from exc
    return response.status_code, response.text


def default_cache_dir() -> Path:
    from app.config import get_infra_settings

    return get_infra_settings().db_file.parent / "finra"


class FinraProvider:
    """Fetches (and keeps on disk) FINRA's daily short-volume files."""

    name = "finra"

    def __init__(
        self,
        cache_dir: Path | None = None,
        http_get: HttpGet | None = None,
        pacing_seconds: float = DOWNLOAD_PACING_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cache_dir = cache_dir
        self._http_get = http_get or _default_http_get
        self._pacing = pacing_seconds
        self._sleep = sleep
        self._downloaded_once = False

    @property
    def cache_dir(self) -> Path:
        if self._cache_dir is None:
            self._cache_dir = default_cache_dir()
        return self._cache_dir

    def url_for(self, day: date) -> str:
        return FINRA_DAILY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))

    def _file_path(self, day: date) -> Path:
        return self.cache_dir / f"CNMSshvol{day.strftime('%Y%m%d')}.txt.gz"

    def _miss_path(self, day: date) -> Path:
        return self.cache_dir / f"CNMSshvol{day.strftime('%Y%m%d')}.none"

    def fetch_day(self, day: date, today: date | None = None) -> dict[str, ShortVolumeRow] | None:
        """The whole day's {symbol: row}, or None when FINRA has no file for it.

        A downloaded file is stored on disk and never fetched again: a posted
        file does not change. A miss for a day older than PERMANENT_MISS_AFTER_DAYS
        is remembered too (a weekend or holiday stays a gap forever).
        """
        path = self._file_path(day)
        if path.exists():
            try:
                return parse_short_volume_file(gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace"))
            except (OSError, EOFError) as exc:
                logger.warning("FINRA cache file %s unreadable (%s); fetching again", path.name, exc)
        if self._miss_path(day).exists():
            return None

        if self._downloaded_once and self._pacing > 0:
            self._sleep(self._pacing)
        self._downloaded_once = True
        with health.track(HEALTH_NAME, "download"):
            status, text = self._http_get(self.url_for(day))
            # A day with no file (weekend, holiday) is a normal answer; only a
            # server-side failure counts against the source.
            if status >= 500 or status in (408, 429):
                raise DataProviderError(f"FINRA returned HTTP {status} for {day.isoformat()}")
        today = today or date.today()
        if status in (403, 404):
            if (today - day).days > PERMANENT_MISS_AFTER_DAYS:
                self._write(self._miss_path(day), b"")
            return None
        if status != 200:
            raise DataProviderError(f"FINRA returned HTTP {status} for {day.isoformat()}")
        rows = parse_short_volume_file(text)
        if not rows:
            # A 200 with nothing parseable is a broken download, not a quiet day: keep it out of the cache.
            raise DataProviderError(f"FINRA file for {day.isoformat()} had no readable rows")
        self._write(path, gzip.compress(text.encode("utf-8")))
        return rows

    def _write(self, path: Path, data: bytes) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:  # a read-only disk costs a re-download, not the run
            logger.warning("could not cache FINRA file %s: %s", path.name, exc)

    def get_short_volume(self, symbols: Iterable[str], day: date) -> dict[str, ShortVolumeRow] | None:
        """Rows for the requested symbols on `day` (None if there is no file).
        A requested symbol the file does not list is simply absent."""
        file_rows = self.fetch_day(day)
        if file_rows is None:
            return None
        wanted = {s.strip().upper() for s in symbols if s and s.strip()}
        return {s: file_rows[s] for s in wanted if s in file_rows}


def weekdays_between(start: date, end: date) -> list[date]:
    """Mon-Fri dates from start to end inclusive (holidays are found by FINRA having no file)."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days

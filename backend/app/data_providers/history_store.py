"""A local store of daily price history: download once, keep on disk.

A backtest needs years of daily bars for hundreds of symbols, and the same SPY
and ^VIX history for every one of them. Fetching that per run (or per scan, as
the live path does) would be thousands of requests and an invitation to be
rate-limited; fetching it ONCE per symbol, then only the few new bars each
later day, is about one request per symbol.

    store = get_history_store()
    bars = store.get_daily_history("AAPL", start=date(2018, 1, 1), end=date(2023, 12, 29))
    store.ensure_history(["SPY", "^VIX", "AAPL"], years=10)      # bulk preload
    store.coverage("AAPL")                                      # what we hold

The file is runtime/history.db, separate from the trading database and from the
disposable provider cache: it is data you chose to keep, so clearing the cache
does not touch it.

Rules that make the numbers trustworthy:

  * **Never fabricated.** Only bars a provider returned are stored. A missing day
    stays missing: nothing is forward-filled, interpolated or guessed. A bar with
    a blank open/high/low/close is dropped, not repaired.
  * **Only final bars.** A day's bar is stored once it has stopped changing
    (markets.is_daily_bar_final). The bar a provider returns mid-session is a
    snapshot; keeping it would freeze a half-formed candle into every later run.
  * **A failed fetch changes nothing.** What is on disk stays; the failure is
    reported in the FillReport and the data already held is returned.
  * **No look-ahead.** `end` bounds what comes back, and inside a simulated
    `with as_of(t):` block it is clamped to the last bar final at `t` whether or
    not the caller passed it. A backtest must still pass end=<the simulated day>
    explicitly: the clamp is a backstop, not the interface.
  * **No silent splices.** yfinance bars are adjusted for splits AND dividends as
    of the day they were fetched, so after a dividend every older adjusted close
    shifts a little. Tacking tomorrow's bars onto last month's would leave a seam.
    Every incremental fetch therefore overlaps the stored bars by about a week and
    compares closes; if they disagree the whole history is refetched, not spliced.
    Bars from a different provider than the stored ones (say Nasdaq answering while
    Yahoo is down) are not spliced either: it is reported and the stored data is
    kept. `refresh="force"` replaces everything from whatever provider answers.

The live scan and exit paths do not use this store. They decide trades from fresh
provider data (and fresh_data_only()); this store exists for research that runs
over the past.

Extension point: every table carries an `interval` column and the public methods
take `interval`. Only "1d" is supported today; intraday bars are a later addition
and slot in under another interval value without a schema change.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal

import pandas as pd

from app.markets import (
    POST_CLOSE_GRACE,
    is_always_on,
    is_daily_bar_final,
    is_us_trading_day,
    to_market_time,
    us_session_bounds,
)

logger = logging.getLogger(__name__)

DAILY = "1d"
SUPPORTED_INTERVALS = (DAILY,)

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# How far back a first fill goes when the caller doesn't say.
DEFAULT_HISTORY_YEARS = 10

# Provider period strings and the calendar days each one reliably covers
# (generous, like the other providers' mappings: asking for a little more than
# needed costs nothing, asking for less leaves a hole). "max" is the fallback for
# anything older than ten years. yfinance accepts all of these.
PERIOD_CALENDAR_DAYS: list[tuple[str, int]] = [
    ("5d", 7),
    ("1mo", 31),
    ("3mo", 92),
    ("6mo", 184),
    ("1y", 366),
    ("2y", 731),
    ("5y", 1827),
    ("10y", 3660),
]
MAX_PERIOD = "max"

# An incremental fetch reaches this far back into bars we already hold, so the
# two can be compared before they are joined (see "No silent splices").
OVERLAP_DAYS = 7
# Extra calendar days on top of the span needed, for weekends and holidays at
# the edges of the period.
PERIOD_SAFETY_DAYS = 3
# Bars older than this many days (before "now") are final by definition, so the
# per-bar session-calendar check only runs on the newest ones.
RECENT_BAR_DAYS = 5
# Largest relative difference between a stored close and the same day's freshly
# fetched close that still counts as "the same series". Two fetches on the same
# adjustment basis agree to float noise (~1e-12); the smallest dividend that
# re-bases history moves older closes by roughly 1e-3. This sits between.
SEAM_TOLERANCE = 1e-4

# Polite pacing between network fetches in a bulk preload. Yahoo tolerates far
# more, but this is a one-off run nobody is waiting on, and being a good
# neighbour is cheaper than being blocked.
DEFAULT_PACE_SECONDS = 0.75

# Sources whose OHLC come back adjusted for splits and dividends. Recorded on
# every bar so a later reader knows what it is looking at.
ADJUSTED_SOURCES = frozenset({"yfinance"})

Refresh = Literal["auto", "never", "force"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bar (
    symbol   TEXT NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    date     TEXT NOT NULL,
    open     REAL NOT NULL,
    high     REAL NOT NULL,
    low      REAL NOT NULL,
    close    REAL NOT NULL,
    volume   REAL,
    adjusted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, interval, date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS history_meta (
    symbol       TEXT NOT NULL,
    interval     TEXT NOT NULL DEFAULT '1d',
    first_date   TEXT NOT NULL,
    last_date    TEXT NOT NULL,
    covered_from TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    last_checked TEXT NOT NULL,
    source       TEXT NOT NULL,
    adjusted     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, interval)
) WITHOUT ROWID;
"""


class HistoryUnavailableError(Exception):
    """Nothing is stored for the symbol and a fetch could not supply any."""


@dataclass
class FillReport:
    """What one fill did. `status` is one of:
    up_to_date (nothing needed fetching), filled (first download), extended (new
    bars joined to what was held), rebuilt (history was re-based, so refetched in
    full), skipped_source_changed (a different provider answered; stored data kept),
    failed (fetch failed or returned nothing usable; stored data kept)."""

    symbol: str
    status: str
    bars_added: int = 0
    fetched: bool = False  # a network request was made
    source: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("up_to_date", "filled", "extended", "rebuilt")


@dataclass
class HistoryCoverage:
    symbol: str
    interval: str
    first_date: date
    last_date: date
    bar_count: int
    max_gap_days: int  # longest stretch between consecutive stored bars: spots holes
    covered_from: date  # earliest date we have asked a provider for (a later first_date = IPO)
    last_updated: datetime
    last_checked: datetime
    source: str
    adjusted: bool


@dataclass
class HistorySummary:
    path: str
    symbols: int
    bars: int
    first_date: date | None
    last_date: date | None
    file_bytes: int
    stalest: list[tuple[str, date]] = field(default_factory=list)  # symbols furthest behind, oldest last_date first


# --------------------------------------------------------------------------
# bar normalisation
# --------------------------------------------------------------------------


def normalize_bars(frame: pd.DataFrame, symbol: str = "", now: datetime | None = None) -> pd.DataFrame:
    """Provider output -> tz-naive calendar dates, finite OHLC, sorted, one row
    per day, final bars only. Never invents a row."""
    missing = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"bars are missing columns {missing}")
    out = frame[BAR_COLUMNS].copy()
    dates = pd.to_datetime(out["date"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)  # keep the wall-clock calendar date (New York midnight)
    out["date"] = dates.dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    # A half-formed bar is worse than a missing one.
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out = out.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    if symbol and not out.empty:
        # Only the newest few days can be unfinished; skip the calendar maths for
        # the thousands of old rows.
        reference = (now or datetime.now(timezone.utc)).date()
        recent = out["date"].dt.date >= reference - timedelta(days=RECENT_BAR_DAYS)
        final = out["date"].map(lambda ts: is_daily_bar_final(symbol, ts.date(), now)).where(recent, True)
        out = out[final.astype(bool)].reset_index(drop=True)
    return out


def period_for_days(days_needed: int) -> str:
    """The smallest provider period string that reaches back `days_needed` calendar days."""
    for period, days in PERIOD_CALENDAR_DAYS:
        if days >= days_needed:
            return period
    return MAX_PERIOD


def _to_date(value: date | datetime | pd.Timestamp | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _final_moment(symbol: str, day: date) -> datetime:
    """When `day`'s bar stopped changing (tz-aware)."""
    if is_always_on(symbol):
        return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    bounds = us_session_bounds(day)
    if bounds is None:
        return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return bounds.close + POST_CLOSE_GRACE


def latest_final_day(symbol: str, now: datetime | None = None) -> date:
    """The most recent calendar day whose daily bar is final at `now`."""
    now_utc = datetime.now(timezone.utc) if now is None else (now if now.tzinfo else now.replace(tzinfo=timezone.utc))
    day = to_market_time(now_utc).date()
    if is_always_on(symbol):
        day = now_utc.date()
    for _ in range(14):
        tradable = is_always_on(symbol) or is_us_trading_day(day)
        if tradable and is_daily_bar_final(symbol, day, now_utc):
            return day
        day -= timedelta(days=1)
    return day


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------


class HistoryStore:
    """See the module docstring.

    `provider` is anything with get_ohlcv(symbol, period, interval) (and,
    preferably, get_ohlcv_with_source, which the composite provider has), or a
    zero-argument callable returning one, built on first use so that reading what
    is already stored never needs a provider at all.
    """

    def __init__(
        self,
        path: str | Path,
        provider=None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._provider_source = provider
        self._provider = None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._db_lock = threading.RLock()
        self._fill_lock = threading.RLock()
        self._reports: dict[str, FillReport] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- plumbing ----------------------------------------------------------

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    @staticmethod
    def _check_interval(interval: str) -> None:
        if interval not in SUPPORTED_INTERVALS:
            raise NotImplementedError(
                f"the history store holds daily bars only for now (asked for interval={interval!r}); "
                "intraday bars are a later addition"
            )

    @staticmethod
    def _symbol(symbol: str) -> str:
        cleaned = symbol.strip().upper()
        if not cleaned:
            raise ValueError("symbol is empty")
        return cleaned

    def _get_provider(self):
        if self._provider is None:
            source = self._provider_source
            if source is None:
                from app.config import load_app_settings
                from app.data_providers.factory import get_data_provider

                source = get_data_provider(load_app_settings())
            elif callable(source) and not hasattr(source, "get_ohlcv"):
                source = source()
            self._provider = source
        return self._provider

    def _fetch(self, symbol: str, period: str) -> tuple[pd.DataFrame, str]:
        provider = self._get_provider()
        if hasattr(provider, "get_ohlcv_with_source"):
            return provider.get_ohlcv_with_source(symbol, period=period, interval=DAILY)
        return provider.get_ohlcv(symbol, period=period, interval=DAILY), getattr(provider, "name", "unknown")

    # ---- reading -----------------------------------------------------------

    def _meta(self, symbol: str, interval: str = DAILY) -> sqlite3.Row | tuple | None:
        with self._db_lock:
            return self._conn.execute(
                "SELECT first_date, last_date, covered_from, last_updated, last_checked, source, adjusted "
                "FROM history_meta WHERE symbol = ? AND interval = ?",
                (symbol, interval),
            ).fetchone()

    def _read(self, symbol: str, start: date | None, end: date | None, interval: str = DAILY) -> pd.DataFrame:
        sql = "SELECT date, open, high, low, close, volume FROM bar WHERE symbol = ? AND interval = ?"
        params: list = [symbol, interval]
        if start is not None:
            sql += " AND date >= ?"
            params.append(start.isoformat())
        if end is not None:
            sql += " AND date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY date"
        with self._db_lock:
            rows = self._conn.execute(sql, params).fetchall()
        frame = pd.DataFrame(rows, columns=BAR_COLUMNS)
        frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[us]")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = frame[column].astype("float64")
        return frame

    def last_report(self, symbol: str) -> FillReport | None:
        """What the most recent fill of `symbol` in this process did."""
        return self._reports.get(self._symbol(symbol))

    def _clamp_end(self, symbol: str, end: date | None) -> date | None:
        """Inside a simulated `with as_of(t):` the future is off limits whatever
        the caller asked for."""
        from app.knowledge.point_in_time import simulated_as_of

        simulated = simulated_as_of()
        if simulated is None:
            return end
        cap = latest_final_day(symbol, simulated.replace(tzinfo=timezone.utc))
        return cap if end is None else min(end, cap)

    def get_daily_history(
        self,
        symbol: str,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        *,
        refresh: Refresh = "auto",
        interval: str = DAILY,
    ) -> pd.DataFrame:
        """Daily bars for `symbol` with `start <= date <= end` (both inclusive,
        both optional), as columns date, open, high, low, close, volume. `date`
        is a tz-naive calendar date at midnight (not the tz-aware New York time
        yfinance emits).

        refresh="auto" (default) downloads what is missing first: the whole
        range on a first call, the newest bars when the tail is stale, an older
        stretch when `start` reaches before what is held. "never" reads the disk
        only (no provider is touched: deterministic and offline, what a backtest
        wants). "force" refetches and replaces everything.

        AS-OF RULE: this returns bars up to `end`, nothing later. A simulated run
        must pass end=<the simulated day>; a bar dated after it is the future.
        As a backstop, inside `with as_of(t):` the end is clamped to the last
        bar final at t.

        A failed fetch leaves the stored bars as they were and they are returned
        (see last_report()). If nothing is stored at all and the fetch failed,
        HistoryUnavailableError is raised: an empty frame would read as "no
        history" rather than "couldn't get any".
        """
        self._check_interval(interval)
        sym = self._symbol(symbol)
        start_d, end_d = _to_date(start), self._clamp_end(sym, _to_date(end))
        if refresh not in ("auto", "never", "force"):
            raise ValueError(f"refresh must be 'auto', 'never' or 'force', not {refresh!r}")
        if refresh != "never":
            report = self.fill(sym, start=start_d, end=end_d, force=(refresh == "force"))
            if not report.ok and self._meta(sym) is None:
                raise HistoryUnavailableError(f"no stored history for {sym} and the fetch failed: {report.error}")
        return self._read(sym, start_d, end_d)

    def coverage(self, symbol: str, interval: str = DAILY) -> HistoryCoverage | None:
        """What is held for `symbol`, or None if nothing is."""
        self._check_interval(interval)
        sym = self._symbol(symbol)
        meta = self._meta(sym, interval)
        if meta is None:
            return None
        first, last, covered_from, updated, checked, source, adjusted = meta
        with self._db_lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM bar WHERE symbol = ? AND interval = ?", (sym, interval)
            ).fetchone()[0]
            dates = [
                r[0]
                for r in self._conn.execute(
                    "SELECT date FROM bar WHERE symbol = ? AND interval = ? ORDER BY date", (sym, interval)
                )
            ]
        gaps = [
            (date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(dates, dates[1:])
        ]
        return HistoryCoverage(
            symbol=sym,
            interval=interval,
            first_date=date.fromisoformat(first),
            last_date=date.fromisoformat(last),
            bar_count=count,
            max_gap_days=max(gaps) if gaps else 0,
            covered_from=date.fromisoformat(covered_from),
            last_updated=datetime.fromisoformat(updated),
            last_checked=datetime.fromisoformat(checked),
            source=source,
            adjusted=bool(adjusted),
        )

    def summary(self, stalest: int = 5) -> HistorySummary:
        """Whole-store numbers for the operator's cache card."""
        with self._db_lock:
            symbols, first, last = self._conn.execute(
                "SELECT COUNT(*), MIN(first_date), MAX(last_date) FROM history_meta"
            ).fetchone()
            bars = self._conn.execute("SELECT COUNT(*) FROM bar").fetchone()[0]
            behind = self._conn.execute(
                "SELECT symbol, last_date FROM history_meta ORDER BY last_date ASC, symbol LIMIT ?", (stalest,)
            ).fetchall()
        file_bytes = 0
        for suffix in ("", "-wal"):
            try:
                file_bytes += Path(f"{self.path}{suffix}").stat().st_size
            except OSError:
                pass
        return HistorySummary(
            path=str(self.path),
            symbols=symbols,
            bars=bars,
            first_date=date.fromisoformat(first) if first else None,
            last_date=date.fromisoformat(last) if last else None,
            file_bytes=file_bytes,
            stalest=[(s, date.fromisoformat(d)) for s, d in behind],
        )

    def delete(self, symbol: str, interval: str = DAILY) -> None:
        sym = self._symbol(symbol)
        with self._db_lock:
            self._conn.execute("DELETE FROM bar WHERE symbol = ? AND interval = ?", (sym, interval))
            self._conn.execute("DELETE FROM history_meta WHERE symbol = ? AND interval = ?", (sym, interval))
            self._conn.commit()

    # ---- filling -----------------------------------------------------------

    def fill(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        force: bool = False,
        interval: str = DAILY,
    ) -> FillReport:
        """Bring the stored history of `symbol` up to what [start, end] needs and
        say what happened. Never raises for a provider problem."""
        self._check_interval(interval)
        sym = self._symbol(symbol)
        with self._fill_lock:
            try:
                report = self._fill(sym, start, end, force)
            except Exception as exc:  # a fill must not take a caller down
                logger.warning("History fill for %s failed: %s", sym, exc)
                report = FillReport(sym, "failed", error=f"{type(exc).__name__}: {exc}")
            self._reports[sym] = report
            return report

    def _fill(self, sym: str, start: date | None, end: date | None, force: bool) -> FillReport:
        now = self._now()
        meta = self._meta(sym)
        expected_last = latest_final_day(sym, now)
        today = to_market_time(now).date() if not is_always_on(sym) else now.date()
        start_wanted = start or (_to_date(meta[2]) if meta else today - timedelta(days=366 * DEFAULT_HISTORY_YEARS))

        if meta is None or force:
            return self._download_all(sym, start_wanted, now, today)

        first_date, last_date, covered_from = (date.fromisoformat(meta[0]), date.fromisoformat(meta[1]), date.fromisoformat(meta[2]))
        last_checked = datetime.fromisoformat(meta[4]).replace(tzinfo=timezone.utc)
        stored_source = meta[5]

        target = expected_last if end is None else min(end, expected_last)
        # Nothing newer can exist once we have looked after `target`'s bar became
        # final (a holiday, a delisting, a provider that simply stops).
        checked_since_final = last_checked >= _final_moment(sym, target)
        tail_needed = last_date < target and not checked_since_final
        head_needed = start is not None and start < covered_from
        if not tail_needed and not head_needed:
            return FillReport(sym, "up_to_date")

        reach_back = (today - (start_wanted if head_needed else last_date)).days + (0 if head_needed else OVERLAP_DAYS)
        period = period_for_days(reach_back + PERIOD_SAFETY_DAYS)
        try:
            raw, source = self._fetch(sym, period)
            fresh = normalize_bars(raw, sym, now)
        except Exception as exc:
            return FillReport(sym, "failed", fetched=True, error=f"{type(exc).__name__}: {exc}")
        if fresh.empty:
            return FillReport(sym, "failed", fetched=True, source=source, error="the provider returned no usable bars")
        if source != stored_source:
            return FillReport(
                sym,
                "skipped_source_changed",
                fetched=True,
                source=source,
                error=f"stored bars come from {stored_source}, this fetch from {source}; kept what is stored "
                "(refresh='force' replaces it)",
            )

        if not self._same_basis(sym, fresh):
            # History was re-based (a dividend or split since we stored it). Refetch it all.
            rebuild_from = min(first_date, start_wanted) if head_needed else first_date
            return self._download_all(sym, rebuild_from, now, today, status="rebuilt")

        added = self._upsert(sym, fresh, source, now, covered_from=min(covered_from, start_wanted if head_needed else covered_from))
        # Zero new bars still counts as a successful check (last_checked moved on):
        # a holiday, or a provider with nothing newer.
        return FillReport(sym, "extended" if added else "up_to_date", bars_added=added, fetched=True, source=source)

    def _same_basis(self, sym: str, fresh: pd.DataFrame) -> bool:
        """Do the freshly fetched closes agree with the stored closes on the days
        they share? No shared day at all means we can't tell, which is not safe."""
        lo, hi = fresh["date"].min().date(), fresh["date"].max().date()
        stored = self._read(sym, lo, hi)
        merged = stored.merge(fresh[["date", "close"]], on="date", suffixes=("_old", "_new"))
        if merged.empty:
            return False
        relative = (merged["close_new"] / merged["close_old"] - 1).abs()
        return bool((relative <= SEAM_TOLERANCE).all())

    def _download_all(
        self, sym: str, start_wanted: date, now: datetime, today: date, *, status: str = "filled"
    ) -> FillReport:
        """Fetch the whole span from `start_wanted` and REPLACE what is stored."""
        period = period_for_days((today - start_wanted).days + PERIOD_SAFETY_DAYS)
        try:
            raw, source = self._fetch(sym, period)
            fresh = normalize_bars(raw, sym, now)
        except Exception as exc:
            return FillReport(sym, "failed", fetched=True, error=f"{type(exc).__name__}: {exc}")
        if fresh.empty:
            return FillReport(sym, "failed", fetched=True, source=source, error="the provider returned no usable bars")
        first_bar = fresh["date"].min().date()
        covered_from = min(start_wanted, first_bar)
        added = self._upsert(sym, fresh, source, now, covered_from=covered_from, replace=True)
        return FillReport(sym, status, bars_added=added, fetched=True, source=source)

    def _upsert(
        self,
        sym: str,
        bars: pd.DataFrame,
        source: str,
        now: datetime,
        *,
        covered_from: date,
        replace: bool = False,
    ) -> int:
        """Write bars and refresh the metadata in ONE transaction. Returns how
        many dates were new."""
        adjusted = 1 if source in ADJUSTED_SOURCES else 0
        rows = [
            (sym, DAILY, r.date.date().isoformat(), r.open, r.high, r.low, r.close, None if pd.isna(r.volume) else r.volume, adjusted)
            for r in bars.itertuples(index=False)
        ]
        stamp = now.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        with self._db_lock:
            conn = self._conn
            try:
                if replace:
                    conn.execute("DELETE FROM bar WHERE symbol = ? AND interval = ?", (sym, DAILY))
                before = conn.execute(
                    "SELECT COUNT(*) FROM bar WHERE symbol = ? AND interval = ?", (sym, DAILY)
                ).fetchone()[0]
                conn.executemany(
                    "INSERT OR REPLACE INTO bar (symbol, interval, date, open, high, low, close, volume, adjusted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                after, first, last = conn.execute(
                    "SELECT COUNT(*), MIN(date), MAX(date) FROM bar WHERE symbol = ? AND interval = ?", (sym, DAILY)
                ).fetchone()
                conn.execute(
                    "INSERT INTO history_meta (symbol, interval, first_date, last_date, covered_from, last_updated, "
                    "last_checked, source, adjusted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(symbol, interval) DO UPDATE SET first_date=excluded.first_date, "
                    "last_date=excluded.last_date, covered_from=excluded.covered_from, "
                    "last_updated=excluded.last_updated, last_checked=excluded.last_checked, "
                    "source=excluded.source, adjusted=excluded.adjusted",
                    (sym, DAILY, first, last, covered_from.isoformat(), stamp, stamp, source, adjusted),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return after - before if not replace else after

    # ---- bulk --------------------------------------------------------------

    def ensure_history(
        self,
        symbols: Iterable[str],
        years: int = DEFAULT_HISTORY_YEARS,
        *,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        progress: Callable[[int, int, FillReport], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        force: bool = False,
    ) -> list[FillReport]:
        """Make sure each symbol has `years` of history and is current, fetching
        only what is missing. Waits `pace_seconds` after every symbol that hit the
        network. `progress(index, total, report)` is called after each symbol. A
        failure on one symbol is reported and the run moves on to the next.
        Re-running is safe and cheap: stored symbols that are current cost nothing."""
        wanted: list[str] = []
        for raw in symbols:
            sym = self._symbol(raw)
            if sym not in wanted:
                wanted.append(sym)
        today = self._now().date()
        start = today - timedelta(days=int(365.25 * years))
        reports: list[FillReport] = []
        for index, sym in enumerate(wanted, start=1):
            report = self.fill(sym, start=start, force=force)
            reports.append(report)
            if progress is not None:
                progress(index, len(wanted), report)
            if report.fetched and index < len(wanted):
                sleep(pace_seconds)
        return reports


# --------------------------------------------------------------------------
# the process-wide store
# --------------------------------------------------------------------------

_default_store: HistoryStore | None = None
_default_guard = threading.Lock()


def get_history_store() -> HistoryStore:
    """The store on runtime/history.db, built on first use."""
    global _default_store
    with _default_guard:
        if _default_store is None:
            # InfraSettings(), not get_infra_settings(): that one generates and
            # writes the auth secrets, which a price lookup shouldn't do.
            from app.config import InfraSettings

            _default_store = HistoryStore(InfraSettings().history_db_file)
        return _default_store


def configure_history_store(store: HistoryStore | None) -> None:
    """Replace the process-wide store (tests, scripts). None resets to the default."""
    global _default_store
    with _default_guard:
        if _default_store is not None and _default_store is not store:
            _default_store.close()
        _default_store = store


def peek_history_summary() -> HistorySummary:
    """The summary of the process-wide store WITHOUT creating its file: a status
    page asking about a store nobody has filled yet should see an empty one, not
    cause history.db to appear."""
    with _default_guard:
        store = _default_store
    if store is not None:
        return store.summary()
    from app.config import InfraSettings

    path = InfraSettings().history_db_file
    if not path.exists():
        return HistorySummary(path=str(path), symbols=0, bars=0, first_date=None, last_date=None, file_bytes=0)
    return get_history_store().summary()

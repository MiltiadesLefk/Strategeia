"""Screener: run a set of rules over the symbol list using the app's own cached data.

Read-only. It reads the same daily bars the Market Scan reads (so a recent scan
has already put them in the provider cache), at most `scan_cap` symbols per run
(60 by default), and only fetches company overview / financials when a rule, the
sort or a requested column actually uses a fundamentals field. The response says
how many symbols were read out of how many were available, so a result is never
mistaken for the whole market.

Idea from OpenTerminal's ScreenerWidget (MIT, ErTasselli/OpenTerminal): a
filter-builder over a table of stocks. That widget queries TradingView's scanner;
this one filters the app's own watchlist data, and the field catalogue, filter
engine and saved screens are new code (see screener_engine.py).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from app.analysis.scanner_scoring import score_symbol
from app.analysis.trend import analyze_chart
from app.data_providers.base import AllProvidersFailedError, DataProvider, DataProviderError
from app.data_providers.universe import UniverseEntry, default_sector_for, load_universe
from app.schemas.screener_schemas import (
    ScreenerFieldOut,
    ScreenerFieldsResponse,
    ScreenerRow,
    ScreenerRunRequest,
    ScreenerRunResponse,
    ScreenSpec,
)
from app.services import screener_engine as engine
from app.services.screener_engine import Criterion, FilterError

# Several symbols are read at once; the provider cache de-duplicates concurrent
# requests for the same key, so this only bounds the parallelism.
FETCH_WORKERS = 6

# Below this many bars the 20/50-day averages and RSI are not meaningful, so the
# chart-based fields are left blank rather than computed on a stub.
MIN_BARS_FOR_ANALYSIS = 55
# The volume ratio compares the last day with the average of this many days before it.
VOLUME_AVERAGE_DAYS = 20
# A "52-week" position needs close to a year of bars (same bar as the Market Terminal's
# new-high/new-low tallies), and is measured over at most a year of trading days.
MIN_BARS_FOR_52W = 200
TRADING_DAYS_PER_YEAR = 252
# A fractional change this small is not a real range (a flat line): no position is reported.
MIN_RANGE_FRACTION = 1e-9


def describe_fields() -> ScreenerFieldsResponse:
    return ScreenerFieldsResponse(
        fields=[
            ScreenerFieldOut(
                name=f.name,
                label=f.label,
                kind=f.kind,
                unit=f.unit,
                source=f.source,
                description=f.description,
                operators=list(engine.NUMBER_OPERATORS if f.kind == "number" else engine.TEXT_OPERATORS),
                costly=f.source in ("overview", "financials"),
            )
            for f in engine.FIELD_CATALOGUE
        ],
        max_filters=engine.MAX_FILTERS,
        max_symbols=200,
        default_scan_cap=ScreenerRunRequest.model_fields["scan_cap"].default,
    )


# ------------------------------------------------------------------ validation


def validate_spec(spec: ScreenSpec) -> tuple[list[Criterion], str | None, list[str]]:
    """Check a screen's rules, sort and columns. Raises FilterError with a message fit to show."""
    criteria = [engine.validate_criterion(f.field, f.op, f.value, f.value2) for f in spec.filters]
    sort_field = engine.validate_sort(spec.sort_field)
    columns = engine.validate_columns(spec.columns)
    return criteria, sort_field, columns


# ------------------------------------------------------------------ per-symbol values


def _last_bar_date(frame: pd.DataFrame) -> str | None:
    if "date" not in frame.columns or frame.empty:
        return None
    try:
        return pd.Timestamp(frame["date"].iloc[-1]).date().isoformat()
    except (ValueError, TypeError):
        return None


def compute_bar_fields(symbol: str, frame: pd.DataFrame) -> dict[str, float | str | None]:
    """Every price-derived field for one symbol, from its daily bars (ascending).
    A field that cannot be worked out is None."""
    values: dict[str, float | str | None] = {
        name: None
        for name in (
            "price", "change_pct", "volume_ratio", "trend", "momentum", "rsi14",
            "scanner_score", "signal", "pct_from_ema20", "week52_position_pct",
        )  # fmt: skip
    }
    closes = frame["close"].dropna().astype(float)
    if closes.empty:
        return values
    price = float(closes.iloc[-1])
    values["price"] = price
    change = None
    if len(closes) >= 2 and closes.iloc[-2] > 0:
        change = (price / float(closes.iloc[-2]) - 1.0) * 100.0
        values["change_pct"] = change

    volume_ratio = None
    if "volume" in frame.columns:
        volumes = frame["volume"].dropna().astype(float)
        if len(volumes) > VOLUME_AVERAGE_DAYS:
            average = float(volumes.iloc[-1 - VOLUME_AVERAGE_DAYS : -1].mean())
            if average > 0:
                volume_ratio = float(volumes.iloc[-1]) / average
    values["volume_ratio"] = volume_ratio

    if len(frame) >= MIN_BARS_FOR_ANALYSIS and change is not None:
        try:
            chart = analyze_chart(frame)
            # The scanner treats unknown volume as an ordinary day (ratio 1.0).
            scored = score_symbol(symbol, price, change, chart, volume_ratio if volume_ratio is not None else 1.0)
        except (ValueError, IndexError, KeyError, ZeroDivisionError):
            chart = scored = None
        if chart is not None and scored is not None:
            values.update(
                trend=chart.trend,
                momentum=chart.momentum,
                rsi14=chart.rsi14,
                scanner_score=float(scored.score),
                signal=scored.signal,
                pct_from_ema20=chart.pct_from_ema20 * 100.0,
            )

    if len(frame) >= MIN_BARS_FOR_52W:
        year = frame.tail(TRADING_DAYS_PER_YEAR)
        high = float(year["high"].max()) if "high" in year.columns else float(year["close"].max())
        low = float(year["low"].min()) if "low" in year.columns else float(year["close"].min())
        if high - low > max(abs(high), 1.0) * MIN_RANGE_FRACTION:
            values["week52_position_pct"] = min(100.0, max(0.0, (price - low) / (high - low) * 100.0))
    return values


def _fundamental_fields(provider: DataProvider, symbol: str, sources: set[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    if "overview" in sources:
        pe = cap = None
        try:
            overview = provider.get_company_overview(symbol)
            # A zero or negative figure means "no positive earnings / unknown", not a tiny P/E.
            pe = overview.pe_ratio if overview.pe_ratio and overview.pe_ratio > 0 else None
            cap = overview.market_cap if overview.market_cap and overview.market_cap > 0 else None
        except (AllProvidersFailedError, DataProviderError, NotImplementedError):
            pass
        out["pe_ratio"], out["market_cap"] = pe, cap
    if "financials" in sources:
        growth = None
        try:
            years = sorted(provider.get_financials(symbol).years, key=lambda y: y.year)
            if len(years) >= 2 and years[-2].revenue and years[-2].revenue > 0:
                growth = (years[-1].revenue - years[-2].revenue) / years[-2].revenue * 100.0
        except (AllProvidersFailedError, DataProviderError, NotImplementedError):
            pass
        out["revenue_growth_pct"] = growth
    return out


def _load_row(provider: DataProvider, entry: UniverseEntry, sources: set[str]) -> dict | None:
    """One row (symbol, name, as_of and every field value), or None when no
    provider could supply the symbol's price bars."""
    try:
        frame = provider.get_ohlcv(entry.symbol, period="1y", interval="1d")
    except (AllProvidersFailedError, DataProviderError, NotImplementedError):
        return None
    if frame is None or frame.empty or "close" not in frame.columns:
        return None
    row: dict = {"symbol": entry.symbol, "name": entry.name, "as_of": _last_bar_date(frame), "sector": entry.sector}
    row.update(compute_bar_fields(entry.symbol, frame))
    row.update(_fundamental_fields(provider, entry.symbol, sources & {"overview", "financials"}))
    return row


# ------------------------------------------------------------------ the run


def _entries_for(symbols: list[str] | None) -> list[UniverseEntry]:
    universe = load_universe()
    if symbols is None:
        return universe
    known = {e.symbol: e for e in universe}
    ordered = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    return [known.get(s) or UniverseEntry(s, s, default_sector_for(s)) for s in ordered]


def run_screen(provider: DataProvider, request: ScreenerRunRequest) -> ScreenerRunResponse:
    """Raises FilterError for an invalid rule, sort or column."""
    criteria, sort_field, columns = validate_spec(request)
    used = engine.fields_in_use(criteria, sort_field, columns)
    sources = engine.sources_needed(used)

    entries = _entries_for(request.symbols)
    considered = entries[: request.scan_cap]

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        loaded = list(pool.map(lambda e: _load_row(provider, e, sources), considered))
    rows = [r for r in loaded if r is not None]
    missing = [e.symbol for e, r in zip(considered, loaded) if r is None]

    matched, skipped = engine.apply_filters(rows, criteria)
    ordered = engine.sort_rows(matched, sort_field, request.sort_dir == "desc")[: request.limit]

    shown = list(dict.fromkeys([*engine.ALWAYS_FIELDS, *[n for n in engine.FIELDS if n in used]]))
    return ScreenerRunResponse(
        rows=[
            ScreenerRow(
                symbol=r["symbol"],
                name=r["name"],
                as_of=r["as_of"],
                values={name: r.get(name) for name in shown},
            )
            for r in ordered
        ],
        scanned=len(considered),
        universe_size=len(entries),
        matched=len(matched),
        skipped_missing_data=skipped,
        missing=missing,
        columns=shown,
        sort_field=sort_field,
        sort_dir=request.sort_dir,
    )


__all__ = ["FilterError", "compute_bar_fields", "describe_fields", "run_screen", "validate_spec"]

"""Market Terminal: a sector heatmap, a macro panel and a market recap.

Everything here is read-only and computed from the data provider's own
(cached) bars. Nothing is stored, and nothing is invented: a symbol or series
the providers cannot supply is listed as missing / "not available" rather than
filled in.

Idea (and the rough layout of the three widgets) from OpenTerminal
(MIT, ErTasselli/OpenTerminal) HeatmapWidget / MacroWidget / RecapWidget. Those
read TradingView's scanner; this version reads the app's own universe and data
providers instead, so no code is copied.
"""

from __future__ import annotations

import hashlib
import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

import pandas as pd

from app.analysis.market_confirmation import VIX_ELEVATED_THRESHOLD
from app.data_providers.base import AllProvidersFailedError, DataProvider, DataProviderError
from app.data_providers.universe import load_universe
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.schemas.terminal_schemas import (
    BreadthStats,
    HeatmapResponse,
    HeatmapSector,
    HeatmapTile,
    MacroResponse,
    MacroTile,
    Mover,
    RecapResponse,
    SectorEtfTile,
    SectorMove,
    YieldCurve,
    YieldPoint,
)

logger = logging.getLogger(__name__)

Window = Literal["1d", "5d", "1m"]

# How many symbols the heatmap and recap read by default. A 500-name universe
# would otherwise mean 500 fresh fetches the first time the page opens; the
# first 60 (the universe's own order, same as a scan) are almost always already
# in the provider cache from a scan. The UI says how many were sampled.
DEFAULT_SAMPLE_SIZE = 60
MAX_SAMPLE_SIZE = 200
# Several symbols are fetched at once; the provider cache de-duplicates
# concurrent requests for the same key, so this only bounds the parallelism.
FETCH_WORKERS = 6

# Trading-day lookbacks for the three change windows (a week and a month).
WINDOW_BARS: dict[str, int] = {"1d": 1, "5d": 5, "1m": 21}
# 52-week extremes need about a year of bars; below this the symbol is not
# counted in the new-high / new-low tallies (too little history to claim it).
MIN_BARS_FOR_52W = 200
TRADING_DAYS_PER_YEAR = 252
# A close within this fraction of the 52-week extreme counts as "at" it.
NEAR_EXTREME_FRACTION = 0.001

# Computed results are reused for this long, so repeated page loads and
# refetch-on-focus do not re-walk the data. Shorter than the provider's own
# 15-minute bar cache on purpose: this layer is only about CPU and request
# volume, not about hiding fresh bars.
RESULT_TTL_SECONDS = 5 * 60
# An AI paragraph costs a model call, so it is reused much longer and only
# ever requested explicitly.
AI_RECAP_TTL_SECONDS = 30 * 60

# Recap lists.
TOP_MOVERS = 5
SECTOR_LEADERS = 3
# A "flat" day: changes this small count as unchanged in the breadth tally.
FLAT_THRESHOLD_PCT = 0.05

SPARKLINE_POINTS = 30
# The macro panel shows about a month of daily history behind each tile.
MACRO_PERIOD = "3mo"

SECTOR_ETFS: tuple[tuple[str, str], ...] = (
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLV", "Health Care"),
    ("XLY", "Consumer Discretionary"),
    ("XLP", "Consumer Staples"),
    ("XLI", "Industrials"),
    ("XLB", "Materials"),
    ("XLU", "Utilities"),
    ("XLRE", "Real Estate"),
    ("XLC", "Communication Services"),
)

DATA_SOURCE_LABEL = "Market data providers (Yahoo Finance first)"


# ---------------------------------------------------------------- result cache

_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def reset_terminal_cache() -> None:
    """Forget every computed result (tests)."""
    with _cache_lock:
        _cache.clear()


def _cached_result(key: tuple, ttl: float, build: Callable[[], object]):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- per-symbol data


@dataclass
class SymbolMove:
    symbol: str
    name: str
    sector: str
    price: float
    change_1d: float
    change_5d: float | None
    change_1m: float | None
    market_cap: float | None
    at_52w_high: bool | None
    at_52w_low: bool | None
    as_of: str | None

    def change(self, window: str) -> float | None:
        return {"1d": self.change_1d, "5d": self.change_5d, "1m": self.change_1m}[window]


def pct_change(closes: list[float], bars_back: int) -> float | None:
    """Percent change from `bars_back` bars before the last close to the last
    close, or None when there is not that much history (never a guess)."""
    if len(closes) <= bars_back:
        return None
    base = closes[-1 - bars_back]
    if base <= 0:
        return None
    return (closes[-1] - base) / base * 100.0


def _closes(frame: pd.DataFrame) -> list[float]:
    return [float(v) for v in frame["close"].dropna().tolist()]


def _last_bar_date(frame: pd.DataFrame) -> str | None:
    if "date" not in frame.columns or frame.empty:
        return None
    try:
        return pd.Timestamp(frame["date"].iloc[-1]).date().isoformat()
    except (ValueError, TypeError):
        return None


def extremes_flags(closes: list[float]) -> tuple[bool | None, bool | None]:
    """(at 52-week high, at 52-week low) by closing prices, or (None, None)
    when there is too little history to say."""
    if len(closes) < MIN_BARS_FOR_52W:
        return None, None
    year = closes[-TRADING_DAYS_PER_YEAR:]
    high, low, last = max(year), min(year), closes[-1]
    return last >= high * (1 - NEAR_EXTREME_FRACTION), last <= low * (1 + NEAR_EXTREME_FRACTION)


def _load_symbol(provider: DataProvider, symbol: str, name: str, sector: str, want_cap: bool) -> SymbolMove | None:
    try:
        # Same call (period/interval) the scanner makes, so a recent scan has
        # already put these bars in the provider cache.
        frame = provider.get_ohlcv(symbol, period="1y", interval="1d")
    except (AllProvidersFailedError, DataProviderError, NotImplementedError):
        return None
    closes = _closes(frame)
    day = pct_change(closes, 1)
    if day is None:
        return None
    cap: float | None = None
    if want_cap:
        try:
            cap = provider.get_company_overview(symbol).market_cap
        except (AllProvidersFailedError, DataProviderError, NotImplementedError):
            cap = None
        if cap is not None and cap <= 0:
            cap = None
    high, low = extremes_flags(closes)
    return SymbolMove(
        symbol=symbol,
        name=name,
        sector=sector,
        price=closes[-1],
        change_1d=day,
        change_5d=pct_change(closes, WINDOW_BARS["5d"]),
        change_1m=pct_change(closes, WINDOW_BARS["1m"]),
        market_cap=cap,
        at_52w_high=high,
        at_52w_low=low,
        as_of=_last_bar_date(frame),
    )


def load_moves(provider: DataProvider, limit: int, want_cap: bool) -> tuple[list[SymbolMove], list[str], int]:
    """Per-symbol moves for the first `limit` symbols of the effective universe.
    Returns (moves, missing symbols, universe size)."""
    universe = load_universe()
    sample = universe[: max(1, min(limit, MAX_SAMPLE_SIZE))]

    def one(entry) -> SymbolMove | None:
        return _load_symbol(provider, entry.symbol, entry.name, entry.sector, want_cap)

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(one, sample))
    moves = [m for m in results if m is not None]
    missing = [e.symbol for e, m in zip(sample, results) if m is None]
    return moves, missing, len(universe)


# ---------------------------------------------------------------- heatmap


def tile_weights(moves: list[SymbolMove]) -> tuple[dict[str, float], Literal["market_cap", "equal"]]:
    """Tile areas. Market cap when it is known for at least half the tiles (a
    symbol without one gets the median of the known ones, so it is not
    invisible); otherwise every tile is equal, because a map where most
    squares are guesses would be decoration."""
    known = [m.market_cap for m in moves if m.market_cap]
    if moves and len(known) * 2 >= len(moves):
        fallback = statistics.median(known)
        return {m.symbol: float(m.market_cap or fallback) for m in moves}, "market_cap"
    return {m.symbol: 1.0 for m in moves}, "equal"


def build_heatmap_sectors(moves: list[SymbolMove], window: Window) -> tuple[list[HeatmapSector], str]:
    weights, basis = tile_weights([m for m in moves if m.change(window) is not None])
    by_sector: dict[str, list[SymbolMove]] = {}
    for m in moves:
        if m.change(window) is None:
            continue
        by_sector.setdefault(m.sector or "Unknown", []).append(m)
    sectors: list[HeatmapSector] = []
    for sector, members in by_sector.items():
        changes = [m.change(window) for m in members]
        sector_weight = sum(weights[m.symbol] for m in members)
        tiles = [
            HeatmapTile(
                symbol=m.symbol,
                name=m.name,
                change_pct=round(m.change(window), 3),
                market_cap=m.market_cap,
                weight=weights[m.symbol],
            )
            for m in sorted(members, key=lambda x: -weights[x.symbol])
        ]
        sectors.append(
            HeatmapSector(
                sector=sector,
                avg_change_pct=round(sum(changes) / len(changes), 3),
                weight=sector_weight,
                tiles=tiles,
            )
        )
    sectors.sort(key=lambda s: -s.weight)
    return sectors, basis


def _load_etf_tiles(provider: DataProvider, window: Window) -> list[SectorEtfTile]:
    def one(item: tuple[str, str]) -> SectorEtfTile:
        symbol, sector = item
        try:
            frame = provider.get_ohlcv(symbol, period="1y", interval="1d")
            change = pct_change(_closes(frame), WINDOW_BARS[window])
        except (AllProvidersFailedError, DataProviderError, NotImplementedError):
            change = None
        return SectorEtfTile(symbol=symbol, sector=sector, change_pct=None if change is None else round(change, 3))

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        return list(pool.map(one, SECTOR_ETFS))


def get_heatmap(provider: DataProvider, window: Window = "1d", limit: int = DEFAULT_SAMPLE_SIZE) -> HeatmapResponse:
    limit = max(1, min(limit, MAX_SAMPLE_SIZE))

    def build() -> HeatmapResponse:
        moves, missing, universe_size = load_moves(provider, limit, want_cap=True)
        sectors, basis = build_heatmap_sectors(moves, window)
        return HeatmapResponse(
            window=window,
            as_of=_utcnow(),
            sampled=len(moves) + len(missing),
            universe_size=universe_size,
            requested_limit=limit,
            weighting=basis,
            sectors=sectors,
            sector_etfs=_load_etf_tiles(provider, window),
            missing=missing,
        )

    return _cached_result(("heatmap", window, limit), RESULT_TTL_SECONDS, build)  # type: ignore[return-value]


# ---------------------------------------------------------------- macro panel

# (id, label, group, symbol, unit). Yields from Yahoo are already in percent.
MACRO_SERIES: tuple[tuple[str, str, str, str, str], ...] = (
    ("spx", "S&P 500", "index", "^GSPC", "index"),
    ("ndx", "Nasdaq Composite", "index", "^IXIC", "index"),
    ("dji", "Dow Jones", "index", "^DJI", "index"),
    ("rut", "Russell 2000", "index", "^RUT", "index"),
    ("vix", "VIX", "volatility", "^VIX", "index"),
    ("dxy", "US Dollar Index", "currency", "DX-Y.NYB", "index"),
    ("y3m", "3-month yield", "yield", "^IRX", "percent"),
    ("y5y", "5-year yield", "yield", "^FVX", "percent"),
    ("y10y", "10-year yield", "yield", "^TNX", "percent"),
    ("y30y", "30-year yield", "yield", "^TYX", "percent"),
)
# No clean free 2-year yield exists on the data chain (the only Yahoo proxy is
# a futures contract that does not track the Treasury yield), so it is shown as
# unavailable rather than approximated.
TWO_YEAR_NOTE = "A 2-year Treasury yield is not available from the free data sources."


def _macro_tile(provider: DataProvider, series: tuple[str, str, str, str, str]) -> MacroTile:
    tile_id, label, group, symbol, unit = series
    try:
        frame = provider.get_ohlcv(symbol, period=MACRO_PERIOD, interval="1d")
    except (AllProvidersFailedError, DataProviderError, NotImplementedError):
        return MacroTile(id=tile_id, label=label, group=group, symbol=symbol, unit=unit, available=False)
    closes = _closes(frame)
    if len(closes) < 2:
        return MacroTile(id=tile_id, label=label, group=group, symbol=symbol, unit=unit, available=False)
    change = closes[-1] - closes[-2]
    return MacroTile(
        id=tile_id,
        label=label,
        group=group,
        symbol=symbol,
        unit=unit,
        available=True,
        value=round(closes[-1], 4),
        change=round(change, 4),
        change_pct=round(change / closes[-2] * 100, 3) if closes[-2] else None,
        as_of=_last_bar_date(frame),
        source=DATA_SOURCE_LABEL,
        sparkline=[round(v, 4) for v in closes[-SPARKLINE_POINTS:]],
    )


def build_yield_curve(tiles: dict[str, MacroTile]) -> YieldCurve:
    def point(tile_id: str, label: str, months: int) -> YieldPoint:
        t = tiles.get(tile_id)
        return YieldPoint(label=label, months=months, value=t.value if t and t.available else None)

    points = [point("y3m", "3M", 3), YieldPoint(label="2Y", months=24, value=None), point("y5y", "5Y", 60), point("y10y", "10Y", 120), point("y30y", "30Y", 360)]
    value = {p.label: p.value for p in points}
    ten, three = value["10Y"], value["3M"]
    spread_10y_3m = round(ten - three, 3) if ten is not None and three is not None else None
    # Inverted = long rates below short rates. The 10y-3m spread is the pair
    # with data on this chain; None (not False) when it cannot be computed.
    inverted = None if spread_10y_3m is None else spread_10y_3m < 0
    return YieldCurve(
        points=points,
        spread_10y_3m=spread_10y_3m,
        spread_10y_2y=None,
        inverted=inverted,
        note=TWO_YEAR_NOTE,
    )


def vix_regime(vix: float | None) -> Literal["elevated", "calm"] | None:
    """The app's own VIX regime: elevated at/above the threshold the scorer
    already penalises setups for (so the panel and the scoring agree)."""
    if vix is None:
        return None
    return "elevated" if vix >= VIX_ELEVATED_THRESHOLD else "calm"


def get_macro(provider: DataProvider) -> MacroResponse:
    def build() -> MacroResponse:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            tiles = list(pool.map(lambda s: _macro_tile(provider, s), MACRO_SERIES))
        by_id = {t.id: t for t in tiles}
        vix = by_id["vix"]
        return MacroResponse(
            as_of=_utcnow(),
            tiles=tiles,
            yield_curve=build_yield_curve(by_id),
            vix_value=vix.value if vix.available else None,
            vix_regime=vix_regime(vix.value if vix.available else None),
            vix_threshold=VIX_ELEVATED_THRESHOLD,
        )

    return _cached_result(("macro",), RESULT_TTL_SECONDS, build)  # type: ignore[return-value]


# ---------------------------------------------------------------- recap


def compute_breadth(moves: list[SymbolMove]) -> BreadthStats:
    adv = sum(1 for m in moves if m.change_1d > FLAT_THRESHOLD_PCT)
    dec = sum(1 for m in moves if m.change_1d < -FLAT_THRESHOLD_PCT)
    flagged = [m for m in moves if m.at_52w_high is not None]
    return BreadthStats(
        advancers=adv,
        decliners=dec,
        unchanged=len(moves) - adv - dec,
        new_highs=sum(1 for m in flagged if m.at_52w_high),
        new_lows=sum(1 for m in flagged if m.at_52w_low),
        # How many symbols had enough history to be judged for highs/lows.
        high_low_judged=len(flagged),
        total=len(moves),
    )


def top_movers(moves: list[SymbolMove], n: int = TOP_MOVERS) -> tuple[list[Mover], list[Mover]]:
    ordered = sorted(moves, key=lambda m: m.change_1d)
    up = [m for m in reversed(ordered) if m.change_1d > 0][:n]
    down = [m for m in ordered if m.change_1d < 0][:n]

    def to(m: SymbolMove) -> Mover:
        return Mover(symbol=m.symbol, name=m.name, change_pct=round(m.change_1d, 3), price=round(m.price, 4))

    return [to(m) for m in up], [to(m) for m in down]


def sector_moves(moves: list[SymbolMove]) -> list[SectorMove]:
    groups: dict[str, list[float]] = {}
    for m in moves:
        groups.setdefault(m.sector or "Unknown", []).append(m.change_1d)
    out = [SectorMove(sector=s, avg_change_pct=round(sum(v) / len(v), 3), count=len(v)) for s, v in groups.items()]
    out.sort(key=lambda s: -s.avg_change_pct)
    return out


def _fmt(v: float) -> str:
    return f"{v:+.2f}%"


def rule_based_summary(breadth: BreadthStats, up: list[Mover], down: list[Mover], sectors: list[SectorMove], vix: MacroTile | None) -> str:
    """The recap as plain sentences built straight from the numbers."""
    if breadth.total == 0:
        return "No price data was available for the scanned symbols."
    parts = [f"Of {breadth.total} scanned symbols, {breadth.advancers} rose and {breadth.decliners} fell ({breadth.unchanged} flat)."]
    if breadth.high_low_judged:
        parts.append(f"{breadth.new_highs} closed at a 52-week high and {breadth.new_lows} at a 52-week low (of {breadth.high_low_judged} with a full year of history).")
    if sectors:
        lead, lag = sectors[0], sectors[-1]
        if lead.sector != lag.sector:
            parts.append(f"Strongest sector: {lead.sector} ({_fmt(lead.avg_change_pct)}); weakest: {lag.sector} ({_fmt(lag.avg_change_pct)}).")
    if up:
        parts.append(f"Top gainer: {up[0].symbol} ({_fmt(up[0].change_pct)}).")
    if down:
        parts.append(f"Top decliner: {down[0].symbol} ({_fmt(down[0].change_pct)}).")
    if vix is not None and vix.available and vix.value is not None:
        regime = vix_regime(vix.value)
        move = f", {_fmt(vix.change_pct)} on the day" if vix.change_pct is not None else ""
        parts.append(f"VIX is at {vix.value:.2f}{move} ({regime} against the app's {VIX_ELEVATED_THRESHOLD:g} threshold).")
    return " ".join(parts)


def recap_prompt(summary: str, up: list[Mover], down: list[Mover], sectors: list[SectorMove]) -> str:
    """Facts-only prompt: the numbers are listed, the model is told not to add any."""
    lines = [
        "You write a short end-of-day market recap for a personal trading dashboard.",
        "Use ONLY the facts below. Do not add any number, company, event, cause or forecast that is not listed. If a fact is missing, leave it out.",
        "Write one plain paragraph of at most 90 words. No advice, no recommendations.",
        "",
        f"Rule-based summary: {summary}",
        "Top gainers: " + (", ".join(f"{m.symbol} {_fmt(m.change_pct)}" for m in up) or "none"),
        "Top decliners: " + (", ".join(f"{m.symbol} {_fmt(m.change_pct)}" for m in down) or "none"),
        "Sectors (average 1-day change, from the scanned symbols): "
        + (", ".join(f"{s.sector} {_fmt(s.avg_change_pct)}" for s in sectors) or "none"),
    ]
    return "\n".join(lines)


def get_recap(
    provider: DataProvider,
    llm: LLMProvider | None = None,
    *,
    ai: bool = False,
    limit: int = DEFAULT_SAMPLE_SIZE,
) -> RecapResponse:
    """The rule-based recap, plus an AI paragraph only when `ai` is asked for
    AND an LLM provider is configured. The AI text is never part of the
    rule-based fields and is labelled as AI in the response."""
    limit = max(1, min(limit, MAX_SAMPLE_SIZE))

    def build_base() -> RecapResponse:
        moves, missing, universe_size = load_moves(provider, limit, want_cap=False)
        macro = get_macro(provider)
        vix = next((t for t in macro.tiles if t.id == "vix"), None)
        up, down = top_movers(moves)
        sectors = sector_moves(moves)
        breadth = compute_breadth(moves)
        etfs = _load_etf_tiles(provider, "1d")
        ranked_etfs = sorted([e for e in etfs if e.change_pct is not None], key=lambda e: -e.change_pct)  # type: ignore[arg-type,return-value]
        return RecapResponse(
            as_of=_utcnow(),
            sampled=len(moves) + len(missing),
            universe_size=universe_size,
            breadth=breadth,
            top_gainers=up,
            top_losers=down,
            sector_leaders=sectors[:SECTOR_LEADERS],
            sector_laggards=list(reversed(sectors[-SECTOR_LEADERS:])) if len(sectors) > 1 else [],
            etf_leaders=ranked_etfs[:SECTOR_LEADERS],
            etf_laggards=list(reversed(ranked_etfs[-SECTOR_LEADERS:])) if len(ranked_etfs) > 1 else [],
            vix_value=vix.value if vix and vix.available else None,
            vix_change_pct=vix.change_pct if vix and vix.available else None,
            vix_regime=vix_regime(vix.value if vix and vix.available else None),
            summary=rule_based_summary(breadth, up, down, sectors, vix),
            missing=missing,
        )

    base: RecapResponse = _cached_result(("recap", limit), RESULT_TTL_SECONDS, build_base)  # type: ignore[assignment]
    if not ai or llm is None or llm.name == "none" or not llm.is_configured():
        return base

    prompt = recap_prompt(base.summary, base.top_gainers, base.top_losers, base.sector_leaders + base.sector_laggards)
    key = ("recap-ai", hashlib.sha256(prompt.encode()).hexdigest())
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and now - hit[0] < AI_RECAP_TTL_SECONDS:
        text, provider_name = hit[1]  # type: ignore[misc]
    else:
        result = generate_with_fallback(llm, prompt, fallback_text="")
        text = result.text.strip() if result.provider != "none" and result.text else None
        provider_name = result.provider if text else None
        # A failed call is not remembered: the next request may succeed.
        if text:
            with _cache_lock:
                _cache[key] = (time.monotonic(), (text, provider_name))
    return base.model_copy(update={"ai_paragraph": text, "ai_provider": provider_name})

"""Run a screen preset (analysis/screen_presets.py) over the first N symbols of the watchlist.

Read-only and bounded. It uses the normal data provider, whose per-method cache means a symbol the
scanner or another page already loaded costs nothing, and it fetches only what the chosen preset's
criteria need (the growth screen never asks for insider filings). The run is capped at `limit`
symbols and says so in the response; the symbols beyond it were not screened, and the response
never implies they were. A failed fetch for one piece of data makes the criteria that need it
"cannot tell", not "failed".
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from app.analysis.screen_presets import (
    NEED_CHART,
    NEED_EARNINGS,
    NEED_FINANCIALS,
    NEED_INSIDER,
    NEED_OVERVIEW,
    NEED_QUOTE,
    PRESETS,
    Preset,
    SymbolData,
    SymbolEvaluation,
    evaluate,
)
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.markets import to_market_time
from app.schemas.screen_preset_schemas import (
    PresetCriterionResultSchema,
    PresetCriterionSchema,
    PresetMatchSchema,
    PresetRunResponse,
    PresetSchema,
    PresetUnavailableSchema,
)
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Symbols screened when the caller does not say, and the most it may ask for. A cold run fetches
# several things per symbol from a free provider, so the bound is what keeps one click from being
# a rate-limit event. The default is a little under half of the default 50-symbol watchlist.
PRESET_DEFAULT_LIMIT = 25
PRESET_MAX_LIMIT = 100
# A few symbols at once: enough to hide network latency, few enough to stay polite to free sources.
PRESET_WORKERS = 4


def preset_schema(preset: Preset) -> PresetSchema:
    return PresetSchema(
        name=preset.name,
        label=preset.label,
        description=preset.description,
        criteria=[PresetCriterionSchema(id=c.id, label=c.label, required=c.required) for c in preset.criteria],
        min_matches=preset.min_matches,
        unavailable=[PresetUnavailableSchema(label=u.label, reason=u.reason) for u in preset.unavailable],
    )


def list_presets() -> list[PresetSchema]:
    return [preset_schema(p) for p in PRESETS]


def load_symbol_data(symbol: str, needs: tuple[str, ...], data_provider: DataProvider, today: date) -> SymbolData:
    """Fetch what `needs` names for one symbol. Any single failure leaves that part empty."""
    data = SymbolData(symbol=symbol, today=today)

    def attempt(label: str, call):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - one missing source must not stop the screen
            logger.info("Screen preset: %s unavailable for %s: %s", label, symbol, exc)
            return None

    if NEED_OVERVIEW in needs:
        data.overview = attempt("overview", lambda: data_provider.get_company_overview(symbol))
    if NEED_FINANCIALS in needs:
        financials = attempt("financials", lambda: data_provider.get_financials(symbol))
        if financials is not None:
            data.financial_years = sorted(financials.years, key=lambda y: y.year)
    if NEED_CHART in needs:
        bars = attempt("price history", lambda: data_provider.get_ohlcv(symbol, period="1y", interval="1d"))
        if bars is not None and len(bars) > 0:
            data.chart = attempt("chart analysis", lambda: analyze_chart(bars))
    if NEED_QUOTE in needs:
        quote = attempt("quote", lambda: data_provider.get_quote(symbol))
        if quote is not None:
            data.price = quote.price
            data.change_pct_24h = quote.change_pct_24h
            data.volume_ratio = quote.volume / quote.avg_volume_20d if quote.avg_volume_20d > 0 else None
    if NEED_INSIDER in needs:
        data.insider = attempt("insider activity", lambda: data_provider.get_insider_activity(symbol))
    if NEED_EARNINGS in needs:
        data.earnings_date = attempt("earnings date", lambda: data_provider.get_earnings_date(symbol))
    return data


def run_preset(preset: Preset, symbols: list[str], data_provider: DataProvider, *, limit: int = PRESET_DEFAULT_LIMIT) -> PresetRunResponse:
    """Screen the first `limit` of `symbols` (the effective watchlist, in order) with `preset`."""
    limit = max(1, min(limit, PRESET_MAX_LIMIT))
    chosen = symbols[:limit]
    today = to_market_time(utcnow_naive()).date()
    needs = preset.needs

    def screen(symbol: str) -> tuple[SymbolData, SymbolEvaluation]:
        data = load_symbol_data(symbol, needs, data_provider, today)
        return data, evaluate(preset, data)

    with ThreadPoolExecutor(max_workers=PRESET_WORKERS) as pool:
        screened = list(pool.map(screen, chosen))

    matches = [
        PresetMatchSchema(
            symbol=data.symbol,
            price=data.price,
            change_pct_24h=data.change_pct_24h,
            criteria=[PresetCriterionResultSchema(id=r.id, label=r.label, ok=r.ok, detail=r.detail) for r in evaluation.results],
        )
        for data, evaluation in screened
        if evaluation.status == "match"
    ]
    unjudged = [evaluation.symbol for _, evaluation in screened if evaluation.status == "unjudged"]
    bounded = (
        f"Screened the first {len(chosen)} of {len(symbols)} watchlist symbols"
        + (f" (limit {limit}); the other {len(symbols) - len(chosen)} were not checked." if len(symbols) > len(chosen) else ".")
    )
    return PresetRunResponse(
        preset=preset_schema(preset),
        matches=matches,
        universe_size=len(symbols),
        limit=limit,
        checked=len(chosen),
        unjudged=unjudged,
        note=bounded,
    )

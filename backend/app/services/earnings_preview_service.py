"""Earnings preview for one symbol: facts first, optional AI paragraph second.

The facts (report date, estimates, how the stock reacted to past reports, options-
implied move, beat/miss record, price context, scenario rows, what to watch) are all
computed from our own data by `analysis/earnings_preview.py`. The AI paragraph only
narrates those facts and is skipped, with a rule-based paragraph in its place, when no
AI is configured or the call fails. Read-only: nothing is stored.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from threading import Lock

from app.analysis import earnings_preview as ep
from app.analysis.expected_move import compute_expected_move_pct, days_to_expiration
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.llm_providers.earnings_preview_prompt import build_earnings_preview_prompt
from app.llm_providers.factory import generate_with_fallback
from app.schemas.earnings_preview_schemas import (
    EarningsPreviewResponse,
    EstimateSchema,
    ImpliedMoveSchema,
    PriceContextSchema,
    ScenarioSchema,
    SurpriseRowSchema,
    TrackRecordSchema,
)
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Enough price history to see several past reports (the provider's earnings history
# goes back about three years; a 1-year fetch would only reach the last four).
PREVIEW_OHLCV_PERIOD = "5y"
# The finished preview, AI paragraph included, is kept this long so reopening the
# tab does not pay for another model call. The underlying data is cached by the
# providers on its own, longer clock.
PREVIEW_CACHE_TTL_SECONDS = 15 * 60
PREVIEW_CACHE_MAX_ENTRIES = 64

_cache: dict[tuple[str, str], tuple[float, EarningsPreviewResponse]] = {}
_cache_lock = Lock()


def reset_earnings_preview_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _try(gaps: list[str], label: str, call, default=None):
    """Run one data fetch; on any failure record a plain-words gap and carry on."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - any one missing section must not sink the preview
        logger.info("Earnings preview: %s unavailable: %s", label, exc)
        gaps.append(f"{label} unavailable")
        return default


def _rule_summary(symbol: str, r: EarningsPreviewResponse) -> str:
    """The deterministic paragraph, used when no AI is configured or it fails."""
    parts: list[str] = []
    if r.earnings_date and r.days_until is not None:
        when = "today" if r.days_until == 0 else f"in {r.days_until} day{'s' if r.days_until != 1 else ''}"
        parts.append(f"{symbol} reports {when} ({r.earnings_date}).")
        if r.estimate and r.estimate.eps_estimate is not None:
            parts.append(f"Consensus EPS is {r.estimate.eps_estimate:.2f}.")
    else:
        parts.append(f"No upcoming earnings date is available for {symbol}.")
    if r.historical_move_pct is not None:
        parts.append(
            f"It has moved a median {r.historical_move_pct:.1f}% around its last {r.reactions_sampled} reports."
        )
    if r.implied_move and r.implied_move.ratio is not None:
        parts.append(
            f"Options imply {r.implied_move.implied_move_pct:.1f}%, {r.implied_move.ratio:.1f}x that "
            f"({r.implied_move.verdict})."
        )
    if r.track_record and r.track_record.quarters:
        parts.append(f"It beat EPS estimates in {r.track_record.beats} of {r.track_record.quarters} quarters.")
    return " ".join(parts)


def _facts_for_prompt(r: EarningsPreviewResponse) -> list[str]:
    facts = [f"Company: {r.name or r.symbol} (ticker {r.symbol})"]
    if r.earnings_date:
        facts.append(f"Earnings date: {r.earnings_date} ({r.days_until} days away)")
    if r.estimate:
        if r.estimate.fiscal_period_label:
            facts.append(f"Fiscal period: {r.estimate.fiscal_period_label}")
        if r.estimate.eps_estimate is not None:
            facts.append(f"Consensus EPS estimate: {r.estimate.eps_estimate:.2f}")
        if r.estimate.revenue_estimate is not None:
            facts.append(f"Consensus revenue estimate: {r.estimate.revenue_estimate:,.0f}")
    if r.historical_move_pct is not None:
        facts.append(
            f"Median absolute move around the last {r.reactions_sampled} reports: {r.historical_move_pct:.2f}%"
        )
    if r.implied_move:
        covers = "includes" if r.implied_move.covers_earnings else "expires BEFORE"
        facts.append(
            f"Options-implied move to {r.implied_move.expiration}: {r.implied_move.implied_move_pct:.2f}% "
            f"(that expiration {covers} the report)"
        )
        if r.implied_move.ratio is not None:
            facts.append(
                f"Implied move is {r.implied_move.ratio:.2f}x the historical median ({r.implied_move.verdict})"
            )
    if r.track_record and r.track_record.quarters:
        t = r.track_record
        facts.append(f"EPS beat/miss record: {t.beats} beats, {t.misses} misses, {t.in_line} in line of {t.quarters}")
    if r.price_context:
        c = r.price_context
        facts.append(f"Price {c.price:.2f}; trend {c.trend}, momentum {c.momentum}, RSI {c.rsi14:.0f}")
    for line in r.what_to_watch:
        facts.append(f"What to watch: {line}")
    return facts


def build_earnings_preview(
    symbol: str,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    *,
    today: date | None = None,
) -> EarningsPreviewResponse:
    symbol = symbol.upper()
    today = today or date.today()
    cache_key = (symbol, f"{llm_provider.name}:{today.isoformat()}")
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and time.monotonic() - hit[0] < PREVIEW_CACHE_TTL_SECONDS:
            return hit[1]

    gaps: list[str] = []
    overview = _try(gaps, "company overview", lambda: data_provider.get_company_overview(symbol))
    earnings_date = _try(gaps, "earnings date", lambda: data_provider.get_earnings_date(symbol))
    estimate = _try(gaps, "analyst estimates", lambda: data_provider.get_earnings_estimate(symbol))
    history = _try(gaps, "earnings history", lambda: data_provider.get_earnings_history(symbol), default=[]) or []
    ohlcv = _try(gaps, "price history", lambda: data_provider.get_ohlcv(symbol, period=PREVIEW_OHLCV_PERIOD, interval="1d"))
    options = _try(gaps, "options data", lambda: data_provider.get_options_summary(symbol))

    days_until = (earnings_date - today).days if earnings_date else None

    chart = None
    if ohlcv is not None and len(ohlcv) >= 50:
        chart = _try(gaps, "chart analysis", lambda: analyze_chart(ohlcv))
    price_context = None
    if chart is not None:
        price_context = PriceContextSchema(
            price=chart.price,
            trend=chart.trend,
            momentum=chart.momentum,
            rsi14=round(chart.rsi14, 1),
            pct_from_ema20=round(chart.pct_from_ema20 * 100, 2),
            week52_low=overview.week52_low if overview else None,
            week52_high=overview.week52_high if overview else None,
        )

    reactions = ep.earnings_reactions(ohlcv, history) if ohlcv is not None else []
    median_move = ep.median_abs_move(reactions)
    scenarios = ep.build_scenarios(reactions)
    scenarios_note = None
    if not scenarios:
        scenarios_note = (
            f"Only {len(reactions)} past reaction(s) could be measured; at least "
            f"{ep.MIN_REACTIONS_FOR_SCENARIOS} are needed for a scenario table."
        )

    implied = None
    implied_covers = False
    verdict = None
    if options is not None and options.atm_implied_volatility is not None:
        days = days_to_expiration(options.expiration, today)
        move = compute_expected_move_pct(options.atm_implied_volatility, days) if days else None
        if move is not None:
            try:
                expiry = date.fromisoformat(options.expiration)
            except ValueError:
                expiry = None
            implied_covers = bool(earnings_date and expiry and expiry >= earnings_date)
            verdict = ep.implied_vs_history(move, median_move) if implied_covers else None
            implied = ImpliedMoveSchema(
                expiration=options.expiration,
                implied_move_pct=round(move, 2),
                covers_earnings=implied_covers,
                historical_median_move_pct=round(median_move, 2) if median_move is not None else None,
                ratio=verdict.ratio if verdict else None,
                verdict=verdict.verdict if verdict else None,
            )

    track = ep.surprise_track_record(history)
    reaction_by_date = {r.report_date: r.move_pct for r in reactions}
    table = [
        SurpriseRowSchema(
            report_date=e.date.isoformat(),
            eps_estimate=e.eps_estimate,
            eps_actual=e.eps_actual,
            surprise_pct=e.surprise_pct,
            reaction_pct=round(reaction_by_date[e.date], 2) if e.date in reaction_by_date else None,
        )
        for e in sorted(history, key=lambda h: h.date, reverse=True)[: ep.SURPRISE_TABLE_QUARTERS]
    ]

    latest_actual = next((e.eps_actual for e in sorted(history, key=lambda h: h.date, reverse=True) if e.eps_actual is not None), None)
    watch = ep.watch_points(
        days_until=days_until,
        track=track,
        verdict=verdict,
        implied_covers_earnings=implied_covers,
        rsi14=chart.rsi14 if chart else None,
        trend=chart.trend if chart else None,
        price=chart.price if chart else None,
        week52_high=overview.week52_high if overview else None,
        week52_low=overview.week52_low if overview else None,
        eps_estimate=estimate.eps_estimate if estimate else None,
        last_eps_actual=latest_actual,
    )

    response = EarningsPreviewResponse(
        symbol=symbol,
        name=overview.name if overview else None,
        earnings_date=earnings_date.isoformat() if earnings_date else None,
        days_until=days_until,
        estimate=(
            EstimateSchema(
                fiscal_period_label=estimate.fiscal_period_label,
                eps_estimate=estimate.eps_estimate,
                revenue_estimate=estimate.revenue_estimate,
            )
            if estimate
            else None
        ),
        price_context=price_context,
        historical_move_pct=round(median_move, 2) if median_move is not None else None,
        reactions_sampled=len(reactions),
        implied_move=implied,
        track_record=(
            TrackRecordSchema(
                quarters=track.quarters,
                beats=track.beats,
                misses=track.misses,
                in_line=track.in_line,
                average_surprise_pct=round(track.average_surprise_pct, 2) if track.average_surprise_pct is not None else None,
            )
            if track.quarters
            else None
        ),
        surprise_table=table,
        scenarios=[ScenarioSchema(**s.__dict__) for s in scenarios],
        scenarios_note=scenarios_note,
        what_to_watch=watch,
        data_gaps=gaps,
        summary="",
        summary_provider="none",
        generated_at=utcnow_naive().isoformat() + "Z",
    )

    fallback = _rule_summary(symbol, response)
    prompt = build_earnings_preview_prompt(symbol, _facts_for_prompt(response))
    result = generate_with_fallback(llm_provider, prompt, fallback)
    response.summary = result.text
    response.summary_provider = result.provider
    response.summary_error = result.error

    with _cache_lock:
        if len(_cache) >= PREVIEW_CACHE_MAX_ENTRIES:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = (time.monotonic(), response)
    return response

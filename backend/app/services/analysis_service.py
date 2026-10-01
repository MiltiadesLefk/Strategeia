from __future__ import annotations

import pandas as pd

from app.analysis.indicators import bollinger, ema, latest_atr, macd, rsi, session_vwap
from app.analysis.insight_text import chart_insight_text
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.llm_providers.prompts import build_chart_insight_prompt
from app.schemas.analysis_schemas import AnalysisResponse, CandleSchema, SeriesPoint

# Trading-day counts per daily-bar UI range tab. Indicators are always
# computed over a full 1y fetch regardless of the selected range (EMA50
# needs ~50+ bars to be meaningful) — the range only trims how much of that
# history is *displayed*. trade_plan_service/scanner_service/research_service
# all fetch the same 1y window for their own analyze_chart() calls, so a
# symbol's trend/EMA/RSI reads identically here and in a generated trade
# plan — they used to diverge (6mo there vs 1y here); see notes/Decisions.md.
RANGE_TO_DISPLAY_DAYS = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252}

# 1D/1W use real intraday bars instead — fetched and displayed in full, no
# separate lookback/display split needed at this granularity. yfinance is the
# only source used for them, on purpose: Stooq now blocks scripted requests,
# Finnhub's free tier has no candles, and Nasdaq's public chart serves only
# today's per-minute last-sale prices (no open/high/low, no volume, no earlier
# days), which cannot be turned into honest 5-minute or 15-minute candles. So when
# Yahoo is rate-limited these two ranges fail with a 502 and the UI says so and
# falls back to the daily view; no data is made up.
INTRADAY_PARAMS = {"1d": ("1d", "5m"), "1w": ("5d", "15m")}


def _date_str(value, intraday: bool) -> str:
    if intraday:
        return value.isoformat()
    return str(value.date()) if hasattr(value, "date") else str(value)


def get_analysis(
    symbol: str, data_provider: DataProvider, llm_provider: LLMProvider, range_: str = "3mo"
) -> AnalysisResponse:
    intraday = range_ in INTRADAY_PARAMS

    if intraday:
        period, interval = INTRADAY_PARAMS[range_]
        ohlcv = data_provider.get_ohlcv(symbol, period=period, interval=interval)
        view = ohlcv
    else:
        ohlcv = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
        display_days = RANGE_TO_DISPLAY_DAYS.get(range_, RANGE_TO_DISPLAY_DAYS["3mo"])
        view = ohlcv.tail(display_days)

    chart = analyze_chart(ohlcv)
    # Every indicator is computed on the FULL fetch and then sliced to the
    # displayed window — same rule the EMAs already followed. Computing them
    # on the trimmed view instead would silently shorten every lookback and
    # make a 1-month chart disagree with a 1-year one about the same bar.
    ema20_full = ema(ohlcv["close"], 20)
    ema50_full = ema(ohlcv["close"], 50)
    bands_full = bollinger(ohlcv["close"])
    macd_full = macd(ohlcv["close"])
    rsi_full = rsi(ohlcv["close"], 14)
    ema20_view = ema20_full.loc[view.index]
    ema50_view = ema50_full.loc[view.index]

    atr14 = latest_atr(ohlcv)
    # VWAP only where it means what it says: intraday. See indicators.session_vwap.
    vwap_view = session_vwap(view) if intraday else None

    fallback_text = chart_insight_text(symbol, chart)
    prompt = build_chart_insight_prompt(symbol, chart)
    llm_result = generate_with_fallback(llm_provider, prompt, fallback_text)

    candles = [
        CandleSchema(
            date=_date_str(row.date, intraday),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in view.itertuples()
    ]
    dates = [_date_str(d, intraday) for d in view["date"]]
    ema20_series = [SeriesPoint(date=d, value=float(v)) for d, v in zip(dates, ema20_view)]
    ema50_series = [SeriesPoint(date=d, value=float(v)) for d, v in zip(dates, ema50_view)]

    def _series(full_series) -> list[SeriesPoint]:
        """Slice a full-fetch series to the displayed window, dropping the
        leading NaNs every windowed indicator starts with (a 20-period band
        has no value for its first 19 bars) rather than plotting them as 0."""
        if full_series is None:
            return []
        sliced = full_series.loc[view.index]
        return [
            SeriesPoint(date=d, value=float(v))
            for d, v in zip(dates, sliced)
            if v is not None and not pd.isna(v)
        ]

    return AnalysisResponse(
        symbol=symbol,
        price=chart.price,
        ema20=chart.ema20,
        ema50=chart.ema50,
        rsi14=chart.rsi14,
        trend=chart.trend,
        momentum=chart.momentum,
        support=chart.support,
        resistance=chart.resistance,
        atr14=atr14,
        atr_pct=(atr14 / chart.price * 100) if atr14 and chart.price else None,
        macd=float(macd_full.macd.iloc[-1]) if not pd.isna(macd_full.macd.iloc[-1]) else None,
        macd_signal=float(macd_full.signal.iloc[-1]) if not pd.isna(macd_full.signal.iloc[-1]) else None,
        candles=candles,
        ema20_series=ema20_series,
        ema50_series=ema50_series,
        bollinger_upper_series=_series(bands_full.upper),
        bollinger_lower_series=_series(bands_full.lower),
        rsi_series=_series(rsi_full),
        macd_series=_series(macd_full.macd),
        macd_signal_series=_series(macd_full.signal),
        macd_histogram_series=_series(macd_full.histogram),
        vwap_series=(
            [SeriesPoint(date=d, value=float(v)) for d, v in zip(dates, vwap_view) if not pd.isna(v)]
            if vwap_view is not None
            else []
        ),
        insight_text=llm_result.text,
        ai_provider=llm_result.provider,
        ai_error=llm_result.error,
    )

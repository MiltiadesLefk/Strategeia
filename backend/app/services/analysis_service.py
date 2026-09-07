from __future__ import annotations

from app.analysis.indicators import ema
from app.analysis.insight_text import chart_insight_text
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.llm_providers.prompts import build_chart_insight_prompt
from app.schemas.analysis_schemas import AnalysisResponse, CandleSchema, SeriesPoint

# Trading-day counts per UI range tab. Indicators are always computed over a
# full 1y fetch regardless of the selected range (EMA50 needs ~50+ bars to be
# meaningful) — the range only trims how much of that history is *displayed*.
RANGE_TO_DISPLAY_DAYS = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252}


def _date_str(value) -> str:
    return str(value.date()) if hasattr(value, "date") else str(value)


def get_analysis(
    symbol: str, data_provider: DataProvider, llm_provider: LLMProvider, range_: str = "3mo"
) -> AnalysisResponse:
    ohlcv = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
    chart = analyze_chart(ohlcv)

    ema20_full = ema(ohlcv["close"], 20)
    ema50_full = ema(ohlcv["close"], 50)

    fallback_text = chart_insight_text(symbol, chart)
    prompt = build_chart_insight_prompt(symbol, chart)
    llm_result = generate_with_fallback(llm_provider, prompt, fallback_text)

    display_days = RANGE_TO_DISPLAY_DAYS.get(range_, RANGE_TO_DISPLAY_DAYS["3mo"])
    view = ohlcv.tail(display_days)
    ema20_view = ema20_full.tail(display_days)
    ema50_view = ema50_full.tail(display_days)

    candles = [
        CandleSchema(
            date=_date_str(row.date),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in view.itertuples()
    ]
    dates = [_date_str(d) for d in view["date"]]
    ema20_series = [SeriesPoint(date=d, value=float(v)) for d, v in zip(dates, ema20_view)]
    ema50_series = [SeriesPoint(date=d, value=float(v)) for d, v in zip(dates, ema50_view)]

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
        candles=candles,
        ema20_series=ema20_series,
        ema50_series=ema50_series,
        insight_text=llm_result.text,
        ai_provider=llm_result.provider,
        ai_error=llm_result.error,
    )

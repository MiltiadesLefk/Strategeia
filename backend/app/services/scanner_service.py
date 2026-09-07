from __future__ import annotations

from dataclasses import asdict

from app.analysis.scanner_scoring import score_symbol
from app.analysis.trend import analyze_chart
from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.schemas.scan_schemas import ScanResultSchema

SPARKLINE_POINTS = 20


def scan_symbols(symbols: list[str], data_provider: DataProvider) -> tuple[list[ScanResultSchema], list[str]]:
    results: list[ScanResultSchema] = []
    errors: list[str] = []

    for symbol in symbols:
        try:
            ohlcv = data_provider.get_ohlcv(symbol, period="6mo", interval="1d")
            quote = data_provider.get_quote(symbol)
        except AllProvidersFailedError as exc:
            errors.append(f"{symbol}: {exc}")
            continue

        chart = analyze_chart(ohlcv)
        volume_ratio = quote.volume / quote.avg_volume_20d if quote.avg_volume_20d > 0 else 1.0
        result = score_symbol(symbol, quote.price, quote.change_pct_24h, chart, volume_ratio)
        sparkline = ohlcv["close"].tail(SPARKLINE_POINTS).astype(float).tolist()
        results.append(ScanResultSchema(**asdict(result), sparkline=sparkline))

    return results, errors

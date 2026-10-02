"""The Data sources panel: every source with live health, and one-shot probes.

Health numbers come from `app.data_providers.health`, which the composite
provider and the SEC/FINRA/ECB/FRED clients feed. This module only assembles
them with a name, a plain-words purpose and the fallback position, and runs a
probe (one tiny real request that bypasses the cache) on demand.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from app.config import AppSettings
from app.data_providers import health
from app.data_providers.base import DataProviderError
from app.data_providers.ecb_provider import ECB_DATA_URL, EcbProvider, parse_ecb_csv
from app.data_providers.factory import get_data_provider
from app.data_providers.fred_provider import FRED_CSV_URL, FredProvider, parse_fred_csv
from app.data_providers.sec_client import SEC_HEALTH_NAME, get_sec_client
from app.schemas.data_sources_schemas import DataSourceHealth, DataSourcesResponse, ProbeResponse
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# The symbol every probe asks about: the most liquid ticker there is, so a
# failure is the source's, not an obscure symbol's.
PROBE_SYMBOL = "SPY"
# A SEC document small enough to be a polite probe (Apple's filing index).
SEC_PROBE_URL = "https://data.sec.gov/submissions/CIK0000320193.json"

PROBE_COOLDOWN_SECONDS = 10

# Per-symbol chain sources: name -> (label, what it is for). The ORDER of the
# chain is not here: it comes from the configured chain itself.
_CHAIN_INFO: dict[str, tuple[str, str]] = {
    "finnhub": ("Finnhub", "Quotes and company data, only when you set a free API key. Tried first when on."),
    "yfinance": ("Yahoo Finance (yfinance)", "Main source: prices, quotes, fundamentals, news, earnings, options. Free, no key, unofficial."),
    "nasdaq": ("Nasdaq", "Fallback: quotes, daily candles and company overview from an independent operator."),
    "stockanalysis": ("StockAnalysis", "Fallback for daily price bars of stocks and ETFs only. Free, no key, unofficial."),
    "stooq": ("Stooq", "Fallback for daily price bars only."),
    "sec_edgar": ("SEC EDGAR (insider trades)", "Last in the chain; answers only insider-trade (Form 4) questions."),
}

# Sources outside the per-symbol chain: (name, label, purpose).
_OTHER_SOURCES: tuple[tuple[str, str, str], ...] = (
    (SEC_HEALTH_NAME, "SEC EDGAR (filings)", "Filings: insider trades (Form 4), 8-K events, annual revenue facts. Held to 5 requests a second."),
    ("finra", "FINRA", "Daily short-sale volume files. A day with no file (weekend, holiday) is normal, not an error."),
    ("house_clerk", "House Clerk (Congress trades)", "Members' stock-trade report index and PDFs. Held to one request a second."),
    ("ecb", "European Central Bank", "Euro-area policy rates, EUR exchange rates and inflation series."),
    ("fred", "FRED (St. Louis Fed)", "US Treasury yields, fed funds, VIX, CPI, unemployment and dollar index series. No key."),
)


class UnknownSourceError(Exception):
    """The name is not a source this app knows."""


class NotProbeableError(Exception):
    """The source has no cheap real request to probe with."""


def _dt(epoch: float | None) -> datetime | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


def _chain_names(settings: AppSettings) -> list[str]:
    return [getattr(p, "name", type(p).__name__) for p in get_data_provider(settings)._providers]


def _row(
    name: str,
    label: str,
    purpose: str,
    snap: health.SourceHealth,
    *,
    in_chain: bool,
    position: int | None,
    can_probe: bool,
) -> DataSourceHealth:
    return DataSourceHealth(
        name=name,
        label=label,
        purpose=purpose,
        in_chain=in_chain,
        position=position,
        status=snap.status,
        calls=snap.calls,
        successes=snap.successes,
        failures=snap.failures,
        window_calls=snap.window_calls,
        success_rate=snap.window_success_rate,
        latency_p50_ms=snap.latency_p50_ms,
        latency_p95_ms=snap.latency_p95_ms,
        consecutive_failures=snap.consecutive_failures,
        last_used_at=_dt(snap.last_used_at),
        last_success_at=_dt(snap.last_success_at),
        last_error=snap.last_error,
        last_error_at=_dt(snap.last_error_at),
        cache_hit_ratio=snap.cache_hit_ratio,
        stale_served=snap.stale_served,
        can_probe=can_probe,
    )


def build_data_sources(settings: AppSettings) -> DataSourcesResponse:
    """Read-only: the configured chain in order, then the other sources."""
    snapshots = health.snapshot()
    chain = []
    for index, name in enumerate(_chain_names(settings), start=1):
        label, purpose = _CHAIN_INFO.get(name, (name, "Data provider."))
        chain.append(
            _row(
                name, label, purpose, snapshots.get(name) or health.empty_health(name),
                in_chain=True, position=index, can_probe=True,
            )
        )
    others = [
        _row(
            name, label, purpose, snapshots.get(name) or health.empty_health(name),
            in_chain=False, position=None, can_probe=name in _PROBE_RUNNERS,
        )
        for name, label, purpose in _OTHER_SOURCES
    ]
    return DataSourcesResponse(chain=chain, others=others, window_size=health.WINDOW_SIZE, generated_at=utcnow_naive())


# --- probes -----------------------------------------------------------------


def _probe_sec(_settings: AppSettings) -> str:
    body = get_sec_client().get_bytes(SEC_PROBE_URL)
    return f"filing index fetched ({len(body):,} bytes)"


def _probe_ecb(_settings: AppSettings) -> str:
    status, text = EcbProvider()._http_get(
        f"{ECB_DATA_URL}/EXR/D.USD.EUR.SP00.A", {"format": "csvdata", "lastNObservations": "1"}
    )
    if status != 200:
        raise DataProviderError(f"ecb returned HTTP {status}")
    points = parse_ecb_csv(text, "probe")
    if not points:
        raise DataProviderError("ecb answered with no observations")
    return f"EUR/USD {points[-1][1]} on {points[-1][0].isoformat()}"


def _probe_fred(_settings: AppSettings) -> str:
    status, text = FredProvider()._http_get(
        FRED_CSV_URL, {"id": "DGS10", "cosd": (date.today() - timedelta(days=14)).isoformat()}
    )
    if status != 200:
        raise DataProviderError(f"fred returned HTTP {status}")
    points = parse_fred_csv(text, "DGS10")
    if not points:
        raise DataProviderError("fred answered with no observations")
    return f"10-year yield {points[-1][1]}% on {points[-1][0].isoformat()}"


# Non-chain sources that have a cheap real request. FINRA has none: its files
# only exist per trading day, so there is no always-valid request to make.
_PROBE_RUNNERS: dict[str, Callable[[AppSettings], str]] = {
    SEC_HEALTH_NAME: _probe_sec,
    "ecb": _probe_ecb,
    "fred": _probe_fred,
}


def _probe_chain_provider(name: str, settings: AppSettings) -> str:
    if name == "sec_edgar":
        return _probe_sec(settings)
    providers = {getattr(p, "name", type(p).__name__): p for p in get_data_provider(settings)._providers}
    provider = providers[name]
    # Providers that only serve price bars are probed with bars.
    if name in ("stooq", "stockanalysis"):
        method_name, args = "get_ohlcv", (PROBE_SYMBOL, "1mo", "1d")
    else:
        method_name, args = "get_quote", (PROBE_SYMBOL,)
    method = getattr(provider, method_name)
    # The cache decorator keeps the undecorated function here: a probe must hit
    # the real source, or it would report on a cached copy.
    raw = getattr(method, "__wrapped__", None)
    result = raw(provider, *args) if raw is not None else method(*args)
    if method_name == "get_quote":
        return f"{PROBE_SYMBOL} quote {getattr(result, 'price', '?')}"
    return f"{PROBE_SYMBOL} {len(result)} daily bars"


def run_probe(name: str, settings: AppSettings) -> ProbeResponse:
    """One tiny real request to `name`. A failure is an answer (ok=False), not an error."""
    name = name.strip()
    if name in _PROBE_RUNNERS:
        runner = _PROBE_RUNNERS[name]
    elif name in _chain_names(settings):
        def runner(s: AppSettings, _name: str = name) -> str:
            return _probe_chain_provider(_name, s)
    elif name in {n for n, _, _ in _OTHER_SOURCES}:
        raise NotProbeableError(name)
    else:
        raise UnknownSourceError(name)
    started = time.perf_counter()
    try:
        with health.track(name, "probe"):
            detail = runner(settings)
    except Exception as exc:
        return ProbeResponse(
            name=name,
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=health.scrub_error(exc) or type(exc).__name__,
        )
    return ProbeResponse(name=name, ok=True, latency_ms=(time.perf_counter() - started) * 1000.0, detail=detail)

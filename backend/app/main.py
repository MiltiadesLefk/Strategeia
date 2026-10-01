from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import terminal as terminal_router
from app.api.routers import analysis, archive, auth, dashboard, market, portfolio, research, risk, scanner, settings, trade_plans
from app.api.routers import signals as signals_router
from app.api.routers import news as news_router
from app.api.routers import strategy as strategy_router
from app.api.routers import calibration as calibration_router
from app.api.routers import data_cache as data_cache_router
from app.api.routers import data_sources as data_sources_router
from app.api.routers import macro_series as macro_series_router
from app.api.routers import backtest_validations as backtest_validations_router
from app.api.routers import backtests as backtests_router
from app.api.routers import missed_trades as missed_trades_router
from app.api.routers import replay as replay_router
from app.api.routers import watchers as watchers_router
from app.api.routers import watchlist as watchlist_router
from app.api.routers import smart_money as smart_money_router
from app.config import get_infra_settings
from app.data_providers.base import AllProvidersFailedError
from app.backtest.service import recover_on_startup as recover_interrupted_backtests
from app.database import create_db_and_tables
from app.scheduler import start_scheduler, stop_scheduler
from app.watchers.sec_watcher import register_sec_watcher
from app.watchers.fed_watcher import register_fed_watcher
from app.watchers.posts_watcher import register_posts_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    # A backtest left running by a previous process can never finish: mark it failed.
    recover_interrupted_backtests()
    register_sec_watcher()  # sec_watcher: the SEC filings watcher (idle until the Watchers switch is on)
    register_fed_watcher()  # fed_watcher: Fed statements and speeches (market-wide, alert-only)
    register_posts_watcher()  # posts_watcher: posts from an unofficial archive feed
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Strategeia", lifespan=lifespan)

infra = get_infra_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=infra.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Cheap, unconditional hardening that costs nothing for local/dev use
    and matters once this is reachable beyond localhost — defense in depth
    alongside (never instead of) whatever reverse-proxy/TLS setup actually
    fronts this deployment, which is the right layer for
    Strict-Transport-Security (setting HSTS from here, over what might
    still be plain HTTP behind the proxy, would be meaningless at best).

    This is a JSON API, not an HTML-rendering surface (the frontend's own
    nginx.conf carries the equivalent headers for what it serves), so the
    three below cover what's relevant here: stop the browser from
    MIME-sniffing a response into something executable, refuse to be
    framed (nothing here should ever be embedded in an iframe), and avoid
    leaking full request URLs — which can carry a symbol or an internal
    path — to a cross-origin referrer target.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.exception_handler(AllProvidersFailedError)
def all_providers_failed_handler(request: Request, exc: AllProvidersFailedError) -> JSONResponse:
    # Every data provider failed for this call (invalid symbol, all providers
    # down, etc). Distinct from an actual server bug: no traceback, no 500 —
    # a clean, client-facing message the frontend's ErrorBanner can show.
    return JSONResponse(status_code=502, content={"detail": f"No data available: {exc}"})


app.include_router(auth.router)
app.include_router(scanner.router)
app.include_router(analysis.router)
app.include_router(research.router)
app.include_router(risk.router)
app.include_router(trade_plans.router)
app.include_router(signals_router.router)
app.include_router(news_router.router)
app.include_router(portfolio.router)
app.include_router(calibration_router.router)
app.include_router(settings.router)
app.include_router(dashboard.router)
app.include_router(market.router)
app.include_router(archive.router)
app.include_router(data_cache_router.router)
app.include_router(data_sources_router.router)
app.include_router(macro_series_router.router)
app.include_router(watchlist_router.router)
app.include_router(watchers_router.router)
app.include_router(strategy_router.router)
# Before the backtests router: its /{run_id} route would swallow /validations.
app.include_router(backtest_validations_router.router)
app.include_router(backtests_router.router)
app.include_router(missed_trades_router.router)
app.include_router(replay_router.router)
app.include_router(smart_money_router.router)
app.include_router(terminal_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}

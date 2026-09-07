from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import analysis, dashboard, portfolio, research, risk, scanner, settings, trade_plans
from app.config import get_infra_settings
from app.database import create_db_and_tables
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
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

app.include_router(scanner.router)
app.include_router(analysis.router)
app.include_router(research.router)
app.include_router(risk.router)
app.include_router(trade_plans.router)
app.include_router(portfolio.router)
app.include_router(settings.router)
app.include_router(dashboard.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}

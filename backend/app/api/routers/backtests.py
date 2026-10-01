from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.backtest import service
from app.backtest.models import BacktestRun, BacktestTrade
from app.backtest.params import BacktestInputError
from app.backtest.service import BacktestBusyError, BacktestManager, BacktestNotFoundError
from app.config import AppSettings
from app.schemas.backtest_schemas import (
    BacktestCreatedResponse,
    BacktestEquityPointSchema,
    BacktestProgress,
    BacktestRequest,
    BacktestRunSchema,
    BacktestTradeSchema,
)

router = APIRouter(prefix="/api/backtests", tags=["backtests"], dependencies=[Depends(require_auth)])

# A run takes the CPU for minutes, so starting one is not a button to mash: a
# second start right after the first is refused (on top of the one-run-at-a-time
# rule). Same shape as the other expensive routes (see the auto-trade cooldown
# in scanner.py).
BACKTEST_START_COOLDOWN_SECONDS = 5
_last_backtest_start_monotonic: float | None = None


def get_manager() -> BacktestManager:
    return service.get_backtest_manager()


def _run_schema(run: BacktestRun) -> BacktestRunSchema:
    return BacktestRunSchema(
        id=run.id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        cancel_requested=run.cancel_requested,
        progress=BacktestProgress(
            days_done=run.progress_days_done, days_total=run.progress_days_total, current_date=run.progress_date
        ),
        strategy_fingerprint=run.strategy_fingerprint,
        params=json.loads(run.params_json or "{}"),
        coverage=json.loads(run.coverage_json) if run.coverage_json else None,
        summary=json.loads(run.summary_json) if run.summary_json else None,
    )


def _trade_schema(trade: BacktestTrade) -> BacktestTradeSchema:
    data = trade.model_dump(exclude={"run_id", "scores_json"})
    return BacktestTradeSchema(**data, scores=json.loads(trade.scores_json) if trade.scores_json else {})


@router.post("", response_model=BacktestCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def start_backtest(
    request: BacktestRequest,
    manager: BacktestManager = Depends(get_manager),
    settings: AppSettings = Depends(get_app_settings),
) -> BacktestCreatedResponse:
    """Queue a run and return its id at once; poll GET /api/backtests/{id}. Reads
    price history from disk only (scripts/preload_history.py fills it), never the network."""
    global _last_backtest_start_monotonic
    now = time.monotonic()
    if _last_backtest_start_monotonic is not None:
        elapsed = now - _last_backtest_start_monotonic
        if elapsed < BACKTEST_START_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"A backtest was just started; wait {BACKTEST_START_COOLDOWN_SECONDS - elapsed:.0f}s.",
            )
    try:
        run_id = manager.start(request, settings)
    except BacktestBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (BacktestInputError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _last_backtest_start_monotonic = now
    return BacktestCreatedResponse(id=run_id, status="queued")


@router.get("", response_model=list[BacktestRunSchema])
def list_backtests(limit: int = 50, session: Session = Depends(get_session)) -> list[BacktestRunSchema]:
    return [_run_schema(run) for run in service.list_runs(session, max(1, min(limit, 200)))]


@router.get("/{run_id}", response_model=BacktestRunSchema)
def get_backtest(run_id: int, session: Session = Depends(get_session)) -> BacktestRunSchema:
    try:
        return _run_schema(service.get_run(session, run_id))
    except BacktestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/trades", response_model=list[BacktestTradeSchema])
def get_backtest_trades(run_id: int, session: Session = Depends(get_session)) -> list[BacktestTradeSchema]:
    try:
        return [_trade_schema(t) for t in service.run_trades(session, run_id)]
    except BacktestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/equity", response_model=list[BacktestEquityPointSchema])
def get_backtest_equity(run_id: int, session: Session = Depends(get_session)) -> list[BacktestEquityPointSchema]:
    try:
        points = service.run_equity(session, run_id)
    except BacktestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [BacktestEquityPointSchema(**p.model_dump(exclude={"id", "run_id"})) for p in points]


@router.post("/{run_id}/cancel")
def cancel_backtest(run_id: int, manager: BacktestManager = Depends(get_manager)) -> dict:
    try:
        return {"id": run_id, "status": manager.cancel(run_id)}
    except BacktestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

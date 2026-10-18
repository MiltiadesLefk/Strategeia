"""History of the 32-point evidence ratings (every morning scan that ran, and manual runs) plus a Rate-now trigger.
A manual run only rates and saves; it buys nothing."""

from __future__ import annotations

import json
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.database import engine
from app.portfolio.models import RatingRecord
from app.timeutil import utcnow_naive
from app.services.rating_service import rate_stock, rate_universe, record_rating

router = APIRouter(prefix="/api/ratings", tags=["ratings"], dependencies=[Depends(require_auth)])

_running: dict[str, bool] = {}  # run_id -> still going (process lifetime)
_lock = threading.Lock()


@router.get("/runs")
def list_runs(limit: int = 30, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(
        select(
            RatingRecord.run_id, func.min(RatingRecord.run_at), func.min(RatingRecord.source), func.count(RatingRecord.id),
            func.sum(RatingRecord.passed), func.sum(RatingRecord.bought),
        ).where(RatingRecord.source != "single").group_by(RatingRecord.run_id).order_by(func.min(RatingRecord.run_at).desc()).limit(min(max(limit, 1), 200))
    ).all()
    out = [
        {"run_id": r[0], "run_at": r[1], "source": r[2], "rated": r[3], "passed": int(r[4] or 0), "bought": int(r[5] or 0),
         "running": _running.get(r[0], False)}
        for r in rows
    ]
    # A run that has started but saved no row yet must still be listed, or the page cannot follow it.
    seen = {o["run_id"] for o in out}
    started = [{"run_id": rid, "run_at": utcnow_naive(), "source": "manual", "rated": 0, "passed": 0, "bought": 0, "running": True}
               for rid, going in _running.items() if going and rid not in seen]
    return started + out


def _row_json(r: RatingRecord) -> dict:
    return {"symbol": r.symbol, "lane": r.lane, "points": r.points, "max_points": r.max_points, "percent": r.percent,
            "passed": r.passed, "bought": r.bought, "dip": r.dip, "note": r.note, "run_at": r.run_at,
            "detail": json.loads(r.detail_json or "{}")}


@router.get("/latest")
def latest_per_symbol(session: Session = Depends(get_session)) -> dict[str, dict]:
    """The newest rating of every stock (a single-stock re-rate counts), for the scan table's Rating column."""
    out: dict[str, dict] = {}
    for r in session.exec(select(RatingRecord).order_by(RatingRecord.run_at.desc())).all():
        out.setdefault(r.symbol, _row_json(r))
    return out


@router.get("/stock/{symbol}")
def stock_history(symbol: str, limit: int = 10, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(RatingRecord).where(RatingRecord.symbol == symbol.upper())
                        .order_by(RatingRecord.run_at.desc()).limit(min(max(limit, 1), 100))).all()
    return [_row_json(r) for r in rows]


@router.post("/stock/{symbol}")
def rate_one(symbol: str, session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings),
             data_provider: DataProvider = Depends(get_data_provider)) -> dict:
    """Re-rate one stock now and save it (source "single", so it stays out of the run history). Buys nothing."""
    symbol = symbol.upper()
    try:
        gate = rate_stock(data_provider, symbol, settings.min_confidence_for_trade, session)
    except Exception as exc:  # noqa: BLE001 - no quote or history means no rating, like the gate
        raise HTTPException(status_code=502, detail=f"{symbol} could not be rated: {exc}") from exc
    row = record_rating(session, uuid.uuid4().hex, "single", symbol, "quality", gate)
    if row is None:
        raise HTTPException(status_code=500, detail="The rating could not be saved")
    return _row_json(row)


@router.get("/runs/{run_id}")
def run_detail(run_id: str, session: Session = Depends(get_session)) -> dict:
    rows = session.exec(select(RatingRecord).where(RatingRecord.run_id == run_id).order_by(RatingRecord.percent.desc())).all()
    if not rows and run_id not in _running:
        raise HTTPException(status_code=404, detail="No such run")
    return {
        "run_id": run_id, "running": _running.get(run_id, False),
        "ratings": [
            {"symbol": r.symbol, "lane": r.lane, "points": r.points, "max_points": r.max_points, "percent": r.percent,
             "passed": r.passed, "bought": r.bought, "dip": r.dip, "note": r.note, "run_at": r.run_at,
             "detail": json.loads(r.detail_json or "{}")}
            for r in rows
        ],
    }


@router.post("/run", status_code=202)
def start_run(settings: AppSettings = Depends(get_app_settings), data_provider: DataProvider = Depends(get_data_provider)) -> dict:
    with _lock:
        if any(_running.values()):
            raise HTTPException(status_code=429, detail="A rating run is already going; wait for it to finish.")
        run_id = uuid.uuid4().hex
        _running[run_id] = True

    def work() -> None:
        try:
            with Session(engine) as session:
                rate_universe(settings, data_provider, session, run_id, "manual")
        finally:
            _running[run_id] = False

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}

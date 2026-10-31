"""History of the 32-point evidence ratings (every morning scan that ran, and manual runs) plus a Rate-now trigger.
A manual run only rates and saves; it buys nothing."""

from __future__ import annotations

import json
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.llm_providers.base import LLMProvider
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.database import engine
from app.llm_providers import usage_meter
from app.portfolio.models import RatingRecord, RunUsage
from app.services.claude_usage_service import get_session_usage
from app.timeutil import utcnow_naive
from app.services.rating_service import REPORT_LANES, rate_stock, rate_universe, record_rating

router = APIRouter(prefix="/api/ratings", tags=["ratings"], dependencies=[Depends(require_auth)])

_running: dict[str, bool] = {}  # run_id -> still going (process lifetime)
_lock = threading.Lock()


@router.get("/runs")
def list_runs(limit: int = 30, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(
        select(
            RatingRecord.run_id, func.min(RatingRecord.run_at), func.min(RatingRecord.source), func.count(RatingRecord.id),
            func.sum(RatingRecord.passed), func.sum(RatingRecord.bought),
        ).where(RatingRecord.source != "single", RatingRecord.lane.notin_(REPORT_LANES)).group_by(RatingRecord.run_id).order_by(func.min(RatingRecord.run_at).desc()).limit(min(max(limit, 1), 200))
    ).all()
    out = [
        {"run_id": r[0], "run_at": r[1], "source": r[2], "rated": r[3], "passed": int(r[4] or 0), "bought": int(r[5] or 0),
         "running": _running.get(r[0], False), "usage": _usage_json(session, r[0])}
        for r in rows
    ]
    # A run that has started but saved no row yet must still be listed, or the page cannot follow it.
    seen = {o["run_id"] for o in out}
    started = [{"run_id": rid, "run_at": utcnow_naive(), "source": "manual", "rated": 0, "passed": 0, "bought": 0, "running": True}
               for rid, going in _running.items() if going and rid not in seen]
    return started + out


def _usage_json(session: Session, run_id: str) -> dict | None:
    u = session.get(RunUsage, run_id)
    if u is None:
        return None
    used = None if u.five_hour_before is None or u.five_hour_after is None else round(max(u.five_hour_after - u.five_hour_before, 0), 1)
    return {"calls": u.calls, "tokens_in": u.tokens_in, "tokens_out": u.tokens_out, "five_hour_used_pct": used}


def _row_json(r: RatingRecord) -> dict:
    return {"symbol": r.symbol, "lane": r.lane, "points": r.points, "max_points": r.max_points, "percent": r.percent,
            "passed": r.passed, "bought": r.bought, "dip": r.dip, "note": r.note, "run_at": r.run_at,
            "detail": json.loads(r.detail_json or "{}")}


@router.get("/latest")
def latest_per_symbol(session: Session = Depends(get_session)) -> dict[str, dict]:
    """The newest rating of every stock (a single-stock re-rate counts), for the scan table's Rating column."""
    out: dict[str, dict] = {}
    for r in session.exec(select(RatingRecord).where(RatingRecord.lane.notin_(REPORT_LANES)).order_by(RatingRecord.run_at.desc())).all():
        out.setdefault(r.symbol, _row_json(r))
    return out


@router.get("/stock/{symbol}")
def stock_history(symbol: str, limit: int = 10, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(RatingRecord).where(RatingRecord.symbol == symbol.upper(), RatingRecord.lane.notin_(REPORT_LANES))
                        .order_by(RatingRecord.run_at.desc()).limit(min(max(limit, 1), 100))).all()
    return [_row_json(r) for r in rows]


@router.post("/stock/{symbol}")
def rate_one(symbol: str, session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings),
             data_provider: DataProvider = Depends(get_data_provider), llm_provider: LLMProvider = Depends(get_llm_provider)) -> dict:
    """Re-rate one stock now and save it (source "single", so it stays out of the run history). Buys nothing."""
    symbol = symbol.upper()
    from app.services.news_reader_service import read_news

    try:  # the same AI news read the morning scan does first; a failure only means keyword news
        read_news(settings, data_provider, llm_provider, session, [symbol])
    except Exception:  # noqa: BLE001
        pass
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
        "run_id": run_id, "running": _running.get(run_id, False), "usage": _usage_json(session, run_id),
        "ratings": [
            {"symbol": r.symbol, "lane": r.lane, "points": r.points, "max_points": r.max_points, "percent": r.percent,
             "passed": r.passed, "bought": r.bought, "dip": r.dip, "note": r.note, "run_at": r.run_at,
             "detail": json.loads(r.detail_json or "{}")}
            for r in rows
        ],
    }


@router.post("/run", status_code=202)
def start_run(settings: AppSettings = Depends(get_app_settings), data_provider: DataProvider = Depends(get_data_provider),
              llm_provider: LLMProvider = Depends(get_llm_provider)) -> dict:
    with _lock:
        if any(_running.values()):
            raise HTTPException(status_code=429, detail="A rating run is already going; wait for it to finish.")
        run_id = uuid.uuid4().hex
        _running[run_id] = True

    def work() -> None:
        try:
            with Session(engine) as session:
                before, window = usage_meter.snapshot(), get_session_usage(fresh=True)
                rate_universe(settings, data_provider, session, run_id, "manual", llm_provider)
                after, window_after = usage_meter.snapshot(), get_session_usage(fresh=True)
                session.add(RunUsage(
                    run_id=run_id, calls=after["calls"] - before["calls"], tokens_in=after["tokens_in"] - before["tokens_in"],
                    tokens_out=after["tokens_out"] - before["tokens_out"],
                    five_hour_before=window and window["five_hour_pct"], five_hour_after=window_after and window_after["five_hour_pct"]))
                session.commit()
        finally:
            _running[run_id] = False

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}

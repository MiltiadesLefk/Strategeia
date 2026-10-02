from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import true
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.markets import format_market_time
from app.portfolio.engine import (
    DuplicatePositionError,
    InsufficientCashError,
    MarketClosedError,
    MaxPositionsExceededError,
    PaperTradingEngine,
    SectorConcentrationError,
    SleeveDisabledError,
    StalePlanError,
)
from app.portfolio.excursion_service import live_excursion
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, Sleeve, TradePlanRecord
from app.portfolio.sleeves import (
    ALL_SLEEVES,
    CORE_SLEEVE_KEY,
    SleeveError,
    ensure_core_sleeve,
    get_sleeve,
    read_scope,
    scope_clause,
)
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.portfolio_schemas import (
    ClosePositionRequest,
    EquityPointSchema,
    OpenPositionRequest,
    PortfolioStatsSchema,
    PositionSchema,
)
from app.services.deferred_evaluation_service import pending_redo_for_plan
from app.services.lesson_service import generate_lesson, is_real_llm
from app.services.sleeve_service import build_sleeve_engine, mark_all_sleeves
from app.strategy.service import plan_strategy_versions

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"], dependencies=[Depends(require_auth)])


def build_engine(
    session: Session, data_provider: DataProvider, settings: AppSettings, sleeve: Sleeve | None = None
) -> PaperTradingEngine:
    """The paper engine for `sleeve` (default: the core sleeve, the original account)."""
    return build_sleeve_engine(session, data_provider, settings, sleeve)


def _sleeve_http_error(exc: SleeveError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _mark_for_read(
    session: Session, data_provider: DataProvider, settings: AppSettings, sleeve_ref: str | None
) -> None:
    """The read-only exit check (snapshot=False) for one sleeve, or for all of them.
    Resolving the sleeve first makes an unknown key a 404 before any work is done."""
    try:
        if sleeve_ref == ALL_SLEEVES:
            mark_all_sleeves(session, data_provider, settings, snapshot=False)
            return
        read_scope(session, sleeve_ref)
        sleeve = None if sleeve_ref in (None, CORE_SLEEVE_KEY) else get_sleeve(session, sleeve_ref)
    except SleeveError as exc:
        raise _sleeve_http_error(exc) from exc
    build_engine(session, data_provider, settings, sleeve).mark_to_market(snapshot=False)


def _sleeve_keys(session: Session) -> dict[int | None, str]:
    """sleeve_id -> key, with None (a row from before sleeves existed) as core."""
    keys: dict[int | None, str] = {None: CORE_SLEEVE_KEY}
    for sleeve in session.exec(select(Sleeve)).all():
        keys[sleeve.id] = sleeve.key
    return keys


def _engine_for_sleeve_id(
    session: Session, data_provider: DataProvider, settings: AppSettings, sleeve_id: int | None
) -> PaperTradingEngine:
    """The engine of the sleeve a plan or position belongs to (None = core)."""
    sleeve = session.get(Sleeve, sleeve_id) if sleeve_id is not None else None
    return build_engine(session, data_provider, settings, sleeve)


def position_to_schema(
    position: PaperPosition,
    strategy_versions: dict[int, int | None] | None = None,
    sleeve_keys: dict[int | None, str] | None = None,
) -> PositionSchema:
    """`strategy_versions` maps plan id -> the plan's strategy version (see
    app/strategy); a position inherits its plan's rather than storing its own."""
    version = (strategy_versions or {}).get(position.trade_plan_id)
    key = (sleeve_keys or {}).get(position.sleeve_id, CORE_SLEEVE_KEY if position.sleeve_id is None else None)
    return PositionSchema(**position.model_dump(), strategy_version=version, sleeve_key=key)


@router.get("/positions", response_model=list[PositionSchema])
def list_positions(
    sleeve: str | None = Query(default=None, description="Sleeve key; omitted = core, 'all' = every sleeve"),
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> list[PositionSchema]:
    # snapshot=False: a GET must not append to the equity curve. With
    # react-query's refetch-on-focus, every tab focus used to write an
    # EquitySnapshot row, so the curve was sampled by how often the dashboard
    # was looked at rather than by time — and the table grew without bound.
    # Exit detection still runs here so a stop/TP that fired between
    # scheduler ticks shows up immediately; only the curve write is dropped.
    _mark_for_read(session, data_provider, settings, sleeve)
    query = select(PaperPosition).order_by(PaperPosition.opened_at.desc())
    if sleeve != ALL_SLEEVES:
        query = query.where(scope_clause(PaperPosition.sleeve_id, read_scope(session, sleeve)))
    positions = session.exec(query).all()
    strategy_versions = plan_strategy_versions(session, [p.trade_plan_id for p in positions])
    sleeve_keys = _sleeve_keys(session)
    schemas = [position_to_schema(p, strategy_versions, sleeve_keys) for p in positions]
    # An open position's best/worst price so far (MFE/MAE) is computed here, read-only, and
    # never stored; a closed one already carries what was recorded when it closed.
    for position, schema in zip(positions, schemas):
        if position.status == "open":
            live = live_excursion(position, data_provider)
            if live is not None:
                schema.mfe_pct, schema.mae_pct, schema.mfe_r, schema.mae_r = live.mfe_pct, live.mae_pct, live.mfe_r, live.mae_r
    return schemas


@router.post("/positions", response_model=PositionSchema)
def open_position(
    req: OpenPositionRequest,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PositionSchema:
    plan = session.get(TradePlanRecord, req.trade_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Trade plan not found")
    if plan.status != "pending":
        raise HTTPException(status_code=400, detail=f"Trade plan is already {plan.status}")
    # D10 = C: a plan made while the market was closed is replaced by a fresh
    # one shortly after the open, and only the fresh one may execute — not
    # this one, built on the previous session's prices, even in the minutes
    # between the bell and its redo.
    redo = pending_redo_for_plan(session, plan.id)
    if redo is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This plan was made while the market was closed. It will be redone from fresh data at "
                f"{format_market_time(redo.due_at)}, and only the fresh plan can be executed."
            ),
        )
    # The position opens in the sleeve the plan was made for (no sleeve = core).
    engine = _engine_for_sleeve_id(session, data_provider, settings, plan.sleeve_id)
    try:
        position = engine.open_position(plan)
    except MarketClosedError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc} Plans made while the market is closed are redone from fresh data at the next open.",
        ) from exc
    except (
        InsufficientCashError,
        DuplicatePositionError,
        MaxPositionsExceededError,
        SectorConcentrationError,
        SleeveDisabledError,
        StalePlanError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return position_to_schema(
        position, plan_strategy_versions(session, [position.trade_plan_id]), _sleeve_keys(session)
    )


@router.post("/positions/{position_id}/close", response_model=PositionSchema)
def close_position(
    position_id: int,
    req: ClosePositionRequest,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PositionSchema:
    position = session.get(PaperPosition, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != "open":
        raise HTTPException(status_code=400, detail="Position is already closed")
    price = data_provider.get_quote(position.symbol).price
    # Closed through the engine of the sleeve that holds it, so that sleeve's cash is credited.
    engine = _engine_for_sleeve_id(session, data_provider, settings, position.sleeve_id)
    closed = engine.close_position(position, price, req.reason)
    return position_to_schema(closed, plan_strategy_versions(session, [closed.trade_plan_id]), _sleeve_keys(session))


# Each manual lesson spends a call on the user's AI provider, and the button can be
# double-clicked. A short per-position wait (in-process, not persisted, like the other
# cooldowns here) stops that without blocking lessons for other positions.
LESSON_REQUEST_COOLDOWN_SECONDS = 20
_last_lesson_request_monotonic: dict[int, float] = {}


@router.post("/positions/{position_id}/lesson", response_model=PositionSchema)
def write_position_lesson(
    position_id: int,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> PositionSchema:
    """Write (or rewrite) the AI lesson for one closed position. This is how a position
    closed before lessons existed gets one: the background job only covers recent closes.
    A model failure is not an HTTP error: the position comes back with `lesson_error`
    set (and any earlier lesson kept)."""
    position = session.get(PaperPosition, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != "closed":
        raise HTTPException(status_code=400, detail="Only a closed position gets a lesson")
    if not is_real_llm(llm_provider):
        raise HTTPException(status_code=400, detail="No AI provider is configured (Settings -> AI Provider)")
    now = time.monotonic()
    last = _last_lesson_request_monotonic.get(position_id)
    if last is not None and now - last < LESSON_REQUEST_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"A lesson was just requested for this position; wait "
            f"{LESSON_REQUEST_COOLDOWN_SECONDS - (now - last):.0f}s before asking again.",
        )
    _last_lesson_request_monotonic[position_id] = now
    outcome = generate_lesson(session, position, data_provider, llm_provider)
    if outcome.status == "skipped":
        raise HTTPException(status_code=409, detail=outcome.detail)
    return position_to_schema(
        position, plan_strategy_versions(session, [position.trade_plan_id]), _sleeve_keys(session)
    )


@router.get("/stats", response_model=PortfolioStatsSchema)
def stats(
    sleeve: str | None = Query(default=None, description="Sleeve key; omitted = core, 'all' = every sleeve"),
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PortfolioStatsSchema:
    _mark_for_read(session, data_provider, settings, sleeve)  # read-only: see list_positions
    try:
        result = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash, sleeve)
    except SleeveError as exc:
        raise _sleeve_http_error(exc) from exc
    return PortfolioStatsSchema(**result.__dict__)


@router.get("/equity-curve", response_model=list[EquityPointSchema])
def equity_curve(
    sleeve: str | None = Query(default=None, description="Sleeve key; omitted = core"),
    session: Session = Depends(get_session),
) -> list[EquityPointSchema]:
    """One sleeve's equity curve. There is no combined curve ('all' is a 400): the
    sleeves are sampled at different moments, so summing their points by timestamp
    would invent values nobody measured. Ask for each sleeve and plot them together."""
    if sleeve == ALL_SLEEVES:
        raise HTTPException(
            status_code=400,
            detail="There is no combined equity curve: request each sleeve's curve with ?sleeve=<key>.",
        )
    try:
        scope = read_scope(session, sleeve)
    except SleeveError as exc:
        raise _sleeve_http_error(exc) from exc
    snapshots = session.exec(
        select(EquitySnapshot).where(scope_clause(EquitySnapshot.sleeve_id, scope)).order_by(EquitySnapshot.timestamp.asc())
    ).all()
    return [EquityPointSchema(**s.model_dump()) for s in snapshots]


@router.post("/reset", response_model=PortfolioStatsSchema)
def reset_portfolio(
    sleeve: str | None = Query(default=None, description="Sleeve to reset; omitted = core, 'all' = every sleeve"),
    confirm: bool = Query(default=False, description="Required (true) when sleeve=all"),
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PortfolioStatsSchema:
    """Wipes ONE sleeve's simulated portfolio (positions, equity history, cash
    balance; the core sleeve unless `sleeve` names another) and recreates its
    account fresh at its starting cash: Settings -> Paper Account -> Starting Cash
    for core, the sleeve's own starting cash otherwise. `sleeve=all` wipes every
    sleeve and needs `confirm=true`; the sleeves themselves stay. Generated
    trade-plan history is left alone: this only resets the paper-trading side,
    not the AI scan/plan record. Needed because `AccountState` is only ever
    seeded once (see SettingsPage's "only takes effect for a fresh account" note):
    this is the supported way to actually apply a changed starting-cash value."""
    everything = sleeve == ALL_SLEEVES
    if everything and not confirm:
        raise HTTPException(status_code=400, detail="Resetting every sleeve wipes all paper positions: pass confirm=true.")
    try:
        scope = None if everything else read_scope(session, sleeve)
        target = None if everything or sleeve in (None, CORE_SLEEVE_KEY) else get_sleeve(session, sleeve)
    except SleeveError as exc:
        raise _sleeve_http_error(exc) from exc

    def covered(column):
        return scope_clause(column, scope) if scope is not None else true()

    for position in session.exec(select(PaperPosition).where(covered(PaperPosition.sleeve_id))).all():
        session.delete(position)
    for snapshot in session.exec(select(EquitySnapshot).where(covered(EquitySnapshot.sleeve_id))).all():
        session.delete(snapshot)
    for account in session.exec(select(AccountState).where(covered(AccountState.sleeve_id))).all():
        session.delete(account)
    for plan in session.exec(
        select(TradePlanRecord).where(TradePlanRecord.status == "executed", covered(TradePlanRecord.sleeve_id))
    ).all():
        plan.status = "discarded"
        session.add(plan)
    session.commit()

    if everything:
        ensure_core_sleeve(session)
        for each in [None, *session.exec(select(Sleeve).where(Sleeve.key != CORE_SLEEVE_KEY)).all()]:
            build_engine(session, data_provider, settings, each).get_account_state()
        target_scope = read_scope(session, None)
    else:
        build_engine(session, data_provider, settings, target).get_account_state()
        target_scope = read_scope(session, sleeve)
    result = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash, target_scope)
    return PortfolioStatsSchema(**result.__dict__)

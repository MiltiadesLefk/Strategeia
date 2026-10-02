"""Sleeves: one isolated paper account per trading style.

The rule that matters most: a sleeve is a wall. Cash, positions, the duplicate
and cap rules, equity points, the exit scan and every statistic are per sleeve,
and a database written before sleeves existed (rows with no sleeve_id) reads as
the core sleeve exactly as it always did.

No test here touches the network or the real database: bars and quotes come from
a fake provider, every database is in memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_data_provider, get_session
from app.config import AppSettings
from app.data_providers.base import QuoteData
from app.llm_providers.null_provider import NullLLMProvider
from app.main import app
from app.portfolio.engine import (
    DuplicatePositionError,
    MaxPositionsExceededError,
    PaperTradingEngine,
    SectorConcentrationError,
    SleeveDisabledError,
)
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, Sleeve, TradePlanRecord
from app.portfolio.sleeves import (
    CORE_SLEEVE_KEY,
    SleeveError,
    create_sleeve,
    delete_sleeve,
    ensure_core_sleeve,
    get_sleeve,
    list_sleeves,
    read_scope,
    update_sleeve,
)
from app.portfolio.stats import compute_portfolio_stats
from app.services import trade_plan_service
from app.services.sleeve_service import build_sleeve_engine, mark_all_sleeves
from tests.test_trade_plan_service import FakeUptrendDataProvider

CASH = 100_000.0
PRICE = 100.0
FIRST_BAR = datetime(2026, 1, 5)
OPENED_AT = FIRST_BAR + timedelta(days=20, hours=20)


class QuietProvider:
    """Daily bars that stay in 99-101 (never near a 90 stop or 120 target); a
    symbol in `stop_touched` dips through 90 on one bar after the entry."""

    name = "fake"

    def __init__(self, stop_touched: tuple[str, ...] = ()):
        self._stop_touched = set(stop_touched)

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        rows = []
        for i in range(40):
            o, h, low, c = PRICE, PRICE + 1, PRICE - 1, PRICE
            if symbol in self._stop_touched and i == 25:
                o, h, low, c = 99.0, 99.5, 85.0, 97.0
            rows.append({"date": FIRST_BAR + timedelta(days=i), "open": o, "high": h, "low": low, "close": c, "volume": 1e6})
        return pd.DataFrame(rows)

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=PRICE, change_pct_24h=0.0, volume=1e6, avg_volume_20d=1e6)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(db):
    with Session(db) as s:
        yield s


def plan(session: Session, symbol: str = "AAPL", sleeve_id: int | None = None, shares: int = 10, **kw) -> TradePlanRecord:
    row = TradePlanRecord(
        symbol=symbol,
        direction=kw.pop("direction", "long"),
        entry=PRICE,
        stop=90.0,
        tp1=120.0,
        tp2=130.0,
        rr1=2.0,
        rr2=3.0,
        suggested_shares=shares,
        account_risk_dollars=100.0,
        confidence_score=70,
        sleeve_id=sleeve_id,
        **kw,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def engine_for(session, sleeve: Sleeve | None = None, provider=None, **kw) -> PaperTradingEngine:
    cash = CASH if sleeve is None else sleeve.starting_cash
    return PaperTradingEngine(session, provider or QuietProvider(), cash, sleeve=sleeve, **kw)


def new_sleeve(session, name="Momentum", cash=50_000.0) -> Sleeve:
    return create_sleeve(session, name, "momentum", cash)


# ------------------------------------------------------------- legacy database


def test_rows_without_a_sleeve_id_read_as_core(session):
    """A database from before sleeves has no sleeve_id anywhere and no Sleeve row.
    The core engine must see all of it, with no backfill."""
    session.add(AccountState(starting_cash=CASH, current_cash=CASH - 1000))
    session.add(
        PaperPosition(
            symbol="AAPL", direction="long", entry_price=PRICE, stop_loss=90.0, tp1=120.0, tp2=130.0, shares=10,
            opened_at=OPENED_AT,
        )
    )
    session.add(EquitySnapshot(equity_value=CASH, cash_balance=CASH - 1000))
    session.commit()
    assert session.exec(select(Sleeve)).all() == []

    engine = engine_for(session)
    assert engine.get_account_state().current_cash == CASH - 1000  # the legacy account, not a new one
    assert len(session.exec(select(AccountState)).all()) == 1
    stats = compute_portfolio_stats(session, QuietProvider(), CASH)
    assert (stats.active_positions, stats.current_cash) == (1, CASH - 1000)

    # A duplicate of the legacy position is refused: it is core's.
    with pytest.raises(DuplicatePositionError):
        engine.open_position(plan(session, "AAPL"))

    # A second sleeve sees none of it.
    other = new_sleeve(session)
    assert compute_portfolio_stats(session, QuietProvider(), CASH, other.key).active_positions == 0
    assert engine_for(session, other).get_account_state().current_cash == other.starting_cash


def test_reads_never_create_the_core_sleeve(session):
    assert read_scope(session, None).sleeve_id is None
    compute_portfolio_stats(session, QuietProvider(), CASH)
    engine_for(session).mark_to_market(snapshot=False)
    assert session.exec(select(Sleeve)).all() == []
    assert session.exec(select(AccountState)).all() == []


def test_the_core_sleeve_is_created_lazily_once(session):
    first = ensure_core_sleeve(session)
    assert (first.key, first.name) == (CORE_SLEEVE_KEY, "Swing (rules)")
    assert ensure_core_sleeve(session).id == first.id
    assert len(session.exec(select(Sleeve)).all()) == 1


def test_a_database_from_before_sleeves_is_migrated_and_reads_as_core(monkeypatch, tmp_path):
    """The real upgrade path: tables on disk without sleeve_id, data in them, then the
    app starts. The additive migration adds the column (NULL), the Sleeve table is
    created, and the core engine and stats read the old rows with no backfill."""
    import app.database as database_module

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE accountstate (id INTEGER PRIMARY KEY, starting_cash FLOAT NOT NULL, current_cash FLOAT NOT NULL)")
        conn.exec_driver_sql("INSERT INTO accountstate (starting_cash, current_cash) VALUES (100000, 99000)")
        conn.exec_driver_sql(
            "CREATE TABLE equitysnapshot (id INTEGER PRIMARY KEY, timestamp DATETIME NOT NULL, "
            "equity_value FLOAT NOT NULL, cash_balance FLOAT NOT NULL)"
        )
        conn.exec_driver_sql("INSERT INTO equitysnapshot (timestamp, equity_value, cash_balance) VALUES ('2026-01-01 00:00:00', 100000, 99000)")
        conn.exec_driver_sql(
            "CREATE TABLE paperposition (id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, direction VARCHAR NOT NULL, "
            "entry_price FLOAT NOT NULL, stop_loss FLOAT NOT NULL, tp1 FLOAT NOT NULL, tp2 FLOAT NOT NULL, "
            "shares INTEGER NOT NULL, opened_at DATETIME NOT NULL, status VARCHAR NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO paperposition (symbol, direction, entry_price, stop_loss, tp1, tp2, shares, opened_at, status) "
            "VALUES ('AAPL', 'long', 100, 90, 120, 130, 10, '2026-01-02 00:00:00', 'open')"
        )
        conn.commit()

    monkeypatch.setattr(database_module, "engine", engine)
    SQLModel.metadata.create_all(engine)
    database_module._add_missing_columns()

    with engine.connect() as conn:
        for table in ("accountstate", "equitysnapshot", "paperposition", "tradeplanrecord"):
            columns = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()}
            assert "sleeve_id" in columns, table
    with Session(engine) as s:
        position = s.exec(select(PaperPosition)).one()
        assert position.sleeve_id is None
        stats = compute_portfolio_stats(s, QuietProvider(), CASH)
        assert (stats.active_positions, stats.current_cash) == (1, 99_000.0)
        assert engine_for(s).get_account_state().current_cash == 99_000.0
        with pytest.raises(DuplicatePositionError):
            engine_for(s).open_position(plan(s, "AAPL"))
        assert compute_portfolio_stats(s, QuietProvider(), CASH, "all").active_positions == 1


# ------------------------------------------------------------------- isolation


def test_each_sleeve_has_its_own_cash(session):
    other = new_sleeve(session, cash=50_000.0)
    core_engine, other_engine = engine_for(session), engine_for(session, other)

    core_engine.open_position(plan(session, "AAPL"))
    other_engine.open_position(plan(session, "MSFT", sleeve_id=other.id, shares=20))

    assert core_engine.get_account_state().current_cash == CASH - 10 * PRICE
    assert other_engine.get_account_state().current_cash == 50_000.0 - 20 * PRICE
    assert core_engine.available_cash() == CASH - 10 * PRICE
    accounts = session.exec(select(AccountState)).all()
    assert len(accounts) == 2
    assert {a.sleeve_id for a in accounts} == {ensure_core_sleeve(session).id, other.id}


def test_new_rows_carry_their_sleeve(session):
    other = new_sleeve(session)
    core_position = engine_for(session).open_position(plan(session, "AAPL"))
    other_position = engine_for(session, other).open_position(plan(session, "AAPL", sleeve_id=other.id))
    assert core_position.sleeve_id == ensure_core_sleeve(session).id
    assert other_position.sleeve_id == other.id
    snapshots = {s.sleeve_id for s in session.exec(select(EquitySnapshot)).all()}
    assert snapshots == {core_position.sleeve_id, other.id}


def test_two_sleeves_may_hold_the_same_symbol_but_one_sleeve_may_not_hold_it_twice(session):
    other = new_sleeve(session)
    core_engine, other_engine = engine_for(session), engine_for(session, other)
    core_engine.open_position(plan(session, "AAPL"))
    other_engine.open_position(plan(session, "AAPL", sleeve_id=other.id))  # no DuplicatePositionError

    held = session.exec(select(PaperPosition).where(PaperPosition.symbol == "AAPL")).all()
    assert len(held) == 2
    with pytest.raises(DuplicatePositionError):
        core_engine.open_position(plan(session, "AAPL"))
    with pytest.raises(DuplicatePositionError):
        other_engine.open_position(plan(session, "AAPL", sleeve_id=other.id))


def test_the_position_cap_applies_per_sleeve(session):
    other = new_sleeve(session)
    core_engine = engine_for(session, max_concurrent_positions=1)
    other_engine = engine_for(session, other, max_concurrent_positions=1)
    core_engine.open_position(plan(session, "AAPL"))
    with pytest.raises(MaxPositionsExceededError):
        core_engine.open_position(plan(session, "MSFT"))
    other_engine.open_position(plan(session, "MSFT", sleeve_id=other.id))  # core being full does not matter
    with pytest.raises(MaxPositionsExceededError):
        other_engine.open_position(plan(session, "NVDA", sleeve_id=other.id))


def test_the_sector_cap_applies_per_sleeve(session, monkeypatch):
    monkeypatch.setattr("app.portfolio.engine.get_sector", lambda symbol: "Technology")
    other = new_sleeve(session)
    core_engine = engine_for(session, max_positions_per_sector=1)
    other_engine = engine_for(session, other, max_positions_per_sector=1)
    core_engine.open_position(plan(session, "AAPL"))
    with pytest.raises(SectorConcentrationError):
        core_engine.open_position(plan(session, "MSFT"))
    other_engine.open_position(plan(session, "MSFT", sleeve_id=other.id))


def test_short_collateral_is_reserved_per_sleeve(session):
    """A short's sale proceeds sit in that sleeve's cash as collateral; they must
    not reduce, or inflate, the buying power of another sleeve."""
    other = new_sleeve(session, cash=50_000.0)
    engine_for(session).open_position(plan(session, "AAPL", direction="short", shares=100))
    assert engine_for(session).available_cash() == pytest.approx(CASH)  # proceeds minus collateral
    assert engine_for(session, other).available_cash() == 50_000.0


def test_mark_to_market_only_touches_its_own_sleeve(session):
    other = new_sleeve(session)
    provider = QuietProvider(stop_touched=("AAPL",))
    core_engine, other_engine = engine_for(session, provider=provider), engine_for(session, other, provider=provider)
    core_position = core_engine.open_position(plan(session, "AAPL"))
    other_position = other_engine.open_position(plan(session, "AAPL", sleeve_id=other.id))
    for position in (core_position, other_position):  # opened long before the dip bar
        position.opened_at = OPENED_AT - timedelta(days=15)
        session.add(position)
    session.commit()

    closed = core_engine.mark_to_market()

    assert [p.id for p in closed] == [core_position.id]
    session.refresh(other_position)
    assert other_position.status == "open"  # core's sweep left it alone
    assert other_engine.get_account_state().current_cash == 50_000.0 - 10 * PRICE
    assert [p.id for p in other_engine.mark_to_market()] == [other_position.id]


def test_mark_all_sleeves_sweeps_every_sleeve_including_disabled_ones(session):
    other = new_sleeve(session)
    provider = QuietProvider(stop_touched=("AAPL",))
    core_position = engine_for(session, provider=provider).open_position(plan(session, "AAPL"))
    other_position = engine_for(session, other, provider=provider).open_position(plan(session, "AAPL", sleeve_id=other.id))
    for position in (core_position, other_position):
        position.opened_at = OPENED_AT - timedelta(days=15)
        session.add(position)
    session.commit()
    update_sleeve(session, other, enabled=False)

    closed = mark_all_sleeves(session, provider, AppSettings())

    assert {p.id for p in closed} == {core_position.id, other_position.id}
    points = session.exec(select(EquitySnapshot)).all()
    assert {p.sleeve_id for p in points} == {core_position.sleeve_id, other.id}


def test_equity_snapshots_are_per_sleeve(session):
    other = new_sleeve(session, cash=50_000.0)
    engine_for(session).open_position(plan(session, "AAPL"))
    engine_for(session, other).open_position(plan(session, "MSFT", sleeve_id=other.id, shares=20))
    core_id = ensure_core_sleeve(session).id
    by_sleeve = {
        sid: [p for p in session.exec(select(EquitySnapshot)).all() if p.sleeve_id == sid] for sid in (core_id, other.id)
    }
    assert len(by_sleeve[core_id]) == len(by_sleeve[other.id]) == 1
    # Each point is that sleeve's own equity: cash plus the marked position.
    assert by_sleeve[core_id][0].equity_value == pytest.approx(CASH)
    assert by_sleeve[other.id][0].equity_value == pytest.approx(50_000.0)


def test_closing_credits_the_sleeve_that_holds_the_position(session):
    other = new_sleeve(session, cash=50_000.0)
    position = engine_for(session, other).open_position(plan(session, "MSFT", sleeve_id=other.id, shares=20))

    with pytest.raises(ValueError):  # the wrong sleeve's engine must not move its cash
        engine_for(session).close_position(position, 110.0, "manual")
    assert engine_for(session).get_account_state().current_cash == CASH  # core untouched

    closed = engine_for(session, other).close_position(position, 110.0, "manual")
    assert closed.realized_pnl == pytest.approx(200.0)
    assert engine_for(session, other).get_account_state().current_cash == pytest.approx(50_000.0 + 200.0)


def test_a_disabled_sleeve_opens_nothing_new_but_still_manages_what_it_holds(session):
    other = new_sleeve(session)
    provider = QuietProvider(stop_touched=("AAPL",))
    held = engine_for(session, other, provider=provider).open_position(plan(session, "AAPL", sleeve_id=other.id))
    held.opened_at = OPENED_AT - timedelta(days=15)
    session.add(held)
    session.commit()
    update_sleeve(session, other, enabled=False)

    with pytest.raises(SleeveDisabledError):
        engine_for(session, other, provider=provider).open_position(plan(session, "MSFT", sleeve_id=other.id))
    assert [p.id for p in engine_for(session, other, provider=provider).mark_to_market()] == [held.id]


# ----------------------------------------------------------------------- stats


def test_stats_are_per_sleeve_and_all_pools_them(session):
    other = new_sleeve(session, cash=50_000.0)
    core_engine, other_engine = engine_for(session), engine_for(session, other)
    winner = core_engine.open_position(plan(session, "AAPL"))
    loser = other_engine.open_position(plan(session, "MSFT", sleeve_id=other.id, shares=20))
    core_engine.close_position(winner, 110.0, "manual")  # +100
    other_engine.close_position(loser, 95.0, "manual")  # -100

    core = compute_portfolio_stats(session, QuietProvider(), CASH)
    mine = compute_portfolio_stats(session, QuietProvider(), CASH, other.key)
    both = compute_portfolio_stats(session, QuietProvider(), CASH, "all")

    assert (core.total_trades, core.win_rate, core.starting_cash) == (1, 100.0, CASH)
    assert core.portfolio_value == pytest.approx(CASH + 100.0)
    assert (mine.total_trades, mine.win_rate, mine.starting_cash) == (1, 0.0, 50_000.0)
    assert mine.portfolio_value == pytest.approx(50_000.0 - 100.0)
    assert (both.total_trades, both.win_rate) == (2, 50.0)
    assert both.starting_cash == pytest.approx(CASH + 50_000.0)
    assert both.portfolio_value == pytest.approx(CASH + 50_000.0)  # +100 and -100 net out
    with pytest.raises(SleeveError):
        compute_portfolio_stats(session, QuietProvider(), CASH, "nope")


# ---------------------------------------------------------------------- CRUD


def test_create_update_and_delete_rules(session):
    sleeve = create_sleeve(session, "AI Committee", "ai", 25_000.0, "notes")
    assert sleeve.key == "ai-committee" and sleeve.enabled and sleeve.color
    again = create_sleeve(session, "AI  Committee!", "ai", 10_000.0)
    assert again.key == "ai-committee-2"  # the slug is made unique
    with pytest.raises(SleeveError) as clash:
        create_sleeve(session, "ai committee", "ai", 1.0)  # same name, any case
    assert clash.value.status_code == 409
    with pytest.raises(SleeveError):
        create_sleeve(session, "Bad", "x", 0)
    with pytest.raises(SleeveError):
        create_sleeve(session, "   ", "x", 1000)

    renamed = update_sleeve(session, sleeve, name="AI Panel", enabled=False)
    assert (renamed.name, renamed.enabled) == ("AI Panel", False)
    assert renamed.key == "ai-committee"  # the key is permanent
    with pytest.raises(SleeveError):
        update_sleeve(session, ensure_core_sleeve(session), enabled=False)
    assert get_sleeve(session, str(sleeve.id)).id == sleeve.id  # by id too
    with pytest.raises(SleeveError) as missing:
        get_sleeve(session, "nope")
    assert missing.value.status_code == 404

    used = create_sleeve(session, "Used", "x", 10_000.0)
    engine_for(session, used).open_position(plan(session, "AAPL", sleeve_id=used.id))
    with pytest.raises(SleeveError) as refused:
        delete_sleeve(session, used)
    assert refused.value.status_code == 409
    with pytest.raises(SleeveError):
        delete_sleeve(session, ensure_core_sleeve(session))
    delete_sleeve(session, again)
    assert again.key not in [s.key for s in list_sleeves(session)]


# ------------------------------------------------------- plan -> sleeve (service)


def _settings(**kw):
    return AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=True, **kw)


def test_a_plan_is_recorded_on_its_sleeve_and_executes_there(session, monkeypatch):
    monkeypatch.setattr("app.services.trade_plan_service.load_app_settings", lambda: _settings())
    other = new_sleeve(session, cash=50_000.0)

    response = trade_plan_service.generate_trade_plan(
        "AAPL", other.starting_cash, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session, sleeve=other
    )

    assert response.status == "executed"
    record = session.get(TradePlanRecord, response.id)
    assert record.sleeve_id == other.id
    position = session.exec(select(PaperPosition).where(PaperPosition.trade_plan_id == response.id)).one()
    assert position.sleeve_id == other.id
    core_scope = compute_portfolio_stats(session, FakeUptrendDataProvider(), CASH)
    assert core_scope.active_positions == 0  # core did not move


def test_regenerating_for_one_sleeve_does_not_discard_another_sleeves_pending_plan(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False),
    )
    other = new_sleeve(session)
    first = trade_plan_service.generate_trade_plan("AAPL", CASH, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session)
    second = trade_plan_service.generate_trade_plan(
        "AAPL", 50_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session, sleeve=other
    )
    assert session.get(TradePlanRecord, first.id).status == "pending"
    assert session.get(TradePlanRecord, second.id).status == "pending"
    third = trade_plan_service.generate_trade_plan("AAPL", CASH, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session)
    assert session.get(TradePlanRecord, first.id).status == "discarded"  # same sleeve: superseded
    assert session.get(TradePlanRecord, second.id).status == "pending"
    assert session.get(TradePlanRecord, third.id).status == "pending"


def test_an_off_hours_plan_is_redone_for_its_own_sleeve(session, monkeypatch):
    from app.services.deferred_evaluation_service import queue_market_open_redo

    other = new_sleeve(session)
    now = datetime(2026, 9, 27, 12, 0)  # a Sunday
    core_redo = queue_market_open_redo(session, "AAPL", source="manual", reason="weekend", now=now)
    other_redo = queue_market_open_redo(session, "AAPL", source="manual", reason="weekend", now=now, sleeve_id=other.id)
    assert core_redo.id != other_redo.id  # one pending redo per symbol PER SLEEVE
    assert core_redo.sleeve_id is None and other_redo.sleeve_id == other.id
    again = queue_market_open_redo(session, "AAPL", source="manual", reason="weekend", now=now, sleeve_id=other.id)
    assert again.id == other_redo.id


# ------------------------------------------------------------------- endpoints


class DbProvider(QuietProvider):
    pass


@pytest.fixture
def client(db):
    def _session():
        with Session(db) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_data_provider] = lambda: QuietProvider()
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(
        scan_universe_size=3, paper_starting_cash=CASH, slippage_bps=0.0, commission_per_trade=0.0
    )
    yield TestClient(app)  # never `with`: that runs the lifespan against the real DB
    for dependency in (get_session, get_data_provider, get_app_settings):
        app.dependency_overrides.pop(dependency, None)


def _count(db, model) -> int:
    with Session(db) as s:
        return len(s.exec(select(model)).all())


def test_sleeve_endpoints_create_list_patch_delete(client, db):
    listed = client.get("/api/sleeves").json()
    assert [s["key"] for s in listed] == ["core"] and listed[0]["is_core"]
    assert listed[0]["stats"]["total_trades"] == 0

    created = client.post("/api/sleeves", json={"name": "Momentum", "style": "momentum", "starting_cash": 25000})
    assert created.status_code == 201
    key = created.json()["key"]
    assert client.post("/api/sleeves", json={"name": "momentum", "style": "x"}).status_code == 409
    assert client.post("/api/sleeves", json={"name": "Z", "style": "x", "starting_cash": -5}).status_code == 422

    patched = client.patch(f"/api/sleeves/{key}", json={"name": "Momentum 2", "enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False
    assert client.patch("/api/sleeves/core", json={"enabled": False}).status_code == 400
    assert client.patch("/api/sleeves/nope", json={"name": "x"}).status_code == 404

    rows = client.get("/api/sleeves").json()
    assert [s["key"] for s in rows] == ["core", key]
    assert rows[1]["stats"]["starting_cash"] == 25000  # the sleeve's own starting cash
    assert client.delete(f"/api/sleeves/{key}").status_code == 204
    assert [s["key"] for s in client.get("/api/sleeves").json()] == ["core"]


def _open_via_api(client, db, symbol, sleeve_key):
    """Generates nothing: writes a pending plan on the sleeve, then executes it
    through POST /api/portfolio/positions (the manual Execute path)."""
    with Session(db) as s:
        sleeve_id = None if sleeve_key == "core" else get_sleeve(s, sleeve_key).id
        pending = plan(s, symbol, sleeve_id=sleeve_id)
    response = client.post("/api/portfolio/positions", json={"trade_plan_id": pending.id})
    assert response.status_code == 200, response.text
    return response.json()


def test_execute_opens_in_the_plans_sleeve_and_gets_are_scoped(client, db):
    key = client.post("/api/sleeves", json={"name": "Momentum", "style": "m", "starting_cash": 50000}).json()["key"]
    core_pos = _open_via_api(client, db, "AAPL", "core")
    other_pos = _open_via_api(client, db, "AAPL", key)  # the same symbol, a different sleeve
    assert (core_pos["sleeve_key"], other_pos["sleeve_key"]) == ("core", key)

    core_list = client.get("/api/portfolio/positions").json()
    other_list = client.get(f"/api/portfolio/positions?sleeve={key}").json()
    all_list = client.get("/api/portfolio/positions?sleeve=all").json()
    assert [p["id"] for p in core_list] == [core_pos["id"]]
    assert [p["id"] for p in other_list] == [other_pos["id"]]
    assert {p["id"] for p in all_list} == {core_pos["id"], other_pos["id"]}
    assert {p["sleeve_key"] for p in all_list} == {"core", key}

    assert client.get("/api/portfolio/stats").json()["current_cash"] == CASH - 1000
    assert client.get(f"/api/portfolio/stats?sleeve={key}").json()["current_cash"] == 50_000 - 1000
    both = client.get("/api/portfolio/stats?sleeve=all").json()
    assert both["active_positions"] == 2 and both["starting_cash"] == CASH + 50_000

    assert len(client.get("/api/portfolio/equity-curve").json()) == 1
    assert len(client.get(f"/api/portfolio/equity-curve?sleeve={key}").json()) == 1
    combined = client.get("/api/portfolio/equity-curve?sleeve=all")
    assert combined.status_code == 400  # no invented combined curve
    for path in ("positions", "stats", "equity-curve"):
        assert client.get(f"/api/portfolio/{path}?sleeve=nope").status_code == 404

    closed = client.post(f"/api/portfolio/positions/{other_pos['id']}/close", json={"reason": "manual"})
    assert closed.status_code == 200
    assert client.get(f"/api/portfolio/stats?sleeve={key}").json()["total_trades"] == 1
    assert client.get("/api/portfolio/stats").json()["total_trades"] == 0  # core did not move


def test_executing_a_plan_for_a_disabled_sleeve_is_refused(client, db):
    key = client.post("/api/sleeves", json={"name": "Off", "style": "m"}).json()["key"]
    client.patch(f"/api/sleeves/{key}", json={"enabled": False})
    with Session(db) as s:
        pending = plan(s, "AAPL", sleeve_id=get_sleeve(s, key).id)
    response = client.post("/api/portfolio/positions", json={"trade_plan_id": pending.id})
    assert response.status_code == 400 and "disabled" in response.json()["detail"]


def test_gets_never_write_equity_points_or_sleeves(client, db):
    key = client.post("/api/sleeves", json={"name": "Momentum", "style": "m"}).json()["key"]
    _open_via_api(client, db, "AAPL", key)
    before = _count(db, EquitySnapshot), _count(db, Sleeve), _count(db, AccountState)
    for _ in range(2):
        for path in ("positions", "stats", "equity-curve"):
            for sleeve in ("", f"?sleeve={key}", "?sleeve=core"):
                assert client.get(f"/api/portfolio/{path}{sleeve}").status_code == 200
        assert client.get("/api/portfolio/positions?sleeve=all").status_code == 200
        assert client.get("/api/portfolio/stats?sleeve=all").status_code == 200
        assert client.get("/api/sleeves").status_code == 200
    assert (_count(db, EquitySnapshot), _count(db, Sleeve), _count(db, AccountState)) == before


def test_reset_is_per_sleeve(client, db):
    key = client.post("/api/sleeves", json={"name": "Momentum", "style": "m", "starting_cash": 50000}).json()["key"]
    _open_via_api(client, db, "AAPL", "core")
    _open_via_api(client, db, "MSFT", key)

    reset = client.post(f"/api/portfolio/reset?sleeve={key}")
    assert reset.status_code == 200
    assert reset.json()["active_positions"] == 0 and reset.json()["current_cash"] == 50_000
    assert client.get("/api/portfolio/stats").json()["active_positions"] == 1  # core untouched
    assert client.get(f"/api/portfolio/equity-curve?sleeve={key}").json() == []

    assert client.post("/api/portfolio/reset?sleeve=all").status_code == 400  # needs confirm
    assert client.get("/api/portfolio/stats").json()["active_positions"] == 1
    everything = client.post("/api/portfolio/reset?sleeve=all&confirm=true")
    assert everything.status_code == 200
    assert client.get("/api/portfolio/positions?sleeve=all").json() == []
    assert client.get("/api/portfolio/stats").json()["current_cash"] == CASH
    assert [s["key"] for s in client.get("/api/sleeves").json()] == ["core", key]  # the sleeves stay

    assert client.post("/api/portfolio/reset?sleeve=nope").status_code == 404
    assert client.post("/api/portfolio/reset").status_code == 200  # the default is core, as before


def test_dashboard_stat_cards_follow_the_sleeve(client, db):
    key = client.post("/api/sleeves", json={"name": "Momentum", "style": "m", "starting_cash": 50000}).json()["key"]
    _open_via_api(client, db, "MSFT", key)
    assert client.get("/api/dashboard/summary").json()["stats"]["starting_cash"] == CASH
    assert client.get(f"/api/dashboard/summary?sleeve={key}").json()["stats"]["active_positions"] == 1
    assert client.get("/api/dashboard/summary?sleeve=nope").status_code == 404


def test_the_generate_endpoint_takes_a_sleeve(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False),
    )
    from app.api.deps import get_llm_provider

    app.dependency_overrides[get_data_provider] = lambda: FakeUptrendDataProvider()
    app.dependency_overrides[get_llm_provider] = lambda: NullLLMProvider()
    try:
        key = client.post("/api/sleeves", json={"name": "Momentum", "style": "m", "starting_cash": 50000}).json()["key"]
        made = client.post("/api/trade-plans/generate", json={"symbol": "AAPL", "sleeve": key})
        assert made.status_code == 200 and made.json()["sleeve_key"] == key
        assert client.get(f"/api/trade-plans/{made.json()['id']}").json()["sleeve_key"] == key
        default = client.post("/api/trade-plans/generate", json={"symbol": "MSFT"})
        assert default.json()["sleeve_key"] == "core"
        assert client.post("/api/trade-plans/generate", json={"symbol": "AAPL", "sleeve": "nope"}).status_code == 404
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_build_sleeve_engine_seeds_a_non_core_account_with_the_sleeves_own_cash(session):
    other = new_sleeve(session, cash=12_345.0)
    settings = AppSettings(paper_starting_cash=999_999.0)
    assert build_sleeve_engine(session, QuietProvider(), settings, other).get_account_state().starting_cash == 12_345.0
    assert build_sleeve_engine(session, QuietProvider(), settings).get_account_state().starting_cash == 999_999.0

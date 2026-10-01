"""GET routes that run the exit check must never write to the equity curve.

Found live on 2026-09-28: GET /api/dashboard/summary called mark_to_market()
with its default snapshot=True, so every Dashboard load appended an
EquitySnapshot row and the equity curve recorded how often the page was
opened (29 points, most of them seconds apart). The 2026-09-12 fix had given
/api/portfolio/positions and /stats snapshot=False but missed this route, and
its only test sat at the engine level, where the route's own call is never
exercised. These tests go through the real routes instead.

Positions are written straight into the DB rather than through
open_position(), and every bar date is fixed in the past: the exit check only
compares bar dates with a position's opened_at, never with the wall clock,
so these tests behave the same at any time of day, on any weekday, in or out
of the market session.
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
from app.main import app
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition

STARTING_CASH = 100_000.0
SHARES = 10
ENTRY = 100.0
STOP = 90.0
TP1 = 120.0

FIRST_BAR = datetime(2026, 1, 5)
BAR_COUNT = 90
ENTRY_BAR = 80  # the position opens after this bar's close
DIP_BAR = 84  # a later bar trades through the stop, then the price recovers
OPENED_AT = FIRST_BAR + timedelta(days=ENTRY_BAR, hours=20)

# The GET routes that run the exit check. /equity-curve only reads the table.
MARKING_GET_ROUTES = ["/api/dashboard/summary", "/api/portfolio/positions", "/api/portfolio/stats"]


class DailyBarsProvider:
    """Serves the same quiet daily bars (range 99-101, never near the stop or
    TP1) for every symbol, except that symbols in `stop_touched` get one
    post-entry bar that trades through the stop and then recovers. The latest
    bar is back at 100, so only a walk over every bar since entry sees it."""

    name = "fake"

    def __init__(self, stop_touched: tuple[str, ...] = ()):
        self._stop_touched = set(stop_touched)

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        rows = []
        for i in range(BAR_COUNT):
            o, h, low, c = ENTRY, ENTRY + 1, ENTRY - 1, ENTRY
            if symbol in self._stop_touched and i == DIP_BAR:
                o, h, low, c = 99.0, 99.5, 85.0, 97.0
            rows.append(
                {"date": FIRST_BAR + timedelta(days=i), "open": o, "high": h, "low": low, "close": c, "volume": 1e6}
            )
        return pd.DataFrame(rows)

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=ENTRY, change_pct_24h=0.0, volume=1e6, avg_volume_20d=1e6)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # The state an open_position() call for each symbol would leave:
        # cash debited for both longs and the one snapshot opening wrote.
        session.add(AccountState(starting_cash=STARTING_CASH, current_cash=STARTING_CASH - 2 * SHARES * ENTRY))
        for symbol in ("AAPL", "MSFT"):
            session.add(
                PaperPosition(
                    symbol=symbol,
                    direction="long",
                    entry_price=ENTRY,
                    stop_loss=STOP,
                    tp1=TP1,
                    tp2=130.0,
                    shares=SHARES,
                    opened_at=OPENED_AT,
                )
            )
        session.add(EquitySnapshot(equity_value=STARTING_CASH, cash_balance=STARTING_CASH - 2 * SHARES * ENTRY))
        session.commit()
    return engine


def _client(db, provider: DailyBarsProvider) -> TestClient:
    def _get_session_override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _get_session_override
    app.dependency_overrides[get_data_provider] = lambda: provider
    # A small scan universe keeps the dashboard's own scan quick, and a fresh
    # AppSettings keeps the user's runtime/settings.json out of the test.
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(scan_universe_size=3)
    # Never `with TestClient(app)`: that runs the lifespan, which creates
    # tables in the real DB and starts the scheduler.
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    for dependency in (get_session, get_data_provider, get_app_settings):
        app.dependency_overrides.pop(dependency, None)


def _snapshot_count(db) -> int:
    with Session(db) as session:
        return len(session.exec(select(EquitySnapshot)).all())


def _positions(db) -> dict[str, PaperPosition]:
    with Session(db) as session:
        return {p.symbol: p for p in session.exec(select(PaperPosition)).all()}


def _cash(db) -> float:
    with Session(db) as session:
        return session.exec(select(AccountState)).one().current_cash


@pytest.mark.parametrize("route", MARKING_GET_ROUTES)
def test_repeated_loads_add_no_equity_snapshots(db, route):
    """THE regression (for the Dashboard; the Portfolio routes had the same
    bug before 2026-09-12). Nothing is due to exit, so repeated loads must
    leave the database exactly as they found it."""
    client = _client(db, DailyBarsProvider())

    for _ in range(3):
        assert client.get(route).status_code == 200

    assert _snapshot_count(db) == 1
    assert {p.status for p in _positions(db).values()} == {"open"}
    assert _cash(db) == pytest.approx(STARTING_CASH - 2 * SHARES * ENTRY)


def test_dashboard_load_still_closes_a_position_whose_stop_was_touched(db):
    """snapshot=False drops only the equity-curve write. The exit check itself
    still runs on a Dashboard load, so a stop touched on a bar since the last
    scheduled check (the AAPL dip, since recovered) closes the position right
    away. Repeat loads change nothing further: no second close, no second
    cash credit, no snapshot."""
    client = _client(db, DailyBarsProvider(stop_touched=("AAPL",)))

    first = client.get("/api/dashboard/summary")

    assert first.status_code == 200
    stats = first.json()["stats"]
    assert stats["total_trades"] == 1
    assert stats["active_positions"] == 1
    assert stats["win_rate"] == 0.0

    positions = _positions(db)
    aapl = positions["AAPL"]
    assert aapl.status == "closed"
    assert aapl.close_reason == "stop_hit"
    assert aapl.close_price < STOP  # the stop is a market order: it pays slippage
    assert aapl.realized_pnl < 0
    assert positions["MSFT"].status == "open"
    assert _snapshot_count(db) == 1

    cash_after_close = _cash(db)
    assert cash_after_close == pytest.approx(STARTING_CASH - 2 * SHARES * ENTRY + SHARES * aapl.close_price)

    for _ in range(2):
        assert client.get("/api/dashboard/summary").status_code == 200

    again = _positions(db)["AAPL"]
    assert (again.closed_at, again.close_price, again.realized_pnl) == (
        aapl.closed_at,
        aapl.close_price,
        aapl.realized_pnl,
    )
    assert _cash(db) == pytest.approx(cash_after_close)
    assert _snapshot_count(db) == 1

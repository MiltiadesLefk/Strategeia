from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_session
from app.main import app
from app.portfolio.models import TradePlanRecord


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def _get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _get_session_override
    with Session(engine) as session:
        yield TestClient(app), session
    app.dependency_overrides.pop(get_session, None)


def test_list_trade_plans_does_not_500_on_a_no_trade_record(client):
    """Regression: trade_plan_to_response() used to unconditionally compute
    potential_gain from suggested_shares/tp1/entry, which are all None on a
    no_trade record (see trade_plan_service.py) — crashed the whole list
    endpoint with a TypeError the moment one no-trade decision existed."""
    test_client, session = client
    session.add(
        TradePlanRecord(
            symbol="BTC-USD",
            status="no_trade",
            reason="No clear trend (EMA20/EMA50 not aligned).",
            confidence_score=26,
            technical_score=1,
            fundamental_score=0,
            news_score=0,
            signal_reasons="Neutral trend with weak momentum",
        )
    )
    session.commit()

    resp = test_client.get("/api/trade-plans")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "no_trade"
    assert body[0]["direction"] is None
    assert body[0]["potential_gain"] is None
    assert body[0]["reason"] == "No clear trend (EMA20/EMA50 not aligned)."

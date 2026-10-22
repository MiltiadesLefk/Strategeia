"""Rate all: a market-wide ticker with a bullish headline is rated and joins the universe only when it reaches the bar."""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.analysis.quality_gate import GateCheck, GateResult
from app.config import AppSettings
from app.services import rating_service as rs


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_only_bullish_tickers_are_rated_and_only_passing_ones_join(session, monkeypatch):
    cards = {"UP": [{"sentiment": "positive"}], "DOWN": [{"sentiment": "negative"}], "LOW": [{"sentiment": "positive"}]}
    monkeypatch.setattr("app.analysis.news_card_scoring.recent_cards", lambda s, t: cards[t])
    rated = []

    def fake_rate(dp, symbol, bar, session=None):
        rated.append(symbol)
        ok = symbol == "UP"
        return GateResult(ok, [GateCheck("rating", ok, "n")])

    adopted = []
    monkeypatch.setattr(rs, "rate_stock", fake_rate)
    monkeypatch.setattr("app.services.market_find_service.adopt_found_stock",
                        lambda session, settings, dp, symbol, lane, detail, gate: adopted.append(symbol) or True)
    added = rs.rate_market_finds(AppSettings(), object(), session, "r", "manual", {"UP": "a", "DOWN": "b", "LOW": "c"})
    assert rated == ["UP", "LOW"] and adopted == ["UP"] and added == ["UP"]

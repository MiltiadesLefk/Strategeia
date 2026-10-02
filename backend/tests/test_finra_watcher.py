"""The FINRA short-volume watcher and the start-up registration of the watchers. No network."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import AppSettings
from app.data_providers.base import DataProviderError
from app.knowledge import FactKind, as_of, facts_known_as_of
from app.watchers import finra_watcher, registry
from app.watchers.base import WatcherContext
from app.watchers.finra_watcher import FinraWatcher, register_finra_watcher
from tests.test_finra_short_volume import FakeFinra


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


NOW = datetime(2026, 10, 1, 23, 0)


def _ctx(session):
    return WatcherContext(session=session, settings=AppSettings(), now=NOW)


def test_poll_stores_published_days_and_never_alerts(session):
    fake = FakeFinra({date(2026, 9, 30): {"AAPL": (400.0, 1000.0)}})
    watcher = FinraWatcher(provider=fake, symbols=lambda c: ["AAPL"])
    assert watcher.poll(_ctx(session)) == []
    assert watcher.last_result.facts_created == 1
    with as_of(datetime(2026, 10, 2)):
        assert len(facts_known_as_of(session, FactKind.FINRA_SHORT_VOLUME, symbol="AAPL")) == 1
    # A second poll finds the day stored and creates nothing new.
    assert watcher.poll(_ctx(session)) == []
    assert watcher.last_result.facts_created == 0


def test_no_published_file_yet_is_not_an_error(session):
    watcher = FinraWatcher(provider=FakeFinra({}), symbols=lambda c: ["AAPL"])
    assert watcher.poll(_ctx(session)) == []


def test_total_download_failure_raises(session):
    class Broken(FakeFinra):
        def get_short_volume(self, symbols, day):
            raise DataProviderError("boom")

    watcher = FinraWatcher(provider=Broken(), symbols=lambda c: ["AAPL"])
    with pytest.raises(DataProviderError):
        watcher.poll(_ctx(session))


def test_register_is_idempotent():
    registry.unregister_watcher(finra_watcher.WATCHER_NAME)
    try:
        register_finra_watcher()
        register_finra_watcher()
        assert registry.get_watcher("finra") is not None
    finally:
        registry.unregister_watcher(finra_watcher.WATCHER_NAME)


def test_lifespan_registers_every_watcher():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        names = {w.name for w in registry.all_watchers()}
    assert {"sec_filings", "fed", "finra"} <= names
    assert any("house" in n for n in names)
    assert any("fund" in n for n in names)
    assert any("post" in n for n in names)

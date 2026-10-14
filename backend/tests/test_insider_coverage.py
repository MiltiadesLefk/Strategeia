from datetime import date, timedelta

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.data_providers import sec_form4
from app.data_providers.sec_form4 import BackfillReport
from app.services import insider_coverage_service as svc
from app.watchers.models import InsiderCoverage

TODAY = date(2026, 10, 14)


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _patch(monkeypatch, calls, fail=()):
    def fake(session, symbols, since, until=None, **kw):
        calls.append((symbols[0], since))
        return BackfillReport(errors=["boom"] if symbols[0] in fail else [])

    monkeypatch.setattr(svc, "backfill_insider_trades", fake)


def test_new_symbol_gets_a_year_then_only_a_top_up(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls)
    with _session() as s:
        assert svc.run_insider_coverage_tick(s, ["SOFI", "BTC-USD", "^VIX"], today=TODAY) == 1
        assert calls == [("SOFI", TODAY - timedelta(days=365))]
        row = s.get(InsiderCoverage, "SOFI")
        assert (row.covered_from, row.covered_through) == (TODAY - timedelta(days=365), TODAY)

        calls.clear()
        assert svc.run_insider_coverage_tick(s, ["SOFI"], today=TODAY) == 0  # nothing new today
        assert svc.run_insider_coverage_tick(s, ["SOFI"], today=TODAY + timedelta(days=5)) == 1
        assert calls == [("SOFI", TODAY - timedelta(days=svc.TOP_UP_OVERLAP_DAYS))]


def test_failed_load_is_retried_and_does_not_mark_coverage(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls, fail={"AAPL"})
    with _session() as s:
        assert svc.run_insider_coverage_tick(s, ["AAPL", "SOFI"], today=TODAY) == 1  # SOFI still done
        assert s.get(InsiderCoverage, "AAPL") is None

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.config import AppSettings
from app.llm_providers.null_provider import NullLLMProvider
from app.main import app
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services import trade_plan_service
from app.strategy.models import StrategyVersion
from app.strategy.service import current_strategy_version, strategy_history, strategy_version_for_settings
from app.strategy.snapshot import (
    DECISION_SETTINGS,
    OVERLAY_RULE_CONSTANTS,
    OVERLAY_SETTINGS,
    RULE_CONSTANTS,
    build_snapshot,
    describe_changes,
    fingerprint,
    normalize,
)
from test_trade_plan_service import FakeFlatDataProvider, FakeUptrendDataProvider

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def fp(**overrides) -> str:
    return fingerprint(build_snapshot(AppSettings(**overrides)))


# ------------------------------------------------------------ fingerprint


def test_fingerprint_is_stable_for_equal_settings():
    assert fp() == fp()
    assert fp(min_confidence_for_trade=38) == fp(min_confidence_for_trade=38)


def test_fingerprint_is_stable_across_processes(tmp_path):
    """Hash randomisation and dict/set ordering must not leak into it: two
    fresh interpreters with different PYTHONHASHSEED agree with this one."""
    code = (
        "from app.config import AppSettings\n"
        "from app.strategy.snapshot import build_snapshot, fingerprint\n"
        "print(fingerprint(build_snapshot(AppSettings())))\n"
    )
    env_base = {
        **os.environ,
        "ALLOW_UNAUTHENTICATED_API": "true",
        "DB_PATH": str(tmp_path / "x.db"),
        "SETTINGS_PATH": str(tmp_path / "settings.json"),
    }
    outputs = []
    for seed in ("1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_DIR,
            env={**env_base, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip().splitlines()[-1])
    assert outputs[0] == outputs[1] == fp()


def test_float_noise_and_integral_floats_do_not_split_a_version():
    assert normalize(0.1 + 0.2) == 0.3
    assert normalize(25.0) == 25 and isinstance(normalize(25.0), int)
    assert fp(default_risk_pct=1.0) == fp(default_risk_pct=1)
    assert fp(max_position_pct_of_adv=0.1 + 0.2) == fp(max_position_pct_of_adv=0.3)


def test_normalize_refuses_what_it_cannot_represent():
    with pytest.raises(TypeError):
        normalize(object())


# ------------------------------------------------------- what is in and out


@pytest.mark.parametrize(
    "name, value",
    [
        ("min_confidence_for_trade", 38),
        ("default_risk_pct", 0.5),
        ("slippage_bps", 12.0),
        ("commission_per_trade", 1.0),
        ("max_concurrent_positions", 3),
        ("max_positions_per_sector", 1),
        ("max_position_pct_of_adv", 0.5),
        ("max_holding_days", 10),
        ("auto_execute_trade_plans", False),
        ("ai_trading_overlay_enabled", True),
    ],
)
def test_each_decision_relevant_setting_changes_the_fingerprint(name, value):
    assert name in DECISION_SETTINGS
    assert fp(**{name: value}) != fp()


@pytest.mark.parametrize(
    "name, value",
    [
        ("ai_overlay_scores_confidence", False),
        ("ai_overlay_objection_action", "hold"),
        ("llm_provider", "gemini"),
        ("openai_model", "gpt-5"),
    ],
)
def test_overlay_settings_change_the_fingerprint_while_the_overlay_is_on(name, value):
    base_settings = dict(ai_trading_overlay_enabled=True, llm_provider="openai")
    changed = {**base_settings, name: value}
    assert fp(**changed) != fp(**base_settings)


def test_the_overlays_model_is_the_one_for_the_active_provider():
    on = dict(ai_trading_overlay_enabled=True)
    # Only the model of the provider in use counts.
    assert fp(**on, llm_provider="openai", gemini_model="other") == fp(**on, llm_provider="openai")
    assert fp(**on, llm_provider="claude_code_cli", claude_cli_model="opus") != fp(**on, llm_provider="claude_code_cli")
    assert fp(**on, llm_provider="openrouter", openrouter_model="x/y") != fp(**on, llm_provider="openrouter")
    assert fp(**on, llm_provider="gemini", gemini_model="g2") != fp(**on, llm_provider="gemini")
    assert fp(**on, llm_provider="orcarouter", orcarouter_model="o2") != fp(**on, llm_provider="orcarouter")


@pytest.mark.parametrize(
    "overrides",
    [
        # narrative only: the overlay is off, so the model cannot change a decision
        dict(llm_provider="openai"),
        dict(llm_provider="claude_code_cli", claude_cli_model="opus"),
        dict(openai_model="gpt-5"),
        dict(openrouter_model="x/y"),
        # the overlay's sub-settings do nothing while it is off
        dict(ai_overlay_scores_confidence=False),
        dict(ai_overlay_objection_action="none"),
        # secrets and keys
        dict(openai_api_key="sk-secret", openrouter_api_key="k", gemini_api_key="k", orcarouter_api_key="k"),
        dict(finnhub_api_key="k"),
        dict(telegram_bot_token="tok", telegram_chat_id="123"),
        # which symbols / when / where the data comes from, not the rule
        dict(scan_universe_size=200),
        dict(auto_scan_enabled=True),
        dict(finnhub_enabled=True),
        # account size and scheduler cadence are not rules
        dict(paper_starting_cash=250_000.0),
        dict(mark_to_market_interval_minutes=5),
    ],
)
def test_narrative_secret_and_non_rule_settings_never_change_the_fingerprint(overrides):
    assert fp(**overrides) == fp()


def test_narrative_model_change_does_not_bump_even_with_the_overlay_on_for_another_provider():
    on = dict(ai_trading_overlay_enabled=True, llm_provider="openai")
    assert fp(**on, claude_cli_model="opus", gemini_model="g2") == fp(**on)


def test_the_snapshot_holds_no_secret_and_overlay_group_only_while_on():
    settings = AppSettings(openai_api_key="sk-very-secret", telegram_bot_token="tok-secret", finnhub_api_key="fh-secret")
    text = json.dumps(build_snapshot(settings))
    for secret in ("sk-very-secret", "tok-secret", "fh-secret"):
        assert secret not in text
    assert "overlay" not in build_snapshot(AppSettings())
    assert "overlay" in build_snapshot(AppSettings(ai_trading_overlay_enabled=True))


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, tuple):
        return (*value, 9.9)
    if isinstance(value, list):
        return [*value, "extra"]
    if isinstance(value, set):
        return {*value, "promoted_signal"}
    raise AssertionError(f"extend _changed for {type(value)}")


def _all_rule_constants():
    for table in (RULE_CONSTANTS, OVERLAY_RULE_CONSTANTS):
        for module_path, names in table.items():
            for name in names:
                yield module_path, name


@pytest.mark.parametrize("module_path, name", list(_all_rule_constants()))
def test_a_code_side_constant_change_bumps_the_fingerprint(monkeypatch, module_path, name):
    module = importlib.import_module(module_path)
    settings = AppSettings(ai_trading_overlay_enabled=True)  # on, so the overlay's constants count too
    before = fingerprint(build_snapshot(settings))
    monkeypatch.setattr(module, name, _changed(getattr(module, name)))
    assert fingerprint(build_snapshot(settings)) != before


def test_the_overlays_constants_only_count_while_the_overlay_is_on(monkeypatch):
    from app.analysis import ai_overlay_scoring

    before = fp()
    monkeypatch.setattr(ai_overlay_scoring, "AI_OVERLAY_STRONG_CONFIDENCE", 99)
    assert fp() == before


def test_the_key_stop_and_exit_constants_named_in_the_brief_are_covered():
    covered = {name for _, name in _all_rule_constants()}
    for name in (
        "ATR_STOP_MULTIPLE", "STOP_BUFFER_PCT", "FALLBACK_STOP_PCT", "MIN_NET_BUY_VALUE",
        "VIX_ELEVATED_THRESHOLD", "CONFIDENCE_CEILING", "MAX_ENTRY_DRIFT_PCT", "EXIT_SCAN_PERIOD",
        "MAX_SCORE_FOR_CONFIDENCE", "AI_OVERLAY_SCORE_CAP",
    ):
        assert name in covered


def test_a_constant_that_disappears_fails_loudly(monkeypatch):
    from app.services import trade_plan_service as tps

    monkeypatch.delattr(tps, "ATR_STOP_MULTIPLE")
    with pytest.raises(AttributeError):
        build_snapshot(AppSettings())


def test_the_rules_revision_escape_hatch_is_part_of_the_fingerprint(monkeypatch):
    from app.strategy import snapshot

    before = fp()
    monkeypatch.setattr(snapshot, "RULES_REVISION", snapshot.RULES_REVISION + 1)
    assert fp() != before


# ------------------------------------------------------------------ numbering


def test_numbers_are_sequential_and_a_returning_fingerprint_reuses_its_row(session):
    v1 = current_strategy_version(session, AppSettings())
    assert v1.number == 1
    assert current_strategy_version(session, AppSettings()).id == v1.id

    v2 = current_strategy_version(session, AppSettings(min_confidence_for_trade=38))
    assert v2.number == 2

    # Changing back is version 1 again, not a new number.
    assert current_strategy_version(session, AppSettings()).number == 1
    v3 = current_strategy_version(session, AppSettings(max_holding_days=10))
    assert v3.number == 3
    assert len(session.exec(select(StrategyVersion)).all()) == 3


def test_the_snapshot_is_stored_in_full_and_the_fingerprint_matches_it(session):
    row = current_strategy_version(session, AppSettings(min_confidence_for_trade=38))
    stored = json.loads(row.settings_snapshot)
    assert stored["settings"]["min_confidence_for_trade"] == 38
    assert stored["rules"]["trade_plan_service.ATR_STOP_MULTIPLE"] == trade_plan_service.ATR_STOP_MULTIPLE
    assert fingerprint(stored) == row.fingerprint
    assert row.label is None


def test_a_code_change_makes_the_next_plan_a_new_version(session, monkeypatch):
    first = current_strategy_version(session, AppSettings())
    monkeypatch.setattr(trade_plan_service, "ATR_STOP_MULTIPLE", 2.5)
    second = current_strategy_version(session, AppSettings())
    assert (first.number, second.number) == (1, 2)
    assert "rule: atr stop multiple 1.5 -> 2.5" in describe_changes(
        json.loads(first.settings_snapshot), json.loads(second.settings_snapshot)
    )


def test_checking_the_current_version_without_a_plan_creates_nothing(session):
    assert strategy_version_for_settings(session, AppSettings()) is None
    assert session.exec(select(StrategyVersion)).all() == []
    current_strategy_version(session, AppSettings())
    assert strategy_version_for_settings(session, AppSettings()) == 1


def test_creation_is_safe_under_concurrency(tmp_path):
    """Two writers asking for the same new version at the same moment end up
    with one row and one number."""
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    barrier = threading.Barrier(2)
    numbers: list[int] = []
    errors: list[BaseException] = []

    def worker():
        try:
            with Session(engine) as s:
                barrier.wait(timeout=10)
                numbers.append(current_strategy_version(s, AppSettings()).number)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert numbers == [1, 1]
    with Session(engine) as s:
        assert len(s.exec(select(StrategyVersion)).all()) == 1


def test_a_lost_insert_race_re_reads_the_winners_row(session, monkeypatch):
    """Simulates the cross-process case the in-process lock cannot cover: the
    first lookup misses, another writer inserts the same fingerprint, and our
    insert hits the unique constraint."""
    from app.strategy import service

    snapshot = build_snapshot(AppSettings())
    real_lookup = service._by_fingerprint
    calls = {"n": 0}

    def racing_lookup(sess, fingerprint_value):
        calls["n"] += 1
        if calls["n"] <= 2:  # the unlocked check and the first locked check both miss
            return None
        return real_lookup(sess, fingerprint_value)

    monkeypatch.setattr(service, "_by_fingerprint", racing_lookup)
    with Session(session.get_bind()) as other:
        other.add(StrategyVersion(number=1, fingerprint=fingerprint(snapshot), settings_snapshot="{}"))
        other.commit()

    row = service.current_strategy_version(session, AppSettings())
    assert row.number == 1
    assert len(session.exec(select(StrategyVersion)).all()) == 1


# ------------------------------------------------------------------ the diff


def test_diff_reads_in_plain_words():
    old = build_snapshot(AppSettings())
    new = build_snapshot(AppSettings(min_confidence_for_trade=38, auto_execute_trade_plans=False))
    changes = describe_changes(old, new)
    assert "min confidence 30 -> 38" in changes
    assert "auto-execute on -> off" in changes
    assert len(changes) == 2


def test_diff_for_the_first_version_is_empty_and_for_equal_snapshots_too():
    snap = build_snapshot(AppSettings())
    assert describe_changes(None, snap) == []
    assert describe_changes(snap, snap) == []


def test_diff_when_the_overlay_is_switched_on_and_off():
    off = build_snapshot(AppSettings())
    on = build_snapshot(AppSettings(ai_trading_overlay_enabled=True, llm_provider="openai"))
    switched_on = describe_changes(off, on)
    assert "AI overlay off -> on" in switched_on
    assert "overlay LLM provider set to openai" in switched_on
    assert "overlay LLM model set to gpt-4o-mini" in switched_on
    assert not any("score cap" in line for line in switched_on)  # constants arriving with the overlay are not listed
    assert describe_changes(on, off) == ["AI overlay on -> off"]


# ---------------------------------------------------- plans carry the number


def _generate(session, monkeypatch, provider, **settings_overrides):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", **settings_overrides),
    )
    return trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, provider, NullLLMProvider(), session)


def test_a_tradeable_plan_carries_the_version_number(session, monkeypatch):
    response = _generate(session, monkeypatch, FakeUptrendDataProvider())
    assert response.status in ("pending", "executed")
    assert response.strategy_version == 1
    assert session.get(TradePlanRecord, response.id).strategy_version == 1


def test_a_no_trade_record_carries_the_version_number_too(session, monkeypatch):
    response = _generate(session, monkeypatch, FakeFlatDataProvider())
    assert response.status == "no_trade"
    assert response.strategy_version == 1
    assert session.get(TradePlanRecord, response.id).strategy_version == 1


def test_a_settings_change_between_plans_splits_them_into_two_versions(session, monkeypatch):
    first = _generate(session, monkeypatch, FakeUptrendDataProvider(), auto_execute_trade_plans=False)
    again = _generate(session, monkeypatch, FakeUptrendDataProvider(), auto_execute_trade_plans=False)
    changed = _generate(session, monkeypatch, FakeUptrendDataProvider(), auto_execute_trade_plans=False, min_confidence_for_trade=20)
    assert (first.strategy_version, again.strategy_version, changed.strategy_version) == (1, 1, 2)


def test_a_failed_evaluation_leaves_no_version_behind(session, monkeypatch):
    class Failing(FakeUptrendDataProvider):
        def get_quote(self, symbol):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _generate(session, monkeypatch, Failing())
    assert session.exec(select(StrategyVersion)).all() == []


# ------------------------------------------------------------------ endpoint


@pytest.fixture
def client(session):
    state = {"settings": AppSettings()}

    def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_app_settings] = lambda: state["settings"]
    yield TestClient(app), session, state
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_app_settings, None)


def test_versions_endpoint_on_an_empty_database_writes_nothing(client):
    test_client, session, _ = client
    body = test_client.get("/api/strategy/versions").json()
    assert body == {"versions": [], "unversioned_plans": 0, "current_number": None}
    assert session.exec(select(StrategyVersion)).all() == []


def test_versions_endpoint_lists_newest_first_with_changes_and_counts(client):
    test_client, session, state = client
    v1 = current_strategy_version(session, AppSettings())
    v2 = current_strategy_version(session, AppSettings(min_confidence_for_trade=38))
    session.add(TradePlanRecord(symbol="OLD", status="no_trade", confidence_score=10))  # before versioning
    session.add(TradePlanRecord(symbol="AAA", status="no_trade", confidence_score=10, strategy_version=v1.number))
    executed = TradePlanRecord(
        symbol="BBB", status="executed", direction="long", confidence_score=60, strategy_version=v2.number
    )
    second = TradePlanRecord(symbol="CCC", status="executed", direction="long", confidence_score=60, strategy_version=v2.number)
    session.add(executed)
    session.add(second)
    session.commit()
    for plan, status in ((executed, "closed"), (second, "open")):
        session.add(
            PaperPosition(
                trade_plan_id=plan.id, symbol=plan.symbol, direction="long", entry_price=10, stop_loss=9, tp1=12,
                tp2=14, shares=1, status=status,
            )
        )
    session.commit()
    state["settings"] = AppSettings(min_confidence_for_trade=38)

    body = test_client.get("/api/strategy/versions").json()

    assert [v["number"] for v in body["versions"]] == [2, 1]
    assert body["current_number"] == 2
    assert body["unversioned_plans"] == 1
    newest, oldest = body["versions"]
    assert newest["changes"] == ["min confidence 30 -> 38"]
    assert oldest["changes"] == []
    assert newest["settings_snapshot"]["settings"]["min_confidence_for_trade"] == 38
    assert (newest["plans"], newest["no_trades"], newest["positions_opened"], newest["closed_trades"]) == (2, 0, 2, 1)
    assert (oldest["plans"], oldest["no_trades"], oldest["positions_opened"], oldest["closed_trades"]) == (1, 1, 0, 0)
    assert newest["created_at"].endswith("Z")
    assert len(session.exec(select(StrategyVersion)).all()) == 2  # the GET created nothing


def test_current_number_is_none_after_a_settings_change_with_no_plan_yet(client):
    test_client, session, state = client
    current_strategy_version(session, AppSettings())
    state["settings"] = AppSettings(max_holding_days=10)
    body = test_client.get("/api/strategy/versions").json()
    assert body["current_number"] is None
    assert [v["number"] for v in body["versions"]] == [1]


def test_trade_plan_and_position_endpoints_expose_the_version(client):
    test_client, session, _ = client
    v = current_strategy_version(session, AppSettings())
    plan = TradePlanRecord(
        symbol="AAA", status="executed", direction="long", entry=10, stop=9, tp1=12, tp2=14, rr1=2, rr2=4,
        suggested_shares=1, confidence_score=60, strategy_version=v.number,
    )
    session.add(plan)
    session.commit()
    session.add(
        PaperPosition(
            trade_plan_id=plan.id, symbol="AAA", direction="long", entry_price=10, stop_loss=9, tp1=12, tp2=14,
            shares=1, status="closed", realized_pnl=1.0, realized_r=1.0,
        )
    )
    session.add(
        PaperPosition(
            trade_plan_id=None, symbol="ZZZ", direction="long", entry_price=10, stop_loss=9, tp1=12, tp2=14,
            shares=1, status="closed", realized_pnl=1.0, realized_r=1.0,
        )
    )
    session.commit()

    assert test_client.get(f"/api/trade-plans/{plan.id}").json()["strategy_version"] == 1
    from app.api.routers.portfolio import position_to_schema
    from app.strategy.service import plan_strategy_versions

    positions = session.exec(select(PaperPosition)).all()
    versions = plan_strategy_versions(session, [p.trade_plan_id for p in positions])
    by_symbol = {p.symbol: position_to_schema(p, versions).strategy_version for p in positions}
    assert by_symbol == {"AAA": 1, "ZZZ": None}


def test_history_helper_counts_match_the_endpoint_shape(session):
    v = current_strategy_version(session, AppSettings())
    session.add(TradePlanRecord(symbol="AAA", status="no_trade", confidence_score=1, strategy_version=v.number))
    session.commit()
    history = strategy_history(session, AppSettings())
    assert history.current_number == 1
    assert history.versions[0].plans == 1 and history.versions[0].no_trades == 1
    assert OVERLAY_SETTINGS  # the overlay group's settings are exported for the UI/docs


def test_an_existing_database_gains_the_table_and_the_column_and_keeps_old_plans_unversioned(monkeypatch, tmp_path):
    import app.database as database_module

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE tradeplanrecord (id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, "
            "confidence_score INTEGER NOT NULL, time_horizon VARCHAR NOT NULL, status VARCHAR NOT NULL, "
            "created_at DATETIME NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO tradeplanrecord (symbol, confidence_score, time_horizon, status, created_at) "
            "VALUES ('AAPL', 60, '1-4 weeks', 'pending', '2026-01-01 00:00:00')"
        )
        conn.commit()

    monkeypatch.setattr(database_module, "engine", engine)
    database_module.create_db_and_tables()

    with Session(engine) as s:
        old = s.exec(select(TradePlanRecord)).one()
        assert old.symbol == "AAPL" and old.strategy_version is None
        assert s.exec(select(StrategyVersion)).all() == []
        assert current_strategy_version(s, AppSettings()).number == 1

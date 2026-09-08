from __future__ import annotations

from sqlmodel import SQLModel, create_engine


def test_add_missing_columns_adds_new_nullable_fields(monkeypatch, tmp_path):
    """Simulates the real scenario: runtime.db was created by an older
    version of TradePlanRecord (before technical_score/fundamental_score/
    news_score/signal_reasons existed), then the model gains those fields.
    Without the migration helper this would 500 with 'no such column' the
    next time a trade plan is read or written."""
    import app.database as database_module
    from app.portfolio import models  # noqa: F401 - registers the *current* model on SQLModel.metadata

    db_path = tmp_path / "old_schema.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Hand-create the table as it looked before this turn's new columns —
    # a subset of TradePlanRecord's real columns.
    with engine.connect() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE tradeplanrecord (
                id INTEGER PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                entry FLOAT NOT NULL,
                stop FLOAT NOT NULL,
                tp1 FLOAT NOT NULL,
                tp2 FLOAT NOT NULL,
                rr1 FLOAT NOT NULL,
                rr2 FLOAT NOT NULL,
                suggested_shares INTEGER NOT NULL,
                account_risk_dollars FLOAT NOT NULL,
                confidence_score INTEGER NOT NULL,
                ai_take_text VARCHAR NOT NULL,
                ai_provider VARCHAR NOT NULL,
                time_horizon VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO tradeplanrecord (symbol, direction, entry, stop, tp1, tp2, rr1, rr2, "
            "suggested_shares, account_risk_dollars, confidence_score, ai_take_text, ai_provider, "
            "time_horizon, status, created_at) VALUES "
            "('AAPL', 'long', 100.0, 95.0, 110.0, 120.0, 2.0, 4.0, 10, 50.0, 70, 'test', 'none', "
            "'1-4 weeks', 'pending', '2026-01-01 00:00:00')"
        )
        conn.commit()

    before = {row[1] for row in engine.connect().exec_driver_sql('PRAGMA table_info("tradeplanrecord")').fetchall()}
    assert "technical_score" not in before

    monkeypatch.setattr(database_module, "engine", engine)
    # Mirrors the real create_db_and_tables() flow: create_all() first (skips
    # tradeplanrecord since it already exists, but creates paperposition/
    # equitysnapshot/accountstate fresh with the full current schema), then
    # the migration helper patches the one table that predates the new columns.
    SQLModel.metadata.create_all(engine)
    database_module._add_missing_columns()

    with engine.connect() as conn:
        after = {row[1] for row in conn.exec_driver_sql('PRAGMA table_info("tradeplanrecord")').fetchall()}
    for new_column in ("technical_score", "fundamental_score", "news_score", "signal_reasons"):
        assert new_column in after

    # The pre-existing row is untouched and readable, with the new columns as NULL.
    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT symbol, technical_score FROM tradeplanrecord").fetchone()
    assert row[0] == "AAPL"
    assert row[1] is None

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import MetaData
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_infra_settings

_infra = get_infra_settings()
_infra.db_file.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{_infra.db_file}",
    connect_args={"check_same_thread": False},
)


def _add_missing_columns() -> None:
    """SQLite has no migration framework wired up here (no Alembic), and
    `create_all` only creates tables that don't exist yet — it never adds a
    column to a table that's already on disk. Without this, a model field
    added after someone's runtime.db already exists would 500 with 'no such
    column' on the very next request instead of just picking up the new
    column. Additive only: never drops or renames, so a field removed from a
    model just leaves a harmless orphan column rather than risking data."""
    with engine.connect() as conn:
        for table in SQLModel.metadata.tables.values():
            existing = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")').fetchall()}
            if not existing:
                continue  # table doesn't exist yet — create_all() (called just before this) already handles that
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(engine.dialect)
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
        conn.commit()


def _relax_not_null_constraints() -> None:
    """A column that used to be required and later became Optional in the
    model (e.g. TradePlanRecord.direction/entry/stop/tp1/tp2/rr1/rr2/
    suggested_shares/account_risk_dollars, once a "no_trade" decision with
    no sizing became a real, storable outcome — see notes/Decisions.md)
    keeps its original NOT NULL constraint on anyone's already-existing
    runtime.db: SQLite has no ALTER COLUMN to drop one. Left unfixed,
    inserting the now-legitimate NULL crashes with a raw IntegrityError
    instead of persisting the no-trade record it's supposed to be.

    Detected per-table via PRAGMA table_info's notnull flag vs the current
    model, fixed with SQLite's standard rebuild recipe: build a correctly-
    constrained shadow table, copy every column the old table and the
    current model still have in common, drop the old table, rename the
    shadow into its place. Unlike `_add_missing_columns()`'s pure ADD
    COLUMN, a rebuild needs one concrete target schema, so — unlike that
    function — this does drop any column the model no longer has; accepted
    since the alternative is a permanently broken table. Only touches a
    table when a real mismatch is found, so it's a cheap no-op PRAGMA check
    against a healthy table."""
    with engine.connect() as conn:
        for table in SQLModel.metadata.tables.values():
            info = conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")').fetchall()
            if not info:
                continue  # table doesn't exist yet — create_all() already handles that
            existing_notnull = {row[1]: bool(row[3]) for row in info}
            needs_relax = any(existing_notnull.get(column.name) and column.nullable for column in table.columns)
            if not needs_relax:
                continue

            shadow_name = f"_migrate_{table.name}"
            shadow = table.to_metadata(MetaData(), name=shadow_name)
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{shadow_name}"')
            shadow.create(conn)

            model_columns = set(table.columns.keys())
            copy_columns = [name for name in existing_notnull if name in model_columns]
            columns_sql = ", ".join(f'"{c}"' for c in copy_columns)
            conn.exec_driver_sql(f'INSERT INTO "{shadow_name}" ({columns_sql}) SELECT {columns_sql} FROM "{table.name}"')
            conn.exec_driver_sql(f'DROP TABLE "{table.name}"')
            conn.exec_driver_sql(f'ALTER TABLE "{shadow_name}" RENAME TO "{table.name}"')
        conn.commit()


def create_db_and_tables() -> None:
    from app.backtest import models as backtest_models  # noqa: F401 - BacktestRun / BacktestTrade / BacktestEquityPoint
    from app.backtest import validation_models  # noqa: F401 - BacktestValidation (walk_forward validation)
    from app.knowledge import models as knowledge_models  # noqa: F401 - KnownFact (plan.md F-4)
    from app.portfolio import alert_models  # noqa: F401 - PriceAlert / NotificationLog
    from app.portfolio import missed_trade_models  # noqa: F401 - MissedTradeOutcome
    from app.portfolio import models  # noqa: F401 - registers tables on SQLModel.metadata
    from app.portfolio import thesis_models  # noqa: F401 - ThesisRecord
    from app.strategy import models as strategy_models  # noqa: F401 - StrategyVersion
    from app.watchers import models as watcher_models  # noqa: F401 - WatcherState

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    _relax_not_null_constraints()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

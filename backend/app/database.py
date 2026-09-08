from __future__ import annotations

from collections.abc import Generator

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


def create_db_and_tables() -> None:
    from app.portfolio import models  # noqa: F401 - registers tables on SQLModel.metadata

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

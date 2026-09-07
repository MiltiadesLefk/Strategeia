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


def create_db_and_tables() -> None:
    from app.portfolio import models  # noqa: F401 - registers tables on SQLModel.metadata

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.config import AppSettings
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.strategy.models import StrategyVersion
from app.strategy.snapshot import build_snapshot, describe_changes, fingerprint, snapshot_json

# Creation is serialised inside the process (the app is one process; SQLite
# allows one writer anyway), and the unique constraints on `number` and
# `fingerprint` settle a race between processes: the loser's insert fails and
# it re-reads. A handful of attempts is plenty, since each failure means
# another writer just made progress.
_creation_lock = threading.Lock()
MAX_CREATE_ATTEMPTS = 5


def _by_fingerprint(session: Session, fp: str) -> StrategyVersion | None:
    return session.exec(select(StrategyVersion).where(StrategyVersion.fingerprint == fp)).first()


def current_strategy_version(session: Session, settings: AppSettings) -> StrategyVersion:
    """The version the given settings plus the code's current constants
    amount to: the existing row with the same fingerprint, else a new one with
    the next number. Called when a plan is generated and nowhere else, so a
    settings edit that is never followed by a plan creates no row (versions
    describe rules that were actually used). Commits the session when it
    creates a row; call it when nothing else is pending.
    """
    snapshot = build_snapshot(settings)
    fp = fingerprint(snapshot)
    existing = _by_fingerprint(session, fp)
    if existing is not None:
        return existing

    with _creation_lock:
        for _ in range(MAX_CREATE_ATTEMPTS):
            existing = _by_fingerprint(session, fp)
            if existing is not None:
                return existing
            last = session.exec(select(func.max(StrategyVersion.number))).one()
            row = StrategyVersion(number=(last or 0) + 1, fingerprint=fp, settings_snapshot=snapshot_json(snapshot))
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                # Another process won the same number or the same fingerprint.
                session.rollback()
                continue
            session.refresh(row)
            return row
    raise RuntimeError("could not record a strategy version after repeated conflicts")


def strategy_version_for_settings(session: Session, settings: AppSettings) -> int | None:
    """Read-only twin of current_strategy_version: the number the settings
    would map to, or None if no plan has been generated under them yet.
    Never writes, so a GET can use it."""
    existing = _by_fingerprint(session, fingerprint(build_snapshot(settings)))
    return existing.number if existing else None


def plan_strategy_versions(session: Session, plan_ids: list[int | None]) -> dict[int, int | None]:
    """{trade_plan_id: strategy version number} for the given plans, read-only.
    A position has no version of its own: it inherits its plan's, so the
    number cannot disagree with the plan it came from."""
    ids = sorted({i for i in plan_ids if i is not None})
    if not ids:
        return {}
    rows = session.exec(
        select(TradePlanRecord.id, TradePlanRecord.strategy_version).where(TradePlanRecord.id.in_(ids))
    ).all()
    return {plan_id: version for plan_id, version in rows}


@dataclass
class StrategyVersionSummary:
    version: StrategyVersion
    snapshot: dict[str, Any]
    changes: list[str] = field(default_factory=list)
    plans: int = 0
    no_trades: int = 0
    positions_opened: int = 0
    closed_trades: int = 0


@dataclass
class StrategyHistory:
    versions: list[StrategyVersionSummary]  # newest first
    unversioned_plans: int  # plans made before versioning existed
    current_number: int | None  # None: the current settings have not produced a plan yet


def strategy_history(session: Session, settings: AppSettings) -> StrategyHistory:
    """Every version with what changed from the one before it and how many
    plans / closed trades were made under it. Read-only."""
    rows = session.exec(select(StrategyVersion).order_by(StrategyVersion.number)).all()

    plan_counts: dict[int, tuple[int, int]] = {}  # version -> (all plans, no_trade plans)
    for number, total, no_trade in session.exec(
        select(
            TradePlanRecord.strategy_version,
            func.count(),
            func.sum(case((TradePlanRecord.status == "no_trade", 1), else_=0)),
        )
        .where(TradePlanRecord.strategy_version.is_not(None))
        .group_by(TradePlanRecord.strategy_version)
    ).all():
        plan_counts[number] = (total, no_trade or 0)

    position_counts: dict[int, tuple[int, int]] = {}  # version -> (positions, closed)
    for number, total, closed in session.exec(
        select(
            TradePlanRecord.strategy_version,
            func.count(),
            func.sum(case((PaperPosition.status == "closed", 1), else_=0)),
        )
        .join(TradePlanRecord, PaperPosition.trade_plan_id == TradePlanRecord.id)
        .where(TradePlanRecord.strategy_version.is_not(None))
        .group_by(TradePlanRecord.strategy_version)
    ).all():
        position_counts[number] = (total, closed or 0)

    summaries: list[StrategyVersionSummary] = []
    previous_snapshot: dict[str, Any] | None = None
    for row in rows:
        snapshot = json.loads(row.settings_snapshot)
        plans, no_trades = plan_counts.get(row.number, (0, 0))
        opened, closed = position_counts.get(row.number, (0, 0))
        summaries.append(
            StrategyVersionSummary(
                version=row,
                snapshot=snapshot,
                changes=describe_changes(previous_snapshot, snapshot),
                plans=plans,
                no_trades=no_trades,
                positions_opened=opened,
                closed_trades=closed,
            )
        )
        previous_snapshot = snapshot

    unversioned = session.exec(
        select(func.count()).select_from(TradePlanRecord).where(TradePlanRecord.strategy_version.is_(None))
    ).one()
    summaries.reverse()
    return StrategyHistory(
        versions=summaries,
        unversioned_plans=unversioned,
        current_number=strategy_version_for_settings(session, settings),
    )

"""Sleeves: one isolated paper account per trading style.

Adapted in spirit from the "sleeve" idea in jev-trade (src/sleeves.ts, MIT,
which is in turn based on jev-trader by Jarrod Watts, MIT): a sleeve is a
named slice of the book with its own cash, positions and results, so two styles
can trade side by side without their numbers mixing. Only the idea is taken;
the implementation here is written for this app's SQLModel tables.

How rows belong to a sleeve: AccountState, PaperPosition, EquitySnapshot and
TradePlanRecord carry a nullable `sleeve_id`. NULL means the core sleeve, so a
database written before sleeves existed needs no backfill and reads exactly as
it always did. Every scoped query goes through `scope_clause`, which is the one
place that rule is written down.

Reads never create anything: `read_scope` finds the sleeve without inserting
(a GET must stay read-only). Writes call `ensure_core_sleeve`, which creates the
core row the first time it is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import or_
from sqlmodel import Session, select

from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, Sleeve, TradePlanRecord
from app.timeutil import utcnow_naive

CORE_SLEEVE_KEY = "core"
CORE_SLEEVE_NAME = "Swing (rules)"
CORE_SLEEVE_STYLE = "swing"
# `?sleeve=all` on a read endpoint: every sleeve together (positions, statistics).
ALL_SLEEVES = "all"

# Colours handed to new sleeves in turn (core is the first), as chips in the UI.
# Chosen to stay distinguishable from each other and readable on both themes.
SLEEVE_COLORS = ["#3b82f6", "#f59e0b", "#10b981", "#a855f7", "#ef4444", "#14b8a6", "#ec4899", "#84cc16"]

MAX_SLEEVES = 12  # a personal dashboard: more than this is a typo or a runaway script
DEFAULT_SLEEVE_STARTING_CASH = 100_000.0


class SleeveError(Exception):
    """A sleeve request that cannot be done (unknown key, duplicate name, ...).
    Carries the HTTP status the router should answer with."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SleeveScope:
    """Which rows a query covers: one sleeve. `sleeve_id` is None only for the
    core sleeve before its row exists (nothing can reference it yet, so only
    NULL rows qualify)."""

    sleeve_id: int | None
    is_core: bool
    key: str = CORE_SLEEVE_KEY
    # What the sleeve's account starts with (None for core: it follows Settings).
    starting_cash: float | None = None


def scope_clause(column, scope: SleeveScope):
    """SQL condition selecting `column` (a model's sleeve_id) rows in `scope`.
    Core also takes NULL: rows written before sleeves existed."""
    if scope.is_core:
        if scope.sleeve_id is None:
            return column.is_(None)
        return or_(column == scope.sleeve_id, column.is_(None))
    return column == scope.sleeve_id


def find_core_sleeve(session: Session) -> Sleeve | None:
    return session.exec(select(Sleeve).where(Sleeve.key == CORE_SLEEVE_KEY)).first()


def ensure_core_sleeve(session: Session) -> Sleeve:
    """The core sleeve, created on first use. Not created by reads."""
    core = find_core_sleeve(session)
    if core is None:
        core = Sleeve(
            key=CORE_SLEEVE_KEY,
            name=CORE_SLEEVE_NAME,
            style=CORE_SLEEVE_STYLE,
            starting_cash=DEFAULT_SLEEVE_STARTING_CASH,
            color=SLEEVE_COLORS[0],
            notes="The original account: rule-based swing trades. Every position from before sleeves existed lives here.",
        )
        session.add(core)
        session.commit()
        session.refresh(core)
    return core


def plan_sleeve_id(session: Session, sleeve: Sleeve | None) -> int | None:
    """What to stamp on a new plan or queued redo for `sleeve` (None = core):
    the sleeve's id, or the core's id if its row exists yet. Never writes."""
    if sleeve is not None:
        return sleeve.id
    core = find_core_sleeve(session)
    return core.id if core is not None else None


def non_core_sleeve_id(session: Session, sleeve_id: int | None) -> int | None:
    """`sleeve_id` with core folded into None, so "no sleeve" and "the core
    sleeve's id" compare equal (queued redos are matched on it)."""
    core = find_core_sleeve(session)
    return None if sleeve_id is None or (core is not None and sleeve_id == core.id) else sleeve_id


def scope_of(sleeve: Sleeve) -> SleeveScope:
    is_core = sleeve.key == CORE_SLEEVE_KEY
    return SleeveScope(sleeve.id, is_core, sleeve.key, None if is_core else sleeve.starting_cash)


def read_scope(session: Session, ref: str | int | None) -> SleeveScope:
    """The scope for a read, by sleeve key or id (None = core), without inserting
    anything. Raises SleeveError(404) for an unknown sleeve."""
    if ref is None or ref == CORE_SLEEVE_KEY:
        core = find_core_sleeve(session)
        return SleeveScope(core.id if core else None, True)
    sleeve = get_sleeve(session, ref)
    return scope_of(sleeve)


def get_sleeve(session: Session, ref: str | int | None) -> Sleeve:
    """A sleeve by key or numeric id; None means core (created if missing).
    Raises SleeveError(404) when it does not exist."""
    if ref is None:
        return ensure_core_sleeve(session)
    if ref == CORE_SLEEVE_KEY:
        return ensure_core_sleeve(session)
    sleeve = None
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        sleeve = session.get(Sleeve, int(ref))
    if sleeve is None and isinstance(ref, str):
        sleeve = session.exec(select(Sleeve).where(Sleeve.key == ref)).first()
    if sleeve is None:
        raise SleeveError(f"Sleeve '{ref}' not found", 404)
    return sleeve


def list_sleeves(session: Session, *, include_disabled: bool = True) -> list[Sleeve]:
    """All sleeves, core first (created if it does not exist yet: the list is
    only ever read from a write-capable path, see api/routers/sleeves.py)."""
    ensure_core_sleeve(session)
    sleeves = session.exec(select(Sleeve).order_by(Sleeve.id)).all()
    return [s for s in sleeves if include_disabled or s.enabled]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40]


def create_sleeve(
    session: Session, name: str, style: str, starting_cash: float, notes: str | None = None
) -> Sleeve:
    name, style = name.strip(), style.strip()
    if not name:
        raise SleeveError("A sleeve needs a name")
    if not style:
        raise SleeveError("A sleeve needs a style label")
    if starting_cash <= 0:
        raise SleeveError("Starting cash must be positive")
    base = slugify(name)
    if not base:
        raise SleeveError("The name must contain at least one letter or digit")
    existing = list_sleeves(session)
    if len(existing) >= MAX_SLEEVES:
        raise SleeveError(f"At most {MAX_SLEEVES} sleeves are supported")
    if any(s.name.lower() == name.lower() for s in existing):
        raise SleeveError(f"A sleeve named '{name}' already exists", 409)
    keys = {s.key for s in existing}
    key, n = base, 2
    while key in keys:
        key = f"{base}-{n}"
        n += 1
    sleeve = Sleeve(
        key=key,
        name=name,
        style=style,
        starting_cash=starting_cash,
        color=SLEEVE_COLORS[len(existing) % len(SLEEVE_COLORS)],
        notes=notes,
        created_at=utcnow_naive(),
    )
    session.add(sleeve)
    session.commit()
    session.refresh(sleeve)
    return sleeve


def update_sleeve(
    session: Session,
    sleeve: Sleeve,
    *,
    name: str | None = None,
    style: str | None = None,
    enabled: bool | None = None,
    notes: str | None = None,
) -> Sleeve:
    if name is not None:
        name = name.strip()
        if not name:
            raise SleeveError("A sleeve needs a name")
        clash = session.exec(select(Sleeve).where(Sleeve.name == name, Sleeve.id != sleeve.id)).first()
        if clash is not None:
            raise SleeveError(f"A sleeve named '{name}' already exists", 409)
        sleeve.name = name
    if style is not None:
        if not style.strip():
            raise SleeveError("A sleeve needs a style label")
        sleeve.style = style.strip()
    if enabled is not None:
        if not enabled and sleeve.key == CORE_SLEEVE_KEY:
            raise SleeveError("The core sleeve cannot be disabled: the automatic scan trades in it")
        sleeve.enabled = enabled
    if notes is not None:
        sleeve.notes = notes
    session.add(sleeve)
    session.commit()
    session.refresh(sleeve)
    return sleeve


def has_history(session: Session, sleeve: Sleeve) -> bool:
    """Whether anything was ever traded or planned in this sleeve."""
    scope = scope_of(sleeve)
    if session.exec(select(PaperPosition.id).where(scope_clause(PaperPosition.sleeve_id, scope))).first() is not None:
        return True
    return session.exec(select(TradePlanRecord.id).where(scope_clause(TradePlanRecord.sleeve_id, scope))).first() is not None


def delete_sleeve(session: Session, sleeve: Sleeve) -> None:
    """Removes an unused sleeve (and its empty account/curve rows). Refuses the
    core sleeve and any sleeve that ever held a position or a plan: deleting one
    would erase trade history that statistics were computed from."""
    if sleeve.key == CORE_SLEEVE_KEY:
        raise SleeveError("The core sleeve cannot be deleted")
    if has_history(session, sleeve):
        raise SleeveError("This sleeve has positions or trade plans; disable it instead of deleting it", 409)
    for model in (AccountState, EquitySnapshot):
        for row in session.exec(select(model).where(model.sleeve_id == sleeve.id)).all():
            session.delete(row)
    session.delete(sleeve)
    session.commit()

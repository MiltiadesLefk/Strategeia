"""The strategy-version table. See app/strategy/__init__.py."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive


class StrategyVersion(SQLModel, table=True):
    """One distinct combination of decision-relevant settings and rule
    constants. `number` counts up from 1 in the order versions were first used.

    `fingerprint` is the hash of `settings_snapshot` (see snapshot.fingerprint)
    and is what identifies a version: the same rules always map back to the same
    row, including when settings are changed and later changed back. Both
    `number` and `fingerprint` are unique so two writers racing to create the
    same version (or the same number) cannot both succeed; the loser re-reads.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    number: int = Field(unique=True, index=True)
    fingerprint: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    # JSON text of the snapshot the fingerprint was computed from, kept in full
    # so a later version can be diffed against it and so a number can always be
    # explained ("version 3 = min confidence 38, ATR stop 1.5x, ...").
    settings_snapshot: str
    # Optional short human note ("tightened the bar after the March drawdown").
    # Nothing writes it automatically; it is there for a person (or a later
    # improvement loop) to annotate a version.
    label: Optional[str] = None

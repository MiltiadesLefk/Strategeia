"""Training rows from stored backtest trades, and the walk-forward split.

Label = the trade's realized R multiple. A row is one closed backtest trade; its
features are the plan's score components at entry (see features.py). Nothing here
reads the market or the future: the split is by time, and a row whose label only
became known (exit) after the next window started is purged from the earlier one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, select

from app.backtest.models import BacktestTrade
from app.ml.features import feature_vector


@dataclass(frozen=True)
class Row:
    entry_date: date
    exit_date: date
    symbol: str
    features: list[float]
    r: float


@dataclass(frozen=True)
class Split:
    train: list[Row]
    validation: list[Row]
    test: list[Row]
    validation_start: date
    test_start: date
    purged: int


def load_rows(session: Session, run_ids: list[int]) -> list[Row]:
    """Closed trades of the given runs that have a result and stored scores, oldest first."""
    if not run_ids:
        return []
    trades = session.exec(select(BacktestTrade).where(BacktestTrade.run_id.in_(run_ids))).all()  # type: ignore[attr-defined]
    rows: list[Row] = []
    for t in trades:
        if t.status != "closed" or t.realized_r is None or t.exit_date is None or not t.scores_json:
            continue
        try:
            scores = json.loads(t.scores_json)
        except ValueError:
            continue
        rows.append(
            Row(t.entry_date, t.exit_date, t.symbol, feature_vector(scores, t.confidence_points, t.direction), float(t.realized_r))
        )
    rows.sort(key=lambda r: (r.entry_date, r.symbol))
    return rows


def walk_forward_split(rows: list[Row], train_frac: float = 0.6, validation_frac: float = 0.2) -> Split | None:
    """Chronological train / validation / test by entry date (rows on the same day
    stay together). A train row that exits on or after the validation start, or a
    validation row that exits on or after the test start, is dropped: its label was
    not yet known when the later window began. Returns None if a window ends up empty."""
    if len(rows) < 3:
        return None
    days = sorted({r.entry_date for r in rows})
    if len(days) < 3:
        return None
    v_idx = max(1, min(len(days) - 2, int(len(days) * train_frac)))
    t_idx = max(v_idx + 1, min(len(days) - 1, int(len(days) * (train_frac + validation_frac))))
    validation_start, test_start = days[v_idx], days[t_idx]
    train = [r for r in rows if r.entry_date < validation_start]
    validation = [r for r in rows if validation_start <= r.entry_date < test_start]
    test = [r for r in rows if r.entry_date >= test_start]
    train_ok = [r for r in train if r.exit_date < validation_start]
    validation_ok = [r for r in validation if r.exit_date < test_start]
    purged = (len(train) - len(train_ok)) + (len(validation) - len(validation_ok))
    if not train_ok or not validation_ok or not test:
        return None
    return Split(train_ok, validation_ok, test, validation_start, test_start, purged)

"""Every datetime this API emits must carry an explicit UTC offset.

Timestamps are stored naive-but-UTC (SQLite columns are naive). Serialized
bare — "2026-09-12T16:16:39" — JavaScript's `new Date()` reads them as LOCAL
time, so the whole UI silently shifted by the viewer's UTC offset: position
open dates, and "x minutes ago" relative times that east of UTC could read as
the future.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.schemas.portfolio_schemas import EquityPointSchema, PositionSchema
from app.schemas.trade_plan_schemas import TradePlanResponse
from app.timeutil import utc_iso_from_timestamp, utcnow_naive


def _position(**overrides) -> PositionSchema:
    defaults = dict(
        id=1, trade_plan_id=None, symbol="AAPL", direction="long", entry_price=100.0,
        stop_loss=95.0, tp1=110.0, tp2=120.0, shares=10, opened_at=utcnow_naive(),
        status="open", closed_at=None, close_price=None, close_reason=None,
        realized_pnl=None, realized_r=None,
    )
    defaults.update(overrides)
    return PositionSchema(**defaults)


def test_position_timestamps_are_utc_marked():
    payload = json.loads(_position().model_dump_json())

    assert payload["opened_at"].endswith("Z"), payload["opened_at"]


def test_closed_at_is_utc_marked_when_present():
    payload = json.loads(_position(closed_at=utcnow_naive(), status="closed").model_dump_json())

    assert payload["closed_at"].endswith("Z")


def test_equity_point_timestamp_is_utc_marked():
    point = EquityPointSchema(timestamp=utcnow_naive(), equity_value=1.0, cash_balance=1.0)

    assert json.loads(point.model_dump_json())["timestamp"].endswith("Z")


def test_trade_plan_created_at_is_utc_marked():
    plan = TradePlanResponse(symbol="AAPL", direction="long", created_at=utcnow_naive())

    assert json.loads(plan.model_dump_json())["created_at"].endswith("Z")


def test_serialized_instant_matches_the_stored_instant():
    """The offset is stamped on, not applied — the moment itself must not move."""
    stored = datetime(2026, 9, 12, 16, 16, 39)
    payload = json.loads(_position(opened_at=stored).model_dump_json())

    parsed = datetime.fromisoformat(payload["opened_at"].replace("Z", "+00:00"))

    assert parsed == stored.replace(tzinfo=timezone.utc)


def test_news_published_at_is_utc_marked():
    """News timestamps reach the frontend as plain strings, bypassing the
    schema serializer — they need the same treatment at the producer."""
    assert utc_iso_from_timestamp(1_757_692_599).endswith("Z")

"""Read side of the smart-money candidates (NEW_PLAN phase 1): a list for the Smart Money page.

Read-only: a GET never scores, fetches or writes. The tracker watcher is what creates candidates.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.config import AppSettings
from app.schemas.smart_money_candidate_schemas import (
    CandidateComponentOut,
    SmartMoneyCandidateOut,
    SmartMoneyCandidatesResponse,
)
from app.smartmoney.models import CANDIDATE_STATUSES, SmartMoneyCandidate
from app.smartmoney.tracker import SOURCE_DELAY_NOTES

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

DISCLAIMER = (
    "These are filings that were REPORTED, with the delay each kind of report has (Form 4: up to 2 business days; "
    "House: up to 45 days; 13F: as of a quarter end, up to 45 days late). The score is a starting guess that has "
    "not been validated. A candidate only means the symbol is worth a normal full evaluation; it is not a trade."
)


def _out(row: SmartMoneyCandidate) -> SmartMoneyCandidateOut:
    return SmartMoneyCandidateOut(
        id=row.id or 0,
        symbol=row.symbol,
        score=row.score,
        components=[
            CandidateComponentOut(
                source=str(c.get("source", "")),
                points=float(c.get("points", 0.0)),
                reason=str(c.get("reason", "")),
                delay_note=SOURCE_DELAY_NOTES.get(str(c.get("source", ""))),
            )
            for c in (row.components or [])
        ],
        status=row.status,
        reason=row.reason,
        scope=row.scope,
        scored_at=row.scored_at,
        first_trigger_known_at=row.first_trigger_known_at,
        created_at=row.created_at,
        expires_at=row.expires_at,
        plan_id=row.plan_id,
        sleeve_key=row.sleeve_key,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        notes=row.notes,
    )


def list_candidates(
    session: Session, settings: AppSettings, *, status: str | None = None, limit: int = DEFAULT_LIMIT
) -> SmartMoneyCandidatesResponse:
    query = select(SmartMoneyCandidate)
    if status is not None and status in CANDIDATE_STATUSES:
        query = query.where(SmartMoneyCandidate.status == status)
    rows = session.exec(query.order_by(SmartMoneyCandidate.created_at.desc(), SmartMoneyCandidate.id.desc())).all()
    shown = rows[: max(1, min(limit, MAX_LIMIT))]
    return SmartMoneyCandidatesResponse(
        scope=settings.smart_money_scope,
        min_score=settings.smart_money_min_score,
        total=len(rows),
        candidates=[_out(r) for r in shown],
        disclaimer=DISCLAIMER,
    )

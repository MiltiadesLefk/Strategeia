"""The committee gate for a plan someone is waiting on: started in the background, verdict applied when it ends.

An unattended pass (auto-scan, the market-open redo) runs the committee inline (committee/gate.py), because
nobody is watching and the result decides whether the position opens. A person pressing Generate is watching,
so the plan is returned at once and the committee runs in the background through the same manager the AI
Committee page uses: every step is saved as it finishes, so the plan card can show each report the moment it
is ready, and the page is never frozen.

When the run ends `apply_committee_verdict` reads the rating against the plan's direction. The rule is the
gate's rule: it can only stop a trade (cancel it or leave it for review), never start one or change a level.
A plan made for a person is never opened automatically: a backing rating just says so on the card and the
Execute button is theirs. A run that fails or cannot be read records no objection and says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlmodel import Session

from app.committee.gate import agrees_with
from app.committee.models import CommitteeRun
from app.committee.service import CommitteeBusyError, CommitteeManager, CommitteeNotConfiguredError, get_committee_manager
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.portfolio.models import TradePlanRecord

logger = logging.getLogger(__name__)

READING_NOTE = "The AI Committee is reading this plan now. Each report appears below as it is written."


def _default_session_factory() -> Callable[[], Session]:
    from app.database import engine

    return lambda: Session(engine)


def start_background_gate(
    record: TradePlanRecord,
    direction: str,
    data_provider: DataProvider,
    llm: LLMProvider,
    settings: AppSettings,
    *,
    informational: bool,
    manager: CommitteeManager | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> tuple[int | None, str]:
    """Start the run. (run id, note for the card). No run id means it could not start, with the reason in
    the note: the plan then carries on as if the gate were not there."""
    manager = manager or get_committee_manager()
    factory = session_factory or _default_session_factory()
    plan_id, action = record.id, settings.committee_gate_action

    def on_finish(run_id: int) -> None:
        apply_committee_verdict(factory, plan_id, run_id, direction, action, informational)

    try:
        run_id = manager.start(record.symbol, data_provider, llm, settings, background=True, on_finish=on_finish)
    except CommitteeBusyError:
        return None, "Not read: another committee run is in progress. Run the committee from the AI Committee page when it ends."
    except CommitteeNotConfiguredError as exc:
        return None, f"Not read: {exc}"
    note = READING_NOTE
    if informational:
        note += " The market is closed, so this reading is for your information; the fresh plan is read again at the open."
    return run_id, note


def apply_committee_verdict(
    session_factory: Callable[[], Session],
    plan_id: int,
    run_id: int,
    direction: str,
    action: str,
    informational: bool,
) -> None:
    """Write what the finished run means onto the plan. Never raises."""
    try:
        with session_factory() as session:
            plan = session.get(TradePlanRecord, plan_id)
            run = session.get(CommitteeRun, run_id)
            if plan is None or run is None:
                return
            rating = run.rating if run.status == "done" else None
            plan.committee_run_id = run_id
            plan.committee_rating = rating
            verdict = agrees_with(rating, direction)
            if verdict is None:
                note = f"The AI Committee gave no readable rating (run #{run_id}), so it neither approved nor objected."
            elif verdict:
                note = f"AI Committee rated {plan.symbol} {rating} (run #{run_id}), which backs the {direction}."
            else:
                note = f"AI Committee rated {plan.symbol} {rating} (run #{run_id}), which does not back a {direction}."
            if plan.status == "pending" and not informational:
                if verdict is False and action == "cancel":
                    plan.status = "no_trade"
                    plan.reason = f"{note} No trade taken."
                elif verdict is False:
                    note += " Left pending for your review."
                elif verdict:
                    note += " You can execute it."
            elif informational:
                note += " Read after the close for your information; the fresh plan is read again at the open."
            elif plan.status != "pending":
                note += f" (The plan was already {plan.status} when the committee finished.)"
            plan.committee_note = note
            plan.signal_reasons = "; ".join(filter(None, [plan.signal_reasons, note]))
            session.add(plan)
            session.commit()
    except Exception:  # noqa: BLE001 - the worker thread must end quietly
        logger.exception("applying the committee verdict to plan %s failed", plan_id)

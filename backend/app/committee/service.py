"""Running committee runs as background jobs and keeping what they write.

One run at a time, in one worker thread: a run is a dozen AI calls on the user's own provider, and a
second one at the same moment would only double the spend and slow both. Every report is written to
the run row the moment it is finished, which is what the live view polls. The only things a run
writes are its own CommitteeRun row: no plan, no position, no setting.

A run is not resumable. One left running by a process that died is marked failed at the next start.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict

from sqlmodel import Session, select

from app.committee.models import ACTIVE_RUN_STATUSES, RUN_DONE, RUN_FAILED, RUN_QUEUED, RUN_RUNNING, CommitteeRun
from app.committee.orchestrator import clamp_rounds, planned_steps, run_committee
from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.llm_providers.base import LLMProvider
from app.schemas.committee_schemas import CommitteeRunSchema, CommitteeRunSummarySchema, CommitteeStepSchema
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "interrupted: the app stopped or restarted while this run was in progress"
OPINION_NOTE = (
    "An AI opinion only. It does not open, size or stop any trade and the rules engine never reads it. "
    "It can be wrong: the figures it quotes are checked against computed data, and anything it could not "
    "support is flagged."
)
MAX_HISTORY = 100


class CommitteeBusyError(RuntimeError):
    """Another run is queued or running."""


class CommitteeNotConfiguredError(RuntimeError):
    """No real AI provider is configured: the committee has nothing to ask."""


class CommitteeNotFoundError(LookupError):
    pass


def recover_interrupted_runs(session_factory: Callable[[], Session]) -> int:
    with session_factory() as session:
        stuck = session.exec(select(CommitteeRun).where(CommitteeRun.status.in_(ACTIVE_RUN_STATUSES))).all()
        for run in stuck:
            run.status, run.error, run.finished_at = RUN_FAILED, INTERRUPTED_MESSAGE, utcnow_naive()
            session.add(run)
        session.commit()
        return len(stuck)


def execute_run(
    session_factory: Callable[[], Session],
    run_id: int,
    data_provider: DataProvider,
    llm: LLMProvider,
    settings: AppSettings,
    on_finish: Callable[[int], None] | None = None,
) -> None:
    """The body of a run. Never raises: any failure ends up on the run row. `on_finish(run_id)` is called once
    the row holds its final state, whatever that state is (it must not raise; a failure is logged)."""
    try:
        _execute_run(session_factory, run_id, data_provider, llm, settings)
    finally:
        if on_finish is not None:
            try:
                on_finish(run_id)
            except Exception:  # noqa: BLE001
                logger.exception("committee run %s: the finish hook failed", run_id)


def _execute_run(
    session_factory: Callable[[], Session],
    run_id: int,
    data_provider: DataProvider,
    llm: LLMProvider,
    settings: AppSettings,
) -> None:

    def update(**fields) -> None:
        with session_factory() as session:
            run = session.get(CommitteeRun, run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
            session.add(run)
            session.commit()

    with session_factory() as session:
        row = session.get(CommitteeRun, run_id)
        symbol = row.symbol if row else ""
    update(status=RUN_RUNNING)

    def publish(steps: list[dict], calls: int) -> None:
        update(steps=json.dumps(steps), llm_calls_used=calls)

    try:
        outcome = run_committee(symbol, data_provider, llm, settings, publish, session_factory=session_factory)
    except AllProvidersFailedError:
        update(status=RUN_FAILED, finished_at=utcnow_naive(), error=f"No price data could be fetched for {symbol}.")
        return
    except Exception as exc:  # the worker thread must end in a stored result, never a silent death
        logger.exception("committee run %s failed", run_id)
        update(status=RUN_FAILED, finished_at=utcnow_naive(), error=f"Unexpected error: {str(exc)[:200]}")
        return
    update(
        status=RUN_FAILED if outcome.error else RUN_DONE,
        finished_at=utcnow_naive(),
        steps=json.dumps([asdict(s) for s in outcome.steps]),
        llm_calls_used=outcome.calls_used,
        rating=outcome.rating,
        rating_summary=outcome.summary,
        rating_key_risks=outcome.key_risks,
        rating_conviction=outcome.conviction,
        rating_parse=outcome.parse,
        web_search=outcome.web_search_used,
        error=outcome.error,
    )


class CommitteeManager:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(
        self,
        symbol: str,
        data_provider: DataProvider,
        llm: LLMProvider,
        settings: AppSettings,
        *,
        background: bool = True,
        on_finish: Callable[[int], None] | None = None,
    ) -> int:
        if llm.name == "none" or not llm.is_configured():
            raise CommitteeNotConfiguredError(
                "The AI Committee needs a real AI provider. Choose one in Settings; the 'none' provider only echoes text."
            )
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise CommitteeBusyError("a committee run is already in progress: wait for it to finish")
            with self._session_factory() as session:
                active = session.exec(select(CommitteeRun).where(CommitteeRun.status.in_(ACTIVE_RUN_STATUSES))).first()
                if active is not None:
                    raise CommitteeBusyError(f"committee run #{active.id} is still {active.status}")
                debate, risk = clamp_rounds(settings.committee_debate_rounds), clamp_rounds(settings.committee_risk_rounds)
                run = CommitteeRun(
                    symbol=symbol,
                    status=RUN_QUEUED,
                    steps=json.dumps([asdict(s) for s in planned_steps(debate, risk)]),
                    llm_calls_max=settings.committee_max_llm_calls,
                    debate_rounds=debate,
                    risk_rounds=risk,
                    provider=llm.name,
                )
                session.add(run)
                session.commit()
                session.refresh(run)
                run_id = run.id
            if not background:
                execute_run(self._session_factory, run_id, data_provider, llm, settings, on_finish)
                return run_id
            self._thread = threading.Thread(
                target=execute_run,
                args=(self._session_factory, run_id, data_provider, llm, settings, on_finish),
                name=f"committee-{run_id}",
                daemon=True,
            )
            self._thread.start()
            return run_id


def run_summary(run: CommitteeRun) -> CommitteeRunSummarySchema:
    return CommitteeRunSummarySchema(
        id=run.id,
        symbol=run.symbol,
        status=run.status,
        created_at=run.created_at,
        finished_at=run.finished_at,
        rating=run.rating,
        llm_calls_used=run.llm_calls_used,
        llm_calls_max=run.llm_calls_max,
    )


def run_schema(run: CommitteeRun) -> CommitteeRunSchema:
    try:
        steps = [CommitteeStepSchema(**s) for s in json.loads(run.steps or "[]")]
    except (ValueError, TypeError):
        steps = []
    return CommitteeRunSchema(
        **run_summary(run).model_dump(),
        steps=steps,
        rating_summary=run.rating_summary,
        rating_key_risks=run.rating_key_risks,
        rating_conviction=run.rating_conviction,
        rating_parse=run.rating_parse,
        debate_rounds=run.debate_rounds,
        risk_rounds=run.risk_rounds,
        provider=run.provider,
        web_search=run.web_search,
        error=run.error,
        note=OPINION_NOTE,
    )


def list_runs(session: Session, symbol: str | None = None, limit: int = 30) -> list[CommitteeRun]:
    query = select(CommitteeRun).order_by(CommitteeRun.created_at.desc(), CommitteeRun.id.desc())
    if symbol:
        query = query.where(CommitteeRun.symbol == symbol)
    return list(session.exec(query.limit(max(1, min(limit, MAX_HISTORY)))).all())


_default_manager: CommitteeManager | None = None
_default_guard = threading.Lock()


def get_committee_manager() -> CommitteeManager:
    global _default_manager
    with _default_guard:
        if _default_manager is None:
            from app.database import engine

            _default_manager = CommitteeManager(lambda: Session(engine))
        return _default_manager


def reset_committee_manager() -> None:
    """Forget the process-wide manager (tests)."""
    global _default_manager
    with _default_guard:
        _default_manager = None


def recover_on_startup() -> int:
    from app.database import engine

    return recover_interrupted_runs(lambda: Session(engine))

# From TauricResearch/TradingAgents (via the vongchu/TradingAgents_TauricResearch mirror).
# Licensed under the Apache License, Version 2.0; full text in THIRD_PARTY_NOTICES.md.
# Adapted from vongchu/TradingAgents_TauricResearch@01477f9 tradingagents/graph/{setup,conditional_logic}.py;
# changes: the LangGraph state machine (analysts, bull/bear loop, research manager, trader, aggressive /
# conservative / neutral loop, portfolio manager) is a plain function over our own LLM provider. Debate
# length is a hard-capped round count, the whole run has a cap on AI calls, and a step is recorded
# (and published to the live view) the moment it starts and again when it ends.
"""The committee's flow, in order, as one small function.

    analysts (market, fundamentals, news, insider)        routine model, may use web search if the
        |                                                  user's research mode allows it
    bull / bear debate (a few rounds)                      routine model
        |
    research manager: a view and a first rating            decision model
        |
    trader: the action it leans toward                     decision model
        |
    risk debate: aggressive, conservative, neutral         decision model
        |
    final rating on the five-level scale                   decision model

An opinion layer. Nothing here opens, sizes, stops or cancels a trade, and the rules engine and the
AI overlay never read the result. Costs are bounded twice: `max_calls` caps the AI calls of a whole
run (optional steps are skipped before the three required ones can be starved), and the round
counts are clamped to MAX_DEBATE_ROUNDS whatever the settings say. Figures the model quotes are
checked against the computed data and shown as warnings; they never change the rating.

A reply that cannot be read gives no rating: the card says so rather than guessing one.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from app.analysis.ground_truth import find_ungrounded_figures
from app.committee import prompts
from app.committee.datapack import DataPack, gather_data
from app.committee.models import RATINGS
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import DECISION_TIER, ROUTINE_TIER, LLMProvider, LLMResult, LLMTier
from app.llm_providers.factory import generate_with_tier
from app.llm_providers.research_mode import generate_for_research
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# No debate runs longer than this many rounds, whatever the setting says: each round is several
# AI calls, and more rounds mostly repeat the same arguments.
MAX_DEBATE_ROUNDS = 3
# The research manager, the trader and the final rating. Optional steps are only run while enough
# of the budget is left to still do these.
REQUIRED_STEPS = 3
# Token limits per kind of step. A report is longer than a debate turn; a JSON verdict is short.
REPORT_MAX_TOKENS = 900
TURN_MAX_TOKENS = 600
VERDICT_MAX_TOKENS = 700
# The debate history placed in a prompt keeps its newest part: older turns were already answered.
MAX_HISTORY_CHARS = 9000
MAX_STORED_TEXT_CHARS = 8000

PENDING, RUNNING, DONE, SKIPPED, FAILED = "pending", "running", "done", "skipped", "failed"

PARSE_STRUCTURED, PARSE_LENIENT, PARSE_FAILED = "structured", "lenient", "failed"
CONVICTIONS = ("low", "medium", "high")

_RATING_BY_LOWER = {r.lower(): r for r in RATINGS}
_RATING_LABEL = re.compile(r"\brating\b[^A-Za-z]{0,6}(buy|overweight|hold|underweight|sell)\b", re.IGNORECASE)


def _now_iso() -> str:
    return utcnow_naive().isoformat() + "Z"


@dataclass
class Step:
    key: str
    role: str  # analyst | debate | manager | trader | risk | final
    title: str
    tier: str
    round: int = 0
    status: str = PENDING
    text: str = ""
    error: str | None = None
    note: str | None = None  # why a step was skipped, or what a provider said about web search
    model: str | None = None
    sources: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class CommitteeOutcome:
    steps: list[Step]
    rating: str | None = None
    summary: str | None = None
    key_risks: str | None = None
    conviction: str | None = None
    parse: str | None = None
    calls_used: int = 0
    web_search_used: bool = False
    error: str | None = None  # set when the run could not finish


# Called with the whole step list and the calls used so far, every time a step changes.
Publish = Callable[[list[dict], int], None]


def clamp_rounds(value: int) -> int:
    return max(1, min(int(value), MAX_DEBATE_ROUNDS))


def planned_steps(debate_rounds: int, risk_rounds: int) -> list[Step]:
    """Every step the run could take, in order, all pending: the live view shows the whole path."""
    steps = [Step(f"analyst_{a.key}", "analyst", a.title, ROUTINE_TIER) for a in prompts.ANALYSTS]
    for rnd in range(1, clamp_rounds(debate_rounds) + 1):
        steps.append(Step(f"bull_{rnd}", "debate", "Bull analyst", ROUTINE_TIER, rnd))
        steps.append(Step(f"bear_{rnd}", "debate", "Bear analyst", ROUTINE_TIER, rnd))
    steps.append(Step("research_manager", "manager", "Research manager", DECISION_TIER))
    steps.append(Step("trader", "trader", "Trader", DECISION_TIER))
    for rnd in range(1, clamp_rounds(risk_rounds) + 1):
        for stance in prompts.RISK_STANCES:
            steps.append(Step(f"risk_{stance}_{rnd}", "risk", prompts.RISK_STANCES[stance][0], DECISION_TIER, rnd))
    steps.append(Step("final", "final", "Portfolio manager (final rating)", DECISION_TIER))
    return steps


# ---- reading replies --------------------------------------------------------------------------


def _first_json_object(raw: str) -> dict | None:
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
        start = raw.find("{", start + 1)
    return None


def _rating_from(value: object) -> str | None:
    return _RATING_BY_LOWER.get(value.strip().strip("*").lower()) if isinstance(value, str) else None


def _str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def verdict_schema(fields: dict[str, dict]) -> dict:
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


RATING_FIELD = {"type": "string", "enum": list(RATINGS)}
MANAGER_SCHEMA = verdict_schema({"rating": RATING_FIELD, "plan": {"type": "string"}})
TRADER_SCHEMA = verdict_schema({"action": {"type": "string", "enum": ["Buy", "Hold", "Sell"]}, "reasoning": {"type": "string"}})
FINAL_SCHEMA = verdict_schema(
    {
        "rating": RATING_FIELD,
        "conviction": {"type": "string", "enum": list(CONVICTIONS)},
        "summary": {"type": "string"},
        "key_risks": {"type": "string"},
    }
)


def read_verdict(raw: str, text_field: str) -> tuple[dict, str]:
    """(fields, parse path) from a verdict reply. Structured = the whole reply is the JSON object;
    lenient = an object found inside prose or fences, or an explicit "Rating: X" label; failed = no
    rating could be read, and then none is invented (a stray rating word in prose is not enough)."""
    raw = (raw or "").strip()
    try:
        whole = json.loads(raw)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict) and _rating_from(whole.get("rating") or whole.get("action")):
        return whole, PARSE_STRUCTURED
    inner = _first_json_object(raw)
    if inner and _rating_from(inner.get("rating") or inner.get("action")):
        return inner, PARSE_LENIENT
    label = _RATING_LABEL.search(raw)
    if label:
        return {"rating": _RATING_BY_LOWER[label.group(1).lower()], text_field: raw}, PARSE_LENIENT
    return {}, PARSE_FAILED


# ---- the run ------------------------------------------------------------------------------------


class _Run:
    def __init__(self, symbol: str, pack: DataPack, llm: LLMProvider, settings: AppSettings, publish: Publish):
        self.symbol = symbol
        self.pack = pack
        self.llm = llm
        self.settings = settings
        self.publish_cb = publish
        self.max_calls = settings.committee_max_llm_calls
        self.calls = 0
        self.web_used = False
        self.steps = planned_steps(settings.committee_debate_rounds, settings.committee_risk_rounds)
        self.by_key = {s.key: s for s in self.steps}
        self.reports: dict[str, str] = {}
        self.debate_history = ""
        self.risk_history = ""

    def publish(self) -> None:
        try:
            self.publish_cb([asdict(s) for s in self.steps], self.calls)
        except Exception:  # a failing live view must never stop the run
            logger.exception("committee: publishing progress failed")

    def required_left(self) -> int:
        return sum(1 for k in ("research_manager", "trader", "final") if self.by_key[k].status == PENDING)

    def can_afford_optional(self) -> bool:
        return self.calls + 1 + self.required_left() <= self.max_calls

    def skip(self, step: Step, why: str) -> None:
        step.status, step.note = SKIPPED, why
        self.publish()

    def call(self, step: Step, prompt: str, *, research: bool = False, schema: dict | None = None, max_tokens: int) -> LLMResult:
        step.status, step.started_at = RUNNING, _now_iso()
        self.publish()
        tier: LLMTier = DECISION_TIER if step.tier == DECISION_TIER else ROUTINE_TIER
        self.calls += 1
        try:
            if research:
                result = generate_for_research(self.llm, prompt, self.settings, tier, max_tokens=max_tokens)
            else:
                result = generate_with_tier(self.llm, prompt, tier, response_schema=schema, max_tokens=max_tokens)
        except Exception as exc:  # a provider bug is a failed step, not a dead run
            logger.exception("committee: %s call raised", step.key)
            result = LLMResult(text="", provider=getattr(self.llm, "name", "?"), latency_ms=0, error=str(exc)[:200])
        step.finished_at = _now_iso()
        step.model = result.model
        if result.error or not result.text.strip():
            step.status, step.error = FAILED, (result.error or "the AI returned no text")[:300]
        else:
            step.status = DONE
            step.text = result.text.strip()[:MAX_STORED_TEXT_CHARS]
            step.sources = list(result.sources)[:20]
            if result.web_search_used:
                self.web_used = True
            if result.note:
                step.note = result.note
            step.ungrounded = find_ungrounded_figures(step.text, self.pack.snapshot, self.pack.all_data_text())
            if step.ungrounded:
                logger.warning("committee %s %s: %s", self.symbol, step.key, "; ".join(step.ungrounded))
        self.publish()
        return result

    # ---- phases

    def analysts(self) -> None:
        for analyst in prompts.ANALYSTS:
            step = self.by_key[f"analyst_{analyst.key}"]
            data = self.pack.sections.get(analyst.key)
            if data is None:
                self.skip(step, "no data was available for this report, so no AI call was spent on it")
                continue
            if not self.can_afford_optional():
                self.skip(step, "skipped: the AI call budget for this run is used up")
                continue
            prompt = prompts.analyst_prompt(analyst, self.pack.ground_truth, data)
            self.call(step, prompt, research=True, max_tokens=REPORT_MAX_TOKENS)
            if step.status == DONE:
                self.reports[analyst.key] = step.text

    def _tail(self, text: str) -> str:
        return text if len(text) <= MAX_HISTORY_CHARS else "…" + text[-MAX_HISTORY_CHARS:]

    def bull_bear(self) -> None:
        last_bull = last_bear = ""
        for rnd in range(1, clamp_rounds(self.settings.committee_debate_rounds) + 1):
            for side in ("bull", "bear"):
                step = self.by_key[f"{side}_{rnd}"]
                if not self.reports:
                    self.skip(step, "skipped: no analyst report was written, so there is nothing to debate")
                    continue
                if not self.can_afford_optional():
                    self.skip(step, "skipped: the AI call budget for this run is used up")
                    continue
                history = self._tail(self.debate_history)
                if side == "bull":
                    prompt = prompts.bull_prompt(self.symbol, self.pack.ground_truth, self.reports, history, last_bear)
                else:
                    prompt = prompts.bear_prompt(self.symbol, self.pack.ground_truth, self.reports, history, last_bull)
                self.call(step, prompt, max_tokens=TURN_MAX_TOKENS)
                if step.status == DONE:
                    line = f"{step.title}: {step.text}"
                    self.debate_history += "\n" + line
                    if side == "bull":
                        last_bull = line
                    else:
                        last_bear = line

    def risk_debate(self, trader_view: str) -> None:
        for rnd in range(1, clamp_rounds(self.settings.committee_risk_rounds) + 1):
            for stance in prompts.RISK_STANCES:
                step = self.by_key[f"risk_{stance}_{rnd}"]
                if not self.can_afford_optional():
                    self.skip(step, "skipped: the AI call budget for this run is used up")
                    continue
                prompt = prompts.risk_prompt(
                    stance, self.symbol, self.pack.ground_truth, self.reports, trader_view, self._tail(self.risk_history)
                )
                self.call(step, prompt, max_tokens=TURN_MAX_TOKENS)
                if step.status == DONE:
                    self.risk_history += f"\n{step.title}: {step.text}"

    def fail_rest(self, why: str) -> None:
        for step in self.steps:
            if step.status in (PENDING, RUNNING):
                self.skip(step, why)


def run_committee(
    symbol: str,
    data_provider: DataProvider,
    llm: LLMProvider,
    settings: AppSettings,
    publish: Publish = lambda steps, calls: None,
) -> CommitteeOutcome:
    """Run the whole committee for one symbol. Never raises for an AI failure: a required step that
    fails ends the run with `error` set and the later steps marked skipped. A price history that
    cannot be fetched raises the data layer's AllProvidersFailedError before any AI call is made."""
    pack = gather_data(symbol, data_provider)
    run = _Run(symbol, pack, llm, settings, publish)
    run.publish()
    outcome = CommitteeOutcome(steps=run.steps)

    def finish(error: str | None = None) -> CommitteeOutcome:
        outcome.calls_used, outcome.web_search_used, outcome.error = run.calls, run.web_used, error
        return outcome

    run.analysts()
    run.bull_bear()

    # Required step 1: the research manager reads the debate (or, if none ran, the reports).
    manager = run.by_key["research_manager"]
    basis = run.debate_history or prompts.reports_block(run.reports)
    run.call(
        manager,
        prompts.research_manager_prompt(symbol, pack.ground_truth, run._tail(basis)),
        schema=MANAGER_SCHEMA,
        max_tokens=VERDICT_MAX_TOKENS,
    )
    if manager.status != DONE:
        run.fail_rest("not run: the research manager step failed")
        return finish(f"The research manager step failed: {manager.error}")
    fields, parse = read_verdict(manager.text, "plan")
    research_view = (
        f"Rating: {fields.get('rating')}\n{_str(fields.get('plan')) or ''}" if parse != PARSE_FAILED else manager.text
    )

    # Required step 2: the trader.
    trader = run.by_key["trader"]
    run.call(
        trader, prompts.trader_prompt(symbol, pack.ground_truth, research_view), schema=TRADER_SCHEMA, max_tokens=VERDICT_MAX_TOKENS
    )
    if trader.status != DONE:
        run.fail_rest("not run: the trader step failed")
        return finish(f"The trader step failed: {trader.error}")
    t_fields, t_parse = read_verdict(trader.text, "reasoning")
    trader_view = (
        f"Action: {t_fields.get('action') or t_fields.get('rating')}\n{_str(t_fields.get('reasoning')) or ''}"
        if t_parse != PARSE_FAILED
        else trader.text
    )

    run.risk_debate(trader_view)

    # Required step 3: the final rating.
    final = run.by_key["final"]
    run.call(
        final,
        prompts.final_prompt(symbol, pack.ground_truth, research_view, trader_view, run._tail(run.risk_history)),
        schema=FINAL_SCHEMA,
        max_tokens=VERDICT_MAX_TOKENS,
    )
    if final.status != DONE:
        return finish(f"The final rating step failed: {final.error}")
    f_fields, f_parse = read_verdict(final.text, "summary")
    outcome.parse = f_parse
    if f_parse == PARSE_FAILED:
        # Loud and honest: no rating is invented from an unreadable reply.
        logger.warning("committee %s: final reply could not be read, no rating recorded", symbol)
        outcome.summary = final.text[:2000]
        return finish("The final reply could not be read as a rating, so none is recorded.")
    outcome.rating = _rating_from(f_fields.get("rating"))
    outcome.summary = _str(f_fields.get("summary")) or final.text[:2000]
    outcome.key_risks = _str(f_fields.get("key_risks"))
    conviction = _str(f_fields.get("conviction"))
    outcome.conviction = conviction.lower() if conviction and conviction.lower() in CONVICTIONS else None
    return finish()

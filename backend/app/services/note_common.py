"""What the morning note and the weekly digest share: sections of facts, the plain-text
layout, the optional AI paragraph and a few small helpers.

Both notes are built the same way. The facts come from our own data and rules and become
rule-based text first; that text is always complete on its own. If an AI provider is
configured and the caller asks for it, one extra paragraph that restates those facts is
added and clearly labelled. The AI is handed the facts only (marked as data) and is never
the source of a number.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar

from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.services.lesson_service import is_real_llm

logger = logging.getLogger(__name__)

# Long headlines and alert texts are cut so one of them cannot fill the message.
MAX_LINE_CHARS = 180
# Each section lists at most this many items, then says how many more there were.
MAX_ITEMS_PER_SECTION = 6
AI_PARAGRAPH_MAX_CHARS = 700
NO_AI_FOOTER = "No AI summary: this is the rule-based note."
PAPER_NOTICE = "Paper trading only: nothing here places or changes a trade."

T = TypeVar("T")


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    more: int = 0  # items left out beyond MAX_ITEMS_PER_SECTION


@dataclass
class NoteFacts:
    heading: str
    sections: list[Section] = field(default_factory=list)
    # Parts that could not be built or fetched, in plain words ("overnight futures: no fresh quote").
    unavailable: list[str] = field(default_factory=list)


@dataclass
class NoteResult:
    text: str
    ai_used: bool = False
    ai_provider: str | None = None
    unavailable: list[str] = field(default_factory=list)
    generated_at: datetime | None = None


def short(text: str, limit: int = MAX_LINE_CHARS) -> str:
    """One line, whitespace collapsed, cut with an ellipsis when too long."""
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "..."


def capped(items: list[T]) -> tuple[list[T], int]:
    """(the first MAX_ITEMS_PER_SECTION items, how many were left out)."""
    return items[:MAX_ITEMS_PER_SECTION], max(0, len(items) - MAX_ITEMS_PER_SECTION)


def safely(name: str, unavailable: list[str], build: Callable[[], T], default: T) -> T:
    """Run one part of the facts gathering. A failure is logged, listed as unavailable
    and replaced by `default`: one broken source must not cost the whole note."""
    try:
        return build()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Note section %s failed: %s", name, type(exc).__name__)
        unavailable.append(name)
        return default


def render_rule_based(facts: NoteFacts, extra_lines: list[str] | None = None) -> str:
    out = [facts.heading, PAPER_NOTICE, ""]
    for section in facts.sections:
        if not section.lines:
            continue
        out.append(section.title.upper())
        out.extend(f"- {line}" for line in section.lines)
        if section.more:
            out.append(f"- ...and {section.more} more")
        out.append("")
    if extra_lines:
        out.extend(extra_lines)
        out.append("")
    if facts.unavailable:
        out.append("Not available this time: " + "; ".join(facts.unavailable) + ".")
        out.append("")
    return "\n".join(out).rstrip()


def fact_lines(facts: NoteFacts) -> list[str]:
    """The facts as flat lines for the AI prompt (section title in front of each)."""
    lines: list[str] = []
    for section in facts.sections:
        for line in section.lines:
            lines.append(f"{section.title}: {line}")
    if facts.unavailable:
        lines.append("Not available: " + "; ".join(facts.unavailable))
    return lines


def with_ai_paragraph(
    rule_text: str,
    facts: NoteFacts,
    llm_provider: LLMProvider | None,
    *,
    use_ai: bool,
    build_prompt: Callable[[list[str]], str],
) -> NoteResult:
    """Append the labelled AI paragraph when asked for and available; otherwise label the
    note as rule-based. Goes through generate_with_fallback (routine tier), so an AI that
    errors only costs the paragraph, never the note."""
    base = NoteResult(text=f"{rule_text}\n\n{NO_AI_FOOTER}", unavailable=list(facts.unavailable))
    if not use_ai or llm_provider is None or not is_real_llm(llm_provider):
        return base
    lines = fact_lines(facts)
    if not lines:
        return base
    result = generate_with_fallback(llm_provider, build_prompt(lines), fallback_text="")
    paragraph = " ".join((result.text or "").split())
    if result.provider == "none" or not paragraph:
        return base
    model = f", {result.model}" if getattr(result, "model", None) else ""
    label = f"AI summary ({result.provider}{model}), restating the facts above:"
    return NoteResult(
        text=f"{rule_text}\n\n{label}\n{paragraph[:AI_PARAGRAPH_MAX_CHARS]}",
        ai_used=True,
        ai_provider=result.provider,
        unavailable=list(facts.unavailable),
    )


def position_r_multiple(direction: str, entry: float, stop: float, price: float) -> float | None:
    """Open result in R (multiples of the initial risk |entry - stop|), None without a risk."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    move = price - entry if direction == "long" else entry - price
    return move / risk


def fmt_money(value: float) -> str:
    return f"{'-' if value < 0 else '+'}${abs(value):,.0f}"

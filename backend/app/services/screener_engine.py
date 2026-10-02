"""The screener's pure part: which fields exist, what a filter means, and how rows
are matched and sorted. No data fetching, no files, no clock: everything here is
a function of the rows it is handed, so it is tested directly.

A field's value is None when it could not be worked out (too little history, no
fundamentals from any provider). A row with None in a field a filter looks at
never matches that filter: the screener does not guess a missing number, and the
count of rows dropped that way is reported so the result is not mistaken for "no
such stock exists".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

FieldKind = Literal["number", "text"]
# Where a field's value comes from, which decides what has to be fetched for it.
FieldSource = Literal["universe", "bars", "overview", "financials"]

NUMBER_OPERATORS = ("gt", "gte", "lt", "lte", "between", "eq", "neq")
TEXT_OPERATORS = ("eq", "neq", "contains")
# More than this many rules on one screen is a typo or a script, not a screen.
MAX_FILTERS = 12


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: FieldKind
    unit: str  # "" | "$" | "%" | "x" (a ratio to a 20-day average) | "pts" | "market cap"
    source: FieldSource
    description: str


# The catalogue. Order is the order the UI lists them in.
FIELD_CATALOGUE: tuple[FieldSpec, ...] = (
    FieldSpec("price", "Price", "number", "$", "bars", "Last daily close."),
    FieldSpec("change_pct", "Change %", "number", "%", "bars", "Change from the previous daily close to the last one."),
    FieldSpec("volume_ratio", "Volume ratio", "number", "x", "bars", "Last day's volume divided by the average of the 20 days before it."),
    FieldSpec("trend", "Trend", "text", "", "bars", "Bullish, Bearish or Neutral, from the 20 and 50 day averages (the same rule the scanner uses)."),
    FieldSpec("momentum", "Momentum", "text", "", "bars", "Strong or Weak (the same rule the scanner uses)."),
    FieldSpec("rsi14", "RSI (14)", "number", "", "bars", "14-day relative strength index."),
    FieldSpec("scanner_score", "Scanner score", "number", "pts", "bars", "The Market Scan score, 0 to 6. The volume point is only counted when volume is known."),
    FieldSpec("signal", "Signal", "text", "", "bars", "potential_setup, watching or no_signal, from the scanner score."),
    FieldSpec("pct_from_ema20", "% from EMA20", "number", "%", "bars", "How far the price is above (+) or below (-) its 20-day exponential average."),
    FieldSpec("week52_position_pct", "52-week position", "number", "%", "bars", "Where the price sits between the year's low (0) and high (100). Needs about a year of bars."),
    FieldSpec("pe_ratio", "P/E", "number", "x", "overview", "Price to earnings. Blank for companies with no positive earnings."),
    FieldSpec("market_cap", "Market cap", "number", "$", "overview", "Market capitalisation in dollars."),
    FieldSpec("revenue_growth_pct", "Revenue growth", "number", "%", "financials", "Latest fiscal year's revenue against the year before."),
    FieldSpec("sector", "Sector", "text", "", "universe", "From the symbol list (the bundled catalogue, or what was stored when the symbol was added)."),
)
FIELDS: dict[str, FieldSpec] = {f.name: f for f in FIELD_CATALOGUE}

# Fields present on every row without any fetch beyond the price bars.
ALWAYS_FIELDS = tuple(f.name for f in FIELD_CATALOGUE if f.source in ("universe", "bars"))


class FilterError(ValueError):
    """A rule or sort that cannot be evaluated; the message is shown to the user."""


@dataclass(frozen=True)
class Criterion:
    field: str
    op: str
    value: float | str
    value2: float | None = None


def _number(raw: object, what: str) -> float:
    if isinstance(raw, bool) or raw is None:
        raise FilterError(f"{what} needs a number")
    try:
        number = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise FilterError(f"{what} needs a number, got {raw!r}") from None
    if not math.isfinite(number):
        raise FilterError(f"{what} needs a finite number")
    return number


def validate_criterion(field: str, op: str, value: object, value2: object = None) -> Criterion:
    """Turn one raw rule into a Criterion, or say exactly what is wrong with it."""
    spec = FIELDS.get(field)
    if spec is None:
        raise FilterError(f"Unknown field '{field}'")
    allowed = NUMBER_OPERATORS if spec.kind == "number" else TEXT_OPERATORS
    if op not in allowed:
        raise FilterError(f"'{op}' cannot be used on {spec.label} ({spec.kind}); use one of: {', '.join(allowed)}")
    if spec.kind == "text":
        text = str(value).strip() if value is not None else ""
        if not text:
            raise FilterError(f"{spec.label} needs a value")
        return Criterion(field, op, text)
    first = _number(value, f"{spec.label}")
    if op == "between":
        second = _number(value2, f"{spec.label} (upper bound)")
        if first > second:
            raise FilterError(f"{spec.label}: the lower bound ({first:g}) is above the upper bound ({second:g})")
        return Criterion(field, op, first, second)
    return Criterion(field, op, first)


def validate_sort(field: str | None) -> str | None:
    if field is None:
        return None
    if field != "symbol" and field not in FIELDS:
        raise FilterError(f"Cannot sort by unknown field '{field}'")
    return field


def validate_columns(columns: list[str]) -> list[str]:
    for name in columns:
        if name not in FIELDS:
            raise FilterError(f"Unknown column '{name}'")
    return list(dict.fromkeys(columns))


def fields_in_use(criteria: list[Criterion], sort_field: str | None, columns: list[str]) -> set[str]:
    """Every catalogue field the screen touches, so only the fetches it needs are made."""
    used = {c.field for c in criteria} | set(columns)
    if sort_field and sort_field != "symbol":
        used.add(sort_field)
    return used


def sources_needed(used: set[str]) -> set[str]:
    return {FIELDS[name].source for name in used}


def _matches(value: object, criterion: Criterion) -> bool:
    op = criterion.op
    if isinstance(criterion.value, str):
        have = str(value).casefold()
        want = criterion.value.casefold()
        if op == "eq":
            return have == want
        if op == "neq":
            return have != want
        return want in have  # contains
    number = float(value)  # type: ignore[arg-type]
    target = float(criterion.value)
    if op == "gt":
        return number > target
    if op == "gte":
        return number >= target
    if op == "lt":
        return number < target
    if op == "lte":
        return number <= target
    if op == "between":
        return target <= number <= float(criterion.value2)  # type: ignore[arg-type]
    close = math.isclose(number, target, rel_tol=1e-9, abs_tol=1e-9)
    return close if op == "eq" else not close  # eq / neq


def apply_filters(rows: list[dict], criteria: list[Criterion]) -> tuple[list[dict], int]:
    """Rows that satisfy every criterion, and how many rows were dropped only
    because a field a rule needed was missing (a row that fails a rule on a
    value it does have is not counted there)."""
    kept: list[dict] = []
    dropped_missing = 0
    for row in rows:
        verdict = True
        missing = False
        for criterion in criteria:
            value = row.get(criterion.field)
            if value is None:
                missing = True
                continue
            if not _matches(value, criterion):
                verdict = False
                break
        if not verdict:
            continue
        if missing:
            dropped_missing += 1
            continue
        kept.append(row)
    return kept, dropped_missing


def sort_rows(rows: list[dict], field: str | None, descending: bool) -> list[dict]:
    """Sorted by `field`, rows with no value for it always last (in either
    direction), ties broken by symbol so the order is stable."""
    if field is None:
        return sorted(rows, key=lambda r: r["symbol"])
    have = [r for r in rows if r.get(field) is not None]
    lack = [r for r in rows if r.get(field) is None]

    def key(row: dict):
        value = row[field]
        return value.casefold() if isinstance(value, str) else value

    have.sort(key=lambda r: r["symbol"])
    have.sort(key=key, reverse=descending)
    return have + sorted(lack, key=lambda r: r["symbol"])

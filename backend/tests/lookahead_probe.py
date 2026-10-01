"""A probe for look-ahead: wraps a data source or a reader and checks everything it
returns against the moment it was asked from.

How it decides a value is from the future
-----------------------------------------
Every returned object is searched for the timestamps it carries, and each kind
has its own rule:

* a price bar's date: the bar must be final at the cutoff (a day's bar is not
  final until its session closed and settled), so a bar for the decision day
  itself is a leak;
* an instant (`known_at`, `published_at`, `visible_from`, ...): must not be after
  the cutoff (a fact known exactly AT the cutoff is visible);
* a date-only fact (a FINRA trading day, an earnings report day): such a source
  proves only "some time that day", so it counts as public at the END of that day
  in New York, and the day must have ended by the cutoff.

Not looked at: `effective_at` / transaction dates / period ends. Those say what a
fact is ABOUT, never when it became visible.

The probe only sees what a source hands back; a source that quietly computes its
answer from future data but returns old-looking timestamps is caught by the other
half of the harness, `assert_independent_of_future`, which changes everything
after the cutoff and demands a byte-identical answer.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.knowledge import KnownFact
from app.knowledge.point_in_time import current_as_of, end_of_local_day_utc, to_naive_utc
from app.markets import is_daily_bar_final

# Attribute names that hold the instant a value became public.
INSTANT_FIELDS = ("known_at", "visible_from", "first_known_at", "accepted_at", "published_at")
# Per result type: date attributes that are public from the END of that day.
DAY_END_FIELDS: dict[str, tuple[str, ...]] = {
    "ShortVolumeReading": ("latest_trade_date",),
    "EarningsHistoryEntry": ("date",),
}
# The symbol used to judge whether a daily bar is final (every probed series is a US equity).
BAR_FINALITY_SYMBOL = "SPY"


class LookAheadFinding(AssertionError):
    """A source returned something that was not public at the cutoff."""


@dataclass(frozen=True)
class Observation:
    kind: str  # "bar_day" | "instant" | "day_end"
    value: Any
    where: str


def _parse_instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_naive_utc(value)
    if isinstance(value, str) and value:
        try:
            return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def observe(obj: Any, where: str = "result") -> list[Observation]:
    """Every timestamp-bearing value inside `obj` (see the module docstring)."""
    found: list[Observation] = []
    if obj is None or isinstance(obj, (str, bytes, int, float, bool, datetime, date)):
        return found
    if isinstance(obj, pd.DataFrame):
        if "date" in obj.columns and len(obj):
            found.append(Observation("bar_day", pd.Timestamp(obj["date"].max()).date(), f"{where}.date[max]"))
        return found
    if isinstance(obj, (list, tuple, set)):
        for i, item in enumerate(obj):
            found.extend(observe(item, f"{where}[{i}]"))
        return found
    if isinstance(obj, dict):
        for key, item in obj.items():
            found.extend(observe(item, f"{where}[{key!r}]"))
        return found
    names = [f.name for f in dataclasses.fields(obj)] if dataclasses.is_dataclass(obj) else list(getattr(obj, "__dict__", {}))
    day_end = DAY_END_FIELDS.get(type(obj).__name__, ())
    for name in names:
        if name.startswith("_"):
            continue
        value = getattr(obj, name, None)
        if name in INSTANT_FIELDS:
            instant = _parse_instant(value)
            if instant is not None:
                found.append(Observation("instant", instant, f"{where}.{name}"))
        elif name in day_end and isinstance(value, date):
            found.append(Observation("day_end", value if not isinstance(value, datetime) else value.date(), f"{where}.{name}"))
        elif isinstance(value, (list, tuple)) or (dataclasses.is_dataclass(value) and not isinstance(value, type)):
            found.extend(observe(value, f"{where}.{name}"))
    return found


def violations(observations: list[Observation], cutoff: datetime) -> list[str]:
    cutoff = to_naive_utc(cutoff)
    bad: list[str] = []
    for ob in observations:
        if ob.kind == "bar_day" and not is_daily_bar_final(BAR_FINALITY_SYMBOL, ob.value, cutoff):
            bad.append(f"{ob.where} = bar of {ob.value}, not final at {cutoff}")
        elif ob.kind == "instant" and ob.value > cutoff:
            bad.append(f"{ob.where} = {ob.value} is after the cutoff {cutoff}")
        elif ob.kind == "day_end" and end_of_local_day_utc(ob.value) > cutoff:
            bad.append(f"{ob.where} = day {ob.value} had not ended (New York) at the cutoff {cutoff}")
    return bad


@dataclass
class ProbeRecord:
    name: str
    cutoff: datetime
    observations: list[Observation]
    problems: list[str]


@dataclass
class Probe:
    """Collects what each probed call returned. With `strict` (the default) a leak
    raises LookAheadFinding at once; otherwise read `problems`."""

    strict: bool = True
    records: list[ProbeRecord] = field(default_factory=list)

    def call(self, name: str, fn: Callable[..., Any], *args: Any, cutoff: datetime | None = None, **kwargs: Any) -> Any:
        moment = cutoff if cutoff is not None else current_as_of()
        result = fn(*args, **kwargs)
        self.check(name, result, moment)
        return result

    def check(self, name: str, result: Any, cutoff: datetime) -> None:
        observations = observe(result, name)
        problems = violations(observations, cutoff)
        self.records.append(ProbeRecord(name, to_naive_utc(cutoff), observations, problems))
        if problems and self.strict:
            raise LookAheadFinding(f"{name}: " + "; ".join(problems))

    @property
    def problems(self) -> list[str]:
        return [p for r in self.records for p in r.problems]

    @property
    def observed(self) -> int:
        return sum(len(r.observations) for r in self.records)


class ProbedProvider:
    """Any DataProvider-shaped object, with every `get_*` call checked against the
    simulated moment it was made in (call it inside `with as_of(...)`)."""

    def __init__(self, inner: Any, probe: Probe | None = None):
        self._inner = inner
        self.probe = probe or Probe()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not name.startswith("get_") or not callable(attr):
            return attr

        def probed(*args: Any, **kwargs: Any) -> Any:
            return self.probe.call(f"{name}{args}", attr, *args, **kwargs)

        return probed


def canon(obj: Any) -> Any:
    """A comparable, order-stable form of anything a reader returns, so two answers
    can be demanded to be identical rather than 'close'."""
    if isinstance(obj, pd.DataFrame):
        return ("frame", obj.to_csv(index=False))
    if isinstance(obj, KnownFact):
        return ("fact", obj.id, obj.kind, obj.symbol, obj.known_at.isoformat(), canon(obj.payload))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return (type(obj).__name__, tuple((f.name, canon(getattr(obj, f.name))) for f in dataclasses.fields(obj)))
    if isinstance(obj, (list, tuple)):
        return [canon(x) for x in obj]
    if isinstance(obj, dict):
        return sorted(((str(k), canon(v)) for k, v in obj.items()), key=lambda kv: kv[0])
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float) and math.isnan(obj):
        return "nan"
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return (type(obj).__name__, canon({k: v for k, v in vars(obj).items() if not k.startswith("_")}))
    return obj


def assert_independent_of_future(ask: Callable[[], Any], rewrite_future: Callable[[], None], label: str) -> Any:
    """Ask, rewrite everything after the cutoff, ask again: the answers must be identical."""
    first = canon(ask())
    rewrite_future()
    second = canon(ask())
    if first != second:
        raise LookAheadFinding(f"{label}: the answer changed when only data after the cutoff changed")
    return first

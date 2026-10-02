"""Silent ("shadow") signals: new evidence that is recorded, not yet scored.

A new signal does not get to move confidence just because it sounds sensible.
It starts silent: on every plan (traded or rejected) the engine records what
the signal read and the points it WOULD have added, but those points are never
summed into `confidence_score`, never change the direction, the entry, stop or
targets, and never touch position size. After enough plans have piled up, a
backtest can compare "plans where the silent signal agreed" with "plans where
it did not" and show whether it beats luck. Only then is it promoted: its
points move into real scoring under a cap and the strategy version changes.

Promoting a signal is therefore a deliberate, visible act:
  1. add its name to `LIVE_SIGNALS` below,
  2. add its capped points to the real scoring in `trade_plan_service`
     (and to `MAX_SCORE_FOR_CONFIDENCE` if the maximum grows),
  3. `LIVE_SIGNALS` is part of the strategy fingerprint, so the next plan gets
     a new strategy version automatically.

How a later signal registers itself (this is the whole API):

    from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal

    @shadow_signal("my_signal")
    def _my_signal(context: ShadowContext) -> ShadowSignal:
        # context.symbol, context.direction ("long" | "short" | None),
        # context.session (a DB session or None), read dated facts only
        # through app.knowledge readers so a backtest cannot see the future.
        return ShadowSignal(name="my_signal", value="...", would_score=-1,
                            reason="why", available=True)

A scorer that raises, or returns the wrong thing, is logged and recorded as
unavailable: a silent signal must never be able to break a plan.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlmodel import Session

logger = logging.getLogger(__name__)

# Signals promoted to real scoring. Empty on purpose: every signal ships silent.
# Part of the strategy fingerprint (app/strategy/snapshot.py), so promoting one
# starts a new strategy version.
LIVE_SIGNALS: set[str] = set()

# A silent signal's "would have added" points are never larger than this, so
# the future promotion cannot surprise anyone with a bigger swing than shown.
SHADOW_POINTS_CAP = 1


@dataclass(frozen=True)
class ShadowSignal:
    """One silent signal's reading for one plan.

    `would_score` is direction-signed and capped: positive supports the trade,
    negative argues against it. `available=False` means there was no data to
    read (never a guess): then `would_score` is 0 and `value` is None.
    """

    name: str
    value: str | None
    would_score: int
    reason: str
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowContext:
    """What a silent scorer may look at. Deliberately small."""

    symbol: str
    direction: str | None  # "long" | "short" | None (no clear trend)
    session: Session | None = None


ShadowScorer = Callable[[ShadowContext], ShadowSignal]

_REGISTRY: dict[str, ShadowScorer] = {}


def register_shadow_signal(name: str, scorer: ShadowScorer) -> None:
    """Add a scorer under `name` (replacing a previous one of the same name)."""
    if not name or not name.strip():
        raise ValueError("a shadow signal needs a name")
    _REGISTRY[name] = scorer


def shadow_signal(name: str) -> Callable[[ShadowScorer], ShadowScorer]:
    """Decorator form of `register_shadow_signal`."""

    def decorator(scorer: ShadowScorer) -> ShadowScorer:
        register_shadow_signal(name, scorer)
        return scorer

    return decorator


def registered_shadow_signals() -> list[str]:
    _load_builtin_signals()
    return sorted(_REGISTRY)


def _load_builtin_signals() -> None:
    # Imported lazily: the built-in scorers import this module to register.
    from app.analysis import short_volume_scoring  # noqa: F401
    from app.analysis import filing_8k_scoring  # noqa: F401
    from app.analysis import fed_event_window  # noqa: F401
    from app.analysis import post_mentions  # noqa: F401
    from app.analysis import news_card_scoring  # noqa: F401
    from app.analysis import fund_signals  # noqa: F401
    from app.analysis import congress_scoring  # noqa: F401


def _unavailable(name: str, reason: str) -> ShadowSignal:
    return ShadowSignal(name=name, value=None, would_score=0, reason=reason, available=False)


def evaluate_shadow_signals(context: ShadowContext) -> list[ShadowSignal]:
    """Run every registered silent scorer that is not already live.

    Never raises: one scorer's failure is logged and shows up as an
    unavailable signal, and the other signals still run.
    """
    _load_builtin_signals()
    results: list[ShadowSignal] = []
    for name in sorted(_REGISTRY):
        if name in LIVE_SIGNALS:
            continue  # promoted: it is in real scoring now, not shadowed
        try:
            signal = _REGISTRY[name](context)
            if not isinstance(signal, ShadowSignal):
                raise TypeError(f"scorer returned {type(signal).__name__}, not ShadowSignal")
            # Enforce the cap here so a scorer cannot exceed what the UI promises.
            capped = max(-SHADOW_POINTS_CAP, min(SHADOW_POINTS_CAP, int(signal.would_score)))
            if capped != signal.would_score:
                signal = ShadowSignal(signal.name, signal.value, capped, signal.reason, signal.available)
            results.append(signal)
        except Exception as exc:  # noqa: BLE001 - a silent signal must never break a plan
            logger.exception("shadow signal %s failed for %s", name, context.symbol)
            results.append(_unavailable(name, f"Could not be read ({type(exc).__name__})."))
    return results


def shadow_signals_to_json(signals: list[ShadowSignal]) -> str:
    return json.dumps([s.to_dict() for s in signals], separators=(",", ":"))


def shadow_signals_from_json(raw: str | None) -> list[dict[str, Any]] | None:
    """The stored list, or None for a plan from before silent signals existed
    (or an unreadable value: shown as absent rather than failing the response)."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, list) else None

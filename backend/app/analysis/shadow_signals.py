"""One evidence reading: the shape the `build_*_signal` readers return (and the AI committee's data pack shows).

Swing trading once recorded these silently on every plan and scored them; that machinery is gone, only the
reading itself is left.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ShadowSignal:
    """One reading for one symbol.

    `would_score` is direction-signed and capped: positive supports a trade, negative argues against it.
    `available=False` means there was no data to read (never a guess): then `would_score` is 0 and `value` is None.
    """

    name: str
    value: str | None
    would_score: int
    reason: str
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

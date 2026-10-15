from __future__ import annotations
from pydantic import BaseModel


class ShadowSignalOut(BaseModel):
    name: str
    value: str | None = None
    would_score: int = 0
    reason: str = ""
    available: bool = True


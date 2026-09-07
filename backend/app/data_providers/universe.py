from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
UNIVERSE_FILE = DATA_DIR / "sp500.csv"


@dataclass
class UniverseEntry:
    symbol: str
    name: str
    sector: str


@lru_cache
def load_universe() -> list[UniverseEntry]:
    with UNIVERSE_FILE.open(newline="", encoding="utf-8") as f:
        return [UniverseEntry(**row) for row in csv.DictReader(f)]


def get_default_watchlist(n: int = 50) -> list[str]:
    universe = load_universe()
    return [entry.symbol for entry in universe[:n]]

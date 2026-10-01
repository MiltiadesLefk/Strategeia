"""The bundled stock list (backend/data/sp500.csv) and the script that rebuilds it.

Nothing here touches the network: the data-file tests read the committed CSV, and
the script tests feed its pure functions a tiny made-up table.
"""

from __future__ import annotations

import csv
import importlib.util
import re
import sys
from pathlib import Path

import pytest
from app.data_providers.universe import UNIVERSE_FILE

ROOT = Path(__file__).resolve().parents[2]

# Loaded by path: scripts/ is a folder of command-line tools, not a package.
_spec = importlib.util.spec_from_file_location("refresh_universe", ROOT / "scripts" / "refresh_universe.py")
refresh_universe = importlib.util.module_from_spec(_spec)
_previous_bytecode_flag = sys.dont_write_bytecode
sys.dont_write_bytecode = True  # don't leave a scripts/__pycache__ behind
try:
    _spec.loader.exec_module(refresh_universe)
finally:
    sys.dont_write_bytecode = _previous_bytecode_flag

ALLOWED_SECTORS = {
    "Crypto",
    "Technology",
    "Healthcare",
    "Financials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Communication Services",
    "Industrials",
    "Energy",
    "Utilities",
    "Materials",
    "Real Estate",
}

# The hand-picked rows at the top. The default scan takes the first 50 rows, so
# their order is what an unconfigured install scans.
CURATED_HEAD = [
    "BTC-USD", "ETH-USD", "SOL-USD", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "CVX",
]
CURATED_TAIL = ["BLK", "NOW", "PLD", "ELV", "QCOM"]


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with UNIVERSE_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["symbol", "name", "sector"]
        return list(reader)


# --- the data file ----------------------------------------------------------


def test_row_count_covers_the_whole_index(rows):
    stocks = [r for r in rows if r["sector"] != "Crypto"]
    assert 495 <= len(stocks) <= 510
    assert len(rows) - len(stocks) == 3


def test_symbols_are_unique(rows):
    symbols = [r["symbol"] for r in rows]
    assert len(symbols) == len(set(symbols))


def test_symbols_are_in_yfinance_format(rows):
    for r in rows:
        assert re.fullmatch(r"[A-Z]{1,5}(-[A-Z]{1,3})?", r["symbol"]), r["symbol"]
    # Dotted share classes are written with a dash, which is what yfinance expects.
    symbols = {r["symbol"] for r in rows}
    assert {"BRK-B", "BF-B"} <= symbols
    assert not any("." in s for s in symbols)


def test_every_row_has_a_known_sector_and_a_name(rows):
    for r in rows:
        assert r["sector"] in ALLOWED_SECTORS, r
        assert r["name"], r


def test_no_whitespace_or_non_ascii_artefacts(rows):
    for r in rows:
        for value in r.values():
            assert value == " ".join(value.split()), repr(value)
            assert value.isascii(), repr(value)


def test_curated_rows_stay_first_and_in_order(rows):
    symbols = [r["symbol"] for r in rows]
    assert symbols[: len(CURATED_HEAD)] == CURATED_HEAD
    assert symbols[59:64] == CURATED_TAIL
    assert [r["sector"] for r in rows[:3]] == ["Crypto"] * 3
    # Everything after the curated block is sorted by symbol.
    appended = symbols[refresh_universe.CURATED_ROW_COUNT :]
    assert appended == sorted(appended)


def test_first_fifty_rows_are_the_curated_block_not_the_appended_names(rows):
    # The default scan takes the first 50 rows of the file, so none of the appended
    # (alphabetical) names may have crept into that window.
    first_fifty = [r["symbol"] for r in rows[:50]]
    assert first_fifty[:20] == CURATED_HEAD
    assert "A" not in first_fifty
    assert len(rows) > 500


def test_sector_of_appended_and_curated_names(rows):
    sectors = {r["symbol"]: r["sector"] for r in rows}
    assert sectors["BRK-B"] == "Financials"
    assert sectors["AAPL"] == "Technology"
    assert sectors["LLY"] == "Healthcare"


# --- the refresh script's pure functions ---------------------------------


SOURCE_TABLE = (
    "Symbol,Security,GICS Sector,GICS Sub-Industry\n"
    "MMM,3M,Industrials,Conglomerates\n"
    "BRK.B,Berkshire Hathaway,Financials,Insurance\n"
    "BF.B,Brown–Forman,Consumer Staples,Distillers\n"
    "AAPL,  Apple   Inc. ,Information Technology,Hardware\n"
    "LLY,Eli Lilly,Health Care,Pharma\n"
)


def test_normalise_symbol():
    assert refresh_universe.normalise_symbol("BRK.B") == "BRK-B"
    assert refresh_universe.normalise_symbol(" bf.b ") == "BF-B"
    assert refresh_universe.normalise_symbol("AAPL") == "AAPL"


def test_map_sector_renames_gics_names_and_rejects_unknown():
    assert refresh_universe.map_sector("Information Technology") == "Technology"
    assert refresh_universe.map_sector("Health Care") == "Healthcare"
    assert refresh_universe.map_sector(" Energy ") == "Energy"
    with pytest.raises(ValueError):
        refresh_universe.map_sector("Space Mining")


def test_parse_constituents_normalises_everything():
    parsed = refresh_universe.parse_constituents(SOURCE_TABLE)
    assert [r["symbol"] for r in parsed] == ["MMM", "BRK-B", "BF-B", "AAPL", "LLY"]
    assert parsed[2]["name"] == "Brown-Forman"
    assert parsed[3] == {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"}
    assert parsed[4]["sector"] == "Healthcare"


def _row(symbol, sector="Technology"):
    return {"symbol": symbol, "name": symbol + " Inc.", "sector": sector}


def test_merge_keeps_curated_rows_verbatim_then_sorts_the_rest(monkeypatch):
    monkeypatch.setattr(refresh_universe, "CURATED_ROW_COUNT", 2)
    existing = [_row("ZZ-USD", "Crypto"), {"symbol": "MSFT", "name": "Old Name", "sector": "Technology"}, _row("OLDAPPEND")]
    constituents = [_row("MSFT"), _row("ZED"), _row("ABC"), _row("MSFT")]
    merged = refresh_universe.merge(existing, constituents)
    # The curated MSFT keeps its old name; the source's MSFT is not added again; an
    # old appended row that left the index is dropped; the rest is alphabetical.
    assert [r["symbol"] for r in merged] == ["ZZ-USD", "MSFT", "ABC", "ZED"]
    assert merged[1]["name"] == "Old Name"


def test_validate_flags_the_problems_that_must_block_a_write(monkeypatch):
    monkeypatch.setattr(refresh_universe, "CURATED_ROW_COUNT", 1)
    monkeypatch.setattr(refresh_universe, "MIN_STOCK_ROWS", 2)
    monkeypatch.setattr(refresh_universe, "MAX_STOCK_ROWS", 4)
    existing = [_row("BTC-USD", "Crypto")]
    good = [_row("BTC-USD", "Crypto"), _row("AAA"), _row("BRK-B")]
    assert refresh_universe.validate(good, existing) == []

    assert any("duplicate" in p for p in refresh_universe.validate(good + [_row("AAA")], existing))
    assert any("blank sector" in p for p in refresh_universe.validate(good + [_row("CCC", "")], existing))
    assert any("yfinance" in p for p in refresh_universe.validate(good + [_row("BRK.B")], existing))
    assert any("outside" in p for p in refresh_universe.validate(good[:2], existing))
    assert any("curated" in p for p in refresh_universe.validate([_row("ETH-USD", "Crypto")] + good[1:], existing))


def test_sec_cross_check_reports_symbols_the_sec_file_lacks():
    payload = {"0": {"cik_str": 1, "ticker": "AAPL", "title": "Apple"}, "1": {"cik_str": 2, "ticker": "BRK-B", "title": "Berkshire"}}
    tickers = refresh_universe.parse_sec_tickers(payload)
    assert tickers == {"AAPL", "BRK-B"}
    assert refresh_universe.missing_from_sec(["AAPL", "BRK-B", "ZZZZ"], tickers) == ["ZZZZ"]


def test_format_csv_round_trips_names_with_commas():
    rows = [{"symbol": "NVR", "name": "NVR, Inc.", "sector": "Consumer Discretionary"}]
    text = refresh_universe.format_csv(rows)
    assert text.splitlines()[0] == "symbol,name,sector"
    assert '"NVR, Inc."' in text

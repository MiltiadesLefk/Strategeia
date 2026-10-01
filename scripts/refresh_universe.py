"""Rebuild backend/data/sp500.csv from the current S&P 500 constituent list.

The app's stock list is that CSV (columns symbol,name,sector). The first rows are a
hand-picked set (three crypto pairs, then large US stocks): the default scan, the
dashboard and the Docker NVDA/AAPL filter all use the first `scan_universe_size`
rows, so those rows must stay first and in the same order. This script keeps them
exactly as they are and appends every other current index member after them,
sorted by symbol.

    backend/.venv/Scripts/python scripts/refresh_universe.py             # fetch, validate, write
    backend/.venv/Scripts/python scripts/refresh_universe.py --dry-run   # fetch, validate, print, write nothing
    backend/.venv/Scripts/python scripts/refresh_universe.py --no-sec    # skip the SEC cross-check

Source: the "S&P 500 Companies" dataset (github.com/datasets/s-and-p-500-companies),
a public-domain (PDDL) table that its maintainers rebuild from Wikipedia's list of
S&P 500 companies and publish on a schedule. S&P's own list is not freely
redistributable and is not used. The list is only today's members: a backtest
over past years that uses it sees the survivors, not the companies that left the
index (survivorship bias).

Every symbol is then checked against the SEC's own ticker file
(sec.gov/files/company_tickers.json, one request) so a typo or a ticker that no
longer trades cannot ship. Symbols the SEC file does not list are printed and the
script still writes the file (a share class the SEC maps under another ticker is
normal); it refuses to write if the checks that matter fail: a blank sector,
duplicate symbols, an unexpected row count, a symbol that is not in yfinance
format, or the curated rows changing.

Run it from the repo root. Only the standard library and the app's own SEC
User-Agent helper are used.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
UNIVERSE_FILE = BACKEND_DIR / "data" / "sp500.csv"

CONSTITUENTS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

COLUMNS = ["symbol", "name", "sector"]

# The sector words the app uses. The source follows GICS naming, which differs
# for two sectors: map those, and fail loudly on any name that is neither.
SECTOR_MAP = {
    "Information Technology": "Technology",
    "Health Care": "Healthcare",
    "Industrials": "Industrials",
    "Financials": "Financials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Materials": "Materials",
    "Communication Services": "Communication Services",
    "Energy": "Energy",
}
CRYPTO_SECTOR = "Crypto"
ALLOWED_SECTORS = set(SECTOR_MAP.values()) | {CRYPTO_SECTOR}

# The S&P 500 holds about 500 companies (a few have two share classes, so ~503
# lines). Anything far outside this range means a broken download, not a real
# change in the index.
MIN_STOCK_ROWS = 495
MAX_STOCK_ROWS = 510

# A yfinance symbol: capital letters, optionally "-" plus a share-class letter
# (BRK-B), or "-USD" for crypto. Dots, spaces and lowercase are all errors.
SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,3})?$")


def normalise_symbol(raw: str) -> str:
    """Wikipedia/exchange style (BRK.B) to yfinance style (BRK-B)."""
    return raw.strip().upper().replace(".", "-")


def clean_text(raw: str) -> str:
    """Collapse stray whitespace and fold to plain ASCII (an en dash becomes "-",
    "e with an acute accent" becomes "e"), so the file stays ASCII like the rest
    of it and a name can't carry a trailing space or a character a console or
    spreadsheet would garble."""
    folded = raw.replace("–", "-").replace("—", "-")
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    return " ".join(folded.split())


def map_sector(raw: str) -> str:
    key = clean_text(raw)
    try:
        return SECTOR_MAP[key]
    except KeyError:
        raise ValueError(f"unknown sector {raw!r}: add it to SECTOR_MAP") from None


def parse_constituents(csv_text: str) -> list[dict[str, str]]:
    """Source CSV (Symbol, Security, GICS Sector, ...) to our rows, in source order."""
    rows = []
    for record in csv.DictReader(io.StringIO(csv_text)):
        rows.append(
            {
                "symbol": normalise_symbol(record["Symbol"]),
                "name": clean_text(record["Security"]),
                "sector": map_sector(record["GICS Sector"]),
            }
        )
    return rows


def read_existing(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


# 3 crypto pairs + 61 large US stocks. The default scan takes the first 50 rows,
# so this block's order is what an unconfigured install scans. Everything after
# it is regenerated on every run.
CURATED_ROW_COUNT = 64


def curated_prefix(existing: list[dict[str, str]]) -> list[dict[str, str]]:
    """The hand-picked leading rows, kept verbatim. They are pinned by count
    because the rows appended by an earlier run follow them in the same file."""
    return existing[:CURATED_ROW_COUNT]


def merge(existing: list[dict[str, str]], constituents: list[dict[str, str]]) -> list[dict[str, str]]:
    """Curated rows verbatim and in order, then the remaining constituents by symbol."""
    head = curated_prefix(existing)
    taken = {row["symbol"] for row in head}
    tail = sorted(
        (row for row in constituents if row["symbol"] not in taken),
        key=lambda row: row["symbol"],
    )
    return head + tail


def validate(rows: list[dict[str, str]], existing: list[dict[str, str]]) -> list[str]:
    """Hard problems; an empty list means the file is safe to write."""
    problems: list[str] = []
    symbols = [row["symbol"] for row in rows]
    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
    if duplicates:
        problems.append(f"duplicate symbols: {duplicates}")
    for row in rows:
        if not row["sector"]:
            problems.append(f"blank sector for {row['symbol']}")
        elif row["sector"] not in ALLOWED_SECTORS:
            problems.append(f"sector {row['sector']!r} for {row['symbol']} is not an allowed sector")
        if not row["name"]:
            problems.append(f"blank name for {row['symbol']}")
        if row["sector"] == CRYPTO_SECTOR:
            if not row["symbol"].endswith("-USD"):
                problems.append(f"crypto row {row['symbol']} is not a -USD pair")
        elif not SYMBOL_PATTERN.match(row["symbol"]):
            problems.append(f"symbol {row['symbol']!r} is not in yfinance format")
    stocks = [row for row in rows if row["sector"] != CRYPTO_SECTOR]
    if not MIN_STOCK_ROWS <= len(stocks) <= MAX_STOCK_ROWS:
        problems.append(f"{len(stocks)} stock rows is outside {MIN_STOCK_ROWS}-{MAX_STOCK_ROWS}")
    if rows[:CURATED_ROW_COUNT] != existing[:CURATED_ROW_COUNT]:
        problems.append("the curated leading rows changed")
    return problems


def missing_from_sec(symbols: list[str], sec_tickers: set[str]) -> list[str]:
    """Stock symbols the SEC ticker file does not list. The SEC writes share classes
    with a dash too (BRK-B), so a plain membership test is the right check."""
    return [s for s in symbols if s not in sec_tickers]


def parse_sec_tickers(payload: dict) -> set[str]:
    """company_tickers.json is {"0": {"cik_str":..., "ticker":..., "title":...}, ...}."""
    return {normalise_symbol(entry["ticker"]) for entry in payload.values() if entry.get("ticker")}


def format_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _fetch(url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="validate and report, write nothing")
    parser.add_argument("--no-sec", action="store_true", help="skip the SEC ticker cross-check")
    args = parser.parse_args()

    existing = read_existing(UNIVERSE_FILE)
    print(f"Fetching {CONSTITUENTS_URL}")
    source_text = _fetch(CONSTITUENTS_URL, {"User-Agent": "Strategeia-universe-refresh"}).decode("utf-8-sig")
    constituents = parse_constituents(source_text)
    print(f"  {len(constituents)} constituent lines")

    rows = merge(existing, constituents)
    problems = validate(rows, existing)

    # The curated block may name a company that has since left the index; that is
    # reported, not an error, because those rows are kept on purpose.
    source_symbols = {row["symbol"] for row in constituents}
    gone = [row["symbol"] for row in rows[:CURATED_ROW_COUNT] if row["sector"] != CRYPTO_SECTOR and row["symbol"] not in source_symbols]
    if gone:
        print(f"NOTE curated rows no longer in the index (kept): {gone}")

    stock_symbols = [row["symbol"] for row in rows if row["sector"] != CRYPTO_SECTOR]
    if args.no_sec:
        print("SEC cross-check skipped")
    else:
        # Importing the app's settings generates login secrets under backend/runtime
        # when none are configured. This script serves nothing, so it opts out of
        # that side effect for its own process only.
        os.environ.setdefault("ALLOW_UNAUTHENTICATED_API", "true")
        sys.path.insert(0, str(BACKEND_DIR))
        from app.data_providers.sec_edgar_provider import _headers

        print(f"Fetching {SEC_TICKERS_URL}")
        sec_tickers = parse_sec_tickers(json.loads(_fetch(SEC_TICKERS_URL, _headers())))
        absent = missing_from_sec(stock_symbols, sec_tickers)
        print(f"  {len(sec_tickers)} SEC tickers; {len(absent)} of {len(stock_symbols)} stock symbols not in it")
        if absent:
            names = {row["symbol"]: row["name"] for row in rows}
            for symbol in absent:
                print(f"  NOT IN SEC FILE: {symbol} ({names[symbol]})")

    stock_count = sum(1 for row in rows if row["sector"] != CRYPTO_SECTOR)
    print(f"{len(rows)} rows ({stock_count} stocks + {len(rows) - stock_count} crypto); {len(rows) - CURATED_ROW_COUNT} appended after the curated {CURATED_ROW_COUNT}")

    if problems:
        print("REFUSING TO WRITE:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    UNIVERSE_FILE.write_text(format_csv(rows), encoding="utf-8", newline="")
    print(f"wrote {UNIVERSE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

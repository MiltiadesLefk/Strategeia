"""Download daily price history once and keep it on disk (runtime/history.db).

A backtest needs years of daily bars for many symbols. Fetching them per run is
thousands of requests; this fetches each symbol once (about one request for ten
years), then later runs only add the newest few days. Re-running is safe and
cheap: symbols that are already current cost nothing.

    backend/.venv/Scripts/python scripts/preload_history.py AAPL MSFT
    backend/.venv/Scripts/python scripts/preload_history.py --benchmarks             # SPY and ^VIX
    backend/.venv/Scripts/python scripts/preload_history.py --sp500 --benchmarks     # the bundled S&P 500 list
    backend/.venv/Scripts/python scripts/preload_history.py AAPL --years 15
    backend/.venv/Scripts/python scripts/preload_history.py AAPL --force             # refetch and replace
    backend/.venv/Scripts/python scripts/preload_history.py --report                 # what is held, no network

Symbols are fetched one at a time with a short pause between network requests
(--pace). A symbol that fails is reported and skipped; the stored data for it, if
any, is left alone. Exit code 1 if any symbol failed. Only daily bars are stored.

--sp500 reads backend/data/sp500.csv. If STRATEGEIA_DEV_TICKERS is set it narrows
that list, as it does everywhere else in the app.

Run it from the repo root. It writes only runtime/history.db (beside settings.json,
or wherever SETTINGS_PATH points); it never touches the trading database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

BENCHMARKS = ["SPY", "^VIX"]


def _print_report(store, symbols: list[str]) -> None:
    summary = store.summary()
    print(f"{summary.symbols} symbols, {summary.bars} bars, {summary.file_bytes / 1_000_000:.1f} MB in {summary.path}")
    for symbol in symbols or []:
        cov = store.coverage(symbol)
        if cov is None:
            print(f"  {symbol:<8} nothing stored")
        else:
            print(
                f"  {symbol:<8} {cov.first_date} .. {cov.last_date}  {cov.bar_count} bars  "
                f"longest gap {cov.max_gap_days}d  source {cov.source}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download daily price history into runtime/history.db.")
    parser.add_argument("symbols", nargs="*", help="tickers, e.g. AAPL MSFT ^VIX")
    parser.add_argument("--years", type=int, default=10, help="how many years back to hold (default 10)")
    parser.add_argument("--benchmarks", action="store_true", help="also fetch SPY and ^VIX")
    parser.add_argument("--sp500", action="store_true", help="also fetch the bundled S&P 500 list")
    parser.add_argument("--force", action="store_true", help="refetch and replace instead of extending")
    parser.add_argument("--pace", type=float, default=None, help="seconds to wait between network requests")
    parser.add_argument("--report", action="store_true", help="print what is stored and exit (no network)")
    args = parser.parse_args(argv)

    from app.data_providers.history_store import DEFAULT_PACE_SECONDS, get_history_store

    symbols: list[str] = [s.upper() for s in args.symbols]
    if args.benchmarks:
        symbols += BENCHMARKS
    if args.sp500:
        from app.data_providers.universe import load_universe

        symbols += [entry.symbol for entry in load_universe()]
    symbols = list(dict.fromkeys(symbols))

    store = get_history_store()
    if args.report:
        _print_report(store, symbols)
        return 0
    if not symbols:
        parser.error("name at least one symbol, or use --benchmarks / --sp500 / --report")
    if args.years < 1:
        parser.error("--years must be at least 1")

    def progress(index: int, total: int, report) -> None:
        detail = f"+{report.bars_added} bars from {report.source}" if report.fetched and report.ok else (report.error or "")
        print(f"[{index}/{total}] {report.symbol:<8} {report.status:<24} {detail}", flush=True)

    pace = DEFAULT_PACE_SECONDS if args.pace is None else args.pace
    reports = store.ensure_history(symbols, years=args.years, pace_seconds=pace, progress=progress, force=args.force)
    failed = [r for r in reports if not r.ok]
    print(f"\nDone: {len(reports) - len(failed)} ok, {len(failed)} failed.")
    for r in failed:
        print(f"  {r.symbol}: {r.error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

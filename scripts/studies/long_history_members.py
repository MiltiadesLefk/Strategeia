"""Test B step 1: parse Wikipedia's changes table (saved HTML), print membership counts, write the ticker list. Read-only.

    backend/.venv/Scripts/python scripts/studies/long_history_members.py
Needs runtime/wiki_hist.html (the "Historical components of the S&P 500" page, saved by hand/curl).
Writes runtime/longhist_changes.csv and runtime/longhist_tickers.txt (every ticker ever a member since 2010-01-01).
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd  # noqa: E402

from app.catalyst.longhist import Change, members_on, norm_ticker  # noqa: E402

START = date(2010, 1, 1)


def current() -> set[str]:
    with open(ROOT / "backend" / "data" / "sp500.csv", newline="") as f:
        return {norm_ticker(r["symbol"]) for r in csv.DictReader(f) if not r["symbol"].endswith("-USD")}


def load_changes(path: Path | None = None) -> list[Change]:
    html = (path or ROOT / "runtime" / "wiki_hist.html").read_bytes()
    t = pd.read_html(io.BytesIO(html))[0]
    t.columns = ["date", "add_t", "add_n", "rem_t", "rem_n", "reason", "refs"]
    out = []
    for _, r in t.iterrows():
        d = pd.to_datetime(str(r["date"]), errors="coerce")
        if pd.isna(d):
            continue
        a = norm_ticker(str(r["add_t"])) if pd.notna(r["add_t"]) else None
        rm = norm_ticker(str(r["rem_t"])) if pd.notna(r["rem_t"]) else None
        out.append(Change(d.date(), a, rm))
    return out


def reasons() -> list[tuple[date, str, str, str]]:
    t = pd.read_html(io.BytesIO((ROOT / "runtime" / "wiki_hist.html").read_bytes()))[0]
    t.columns = ["date", "add_t", "add_n", "rem_t", "rem_n", "reason", "refs"]
    return [(pd.to_datetime(str(r["date"]), errors="coerce"), str(r["add_t"]), str(r["rem_t"]), str(r["reason"])) for _, r in t.iterrows()]


def main() -> int:
    cur = current()
    ch = [c for c in load_changes() if c.day >= START]
    allch = load_changes()
    print(f"current non-crypto members in sp500.csv: {len(cur)}; changes parsed: {len(allch)} (since {START}: {len(ch)}); "
          f"oldest {min(c.day for c in allch)}, newest {max(c.day for c in allch)}")
    for d in (date(2011, 1, 31), date(2015, 12, 31), date(2020, 12, 31), date(2025, 12, 31)):
        print(f"members on {d}: {len(members_on(cur, ch, d))}")
    ever = set(cur)
    for c in ch:
        if c.added:
            ever.add(c.added)
        if c.removed:
            ever.add(c.removed)
    removed_only = sorted(ever - cur)
    print(f"tickers ever a member since {START}: {len(ever)}; not in the current list (removed): {len(removed_only)}")
    (ROOT / "runtime" / "longhist_tickers.txt").write_text("\n".join(sorted(ever)) + "\n")
    with open(ROOT / "runtime" / "longhist_changes.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "added", "removed"])
        for c in allch:
            w.writerow([c.day, c.added or "", c.removed or ""])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

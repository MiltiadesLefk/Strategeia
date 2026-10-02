"""Compare Kronos price forecasts with what actually happened. Standalone; not part of the app.

Kronos (https://github.com/shiyu-coder/Kronos, MIT) is a pretrained model that reads a window of
daily candles and samples possible future candles. This script asks one question: on about 20
symbols, over many past dates, does its forecast of the next few days beat simple baselines?

It never touches the app, the database or the settings. It needs PyTorch and the model files,
which are NOT in requirements.txt:

    backend/.venv/Scripts/pip install -r backend/requirements-ml.txt
    backend/.venv/Scripts/python scripts/kronos_experiment.py --dry-run     # no model, no download
    backend/.venv/Scripts/python scripts/kronos_experiment.py               # the real run

The first real run clones Kronos (shallow) into backend/runtime/kronos_src and downloads the weights from
Hugging Face (NeoQuasar/Kronos-small, MIT per its model card; the tokenizer is NeoQuasar/Kronos-Tokenizer-base).
A CPU run with the defaults (20 symbols x 12 dates x 8 sampled paths) is slow; use --symbols,
--dates and --paths to make it smaller first. Results go to backend/runtime/kronos_experiment/.

How each forecast is scored. At each past date T, the model sees only bars up to T (the closing
bar of T included) and samples PATHS futures HORIZON trading days long. We compare the forecast
return (the last forecast close over the close at T) with the real one:
  - direction hit rate of the median path, against the share of dates that really went up;
  - rank correlation (IC) between the median forecast return and the real return;
  - mean absolute error against two baselines: "no change" (0%) and "same as the last HORIZON days";
  - the share of paths ending above T's close, treated as P(up): Brier score against always saying 0.5
    (always 0.5), and how often the real return fell inside the 10th-90th percentile of the paths.
Dates overlap when the spacing is shorter than the horizon, so treat the numbers as rough: the
count of dates is printed with every figure. The result is evidence, not a trading rule.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO_DIR / "backend" / "runtime"  # git-ignored
KRONOS_REPO_URL = "https://github.com/shiyu-coder/Kronos.git"
TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
MODEL_ID = "NeoQuasar/Kronos-small"
MAX_CONTEXT = 512  # Kronos-small / base limit

# A mixed set: large caps across sectors, two ETFs, two crypto. Edit freely.
DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "JNJ", "PG",
    "KO", "WMT", "CAT", "UNH", "HD", "NFLX", "AMD", "SPY", "QQQ", "BTC-USD",
]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare Kronos forecasts with real prices.")
    p.add_argument("--symbols", help="comma-separated list (default: a built-in 20)")
    p.add_argument("--horizon", type=int, default=5, help="trading days to forecast (default 5)")
    p.add_argument("--lookback", type=int, default=400, help="bars of context, at most 512 (default 400)")
    p.add_argument("--dates", type=int, default=12, help="past dates per symbol (default 12)")
    p.add_argument("--span-days", type=int, default=500,
                   help="spread the dates over this many recent trading days (default 500)")
    p.add_argument("--paths", type=int, default=8, help="sampled futures per date (default 8)")
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--top-p", type=float, default=0.9)
    p.add_argument("--model", default=MODEL_ID, help="Hugging Face model id (default %(default)s)")
    p.add_argument("--tokenizer", default=TOKENIZER_ID)
    p.add_argument("--kronos-dir", help="existing Kronos checkout (default: clone to backend/runtime/kronos_src)")
    p.add_argument("--device", help="cpu, cuda:0, mps (default: auto)")
    p.add_argument("--out", help="output folder (default backend/runtime/kronos_experiment)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--dry-run", action="store_true",
                   help="download prices and print the plan and the baselines; load no model")
    return p.parse_args(argv)


# --------------------------------------------------------------------------- data

def load_daily_bars(symbol: str, need_bars: int):
    """Adjusted daily bars with lower-case columns, oldest first, no gaps filled."""
    import yfinance as yf

    years = max(3, math.ceil(need_bars / 250) + 1)
    df = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=True)
    if df is None or df.empty:
        return None
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = df.index.tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def pick_dates(n_bars: int, lookback: int, horizon: int, count: int, span: int) -> list[int]:
    """Positions (row numbers) of the forecast dates, evenly spaced over the latest `span` bars.

    Each needs `lookback` bars before it and `horizon` real bars after it."""
    last = n_bars - 1 - horizon
    first = max(lookback - 1, last - span)
    if last < first:
        return []
    if count <= 1 or last == first:
        return [last]
    step = (last - first) / (count - 1)
    return sorted({int(round(first + i * step)) for i in range(count)})


# --------------------------------------------------------------------------- model

def ensure_kronos_source(path_arg: str | None) -> Path:
    """Return a Kronos checkout, cloning it shallowly into runtime/ the first time."""
    target = Path(path_arg) if path_arg else RUNTIME_DIR / "kronos_src"
    if not (target / "model" / "__init__.py").exists():
        if path_arg:
            sys.exit(f"--kronos-dir {target} has no model/ package")
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning {KRONOS_REPO_URL} (shallow) into {target} ...")
        subprocess.run(["git", "clone", "--depth", "1", KRONOS_REPO_URL, str(target)], check=True)
    return target


def load_predictor(args: argparse.Namespace):
    try:
        import torch  # noqa: F401
    except ImportError:
        sys.exit("PyTorch is not installed. Run: pip install -r backend/requirements-ml.txt")
    source = ensure_kronos_source(args.kronos_dir)
    sys.path.insert(0, str(source))
    from model import Kronos, KronosPredictor, KronosTokenizer  # Kronos's own package

    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    return KronosPredictor(model, tokenizer, device=args.device, max_context=MAX_CONTEXT)


def forecast_paths(predictor, df, pos: int, lookback: int, horizon: int, paths: int, args) -> list[float]:
    """Sampled closes at the horizon end, one per path, from bars up to and including row `pos`."""
    import pandas as pd

    window = df.iloc[pos - lookback + 1: pos + 1]
    x_ts = pd.Series(window.index)
    # Future dates are business days after T; Kronos only uses them as calendar features.
    y_ts = pd.Series(pd.bdate_range(window.index[-1] + pd.Timedelta(days=1), periods=horizon))
    cols = ["open", "high", "low", "close", "volume"]
    batch = [window[cols].reset_index(drop=True) for _ in range(paths)]
    out = predictor.predict_batch(
        df_list=batch,
        x_timestamp_list=[x_ts] * paths,
        y_timestamp_list=[y_ts] * paths,
        pred_len=horizon,
        T=args.temperature,
        top_p=args.top_p,
        sample_count=1,  # one future per copy, so the copies are separate samples
        verbose=False,
    )
    return [float(frame["close"].iloc[-1]) for frame in out]


# --------------------------------------------------------------------------- statistics

def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def summarise(rows: list[dict]) -> list[str]:
    """Plain-text summary of the forecast rows (each has the keys written by run_forecasts)."""
    n = len(rows)
    if n == 0:
        return ["no forecasts"]
    actual = [r["actual_ret"] for r in rows]
    median = [r["pred_median_ret"] for r in rows]
    hit = sum(1 for a, m in zip(actual, median) if (a > 0) == (m > 0)) / n
    up_rate = sum(1 for a in actual if a > 0) / n
    up_always = max(up_rate, 1 - up_rate)
    mae_model = sum(abs(a - m) for a, m in zip(actual, median)) / n
    mae_zero = sum(abs(a) for a in actual) / n
    mae_prev = sum(abs(a - r["prev_ret"]) for a, r in zip(actual, rows)) / n
    brier = sum((r["p_up"] - (1.0 if r["actual_ret"] > 0 else 0.0)) ** 2 for r in rows) / n
    brier_half = sum((0.5 - (1.0 if a > 0 else 0.0)) ** 2 for a in actual) / n
    inside = sum(1 for r in rows if r["pred_p10_ret"] <= r["actual_ret"] <= r["pred_p90_ret"]) / n
    ic = spearman(median, actual)
    se = math.sqrt(hit * (1 - hit) / n)
    lines = [
        f"forecasts: {n}  (overlapping dates are not independent)",
        f"direction hit rate: {hit:.1%} +/- {1.96 * se:.1%}  | always-the-commoner-side: {up_always:.1%}  (share up: {up_rate:.1%})",
        f"rank IC (median forecast vs actual): {'n/a' if ic is None else f'{ic:+.3f}'}",
        f"mean abs error: model {mae_model:.4f} | no-change {mae_zero:.4f} | repeat-last-window {mae_prev:.4f}",
        f"Brier of P(up): model {brier:.4f} | always-0.5 {brier_half:.4f}  (lower is better)",
        f"actual inside the 10-90% band of paths: {inside:.1%}  (about 80% if well calibrated)",
    ]
    return lines


# --------------------------------------------------------------------------- run

def run(args: argparse.Namespace) -> int:
    import random

    lookback = min(args.lookback, MAX_CONTEXT)
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else DEFAULT_SYMBOLS
    out_dir = Path(args.out) if args.out else RUNTIME_DIR / "kronos_experiment"
    random.seed(args.seed)

    plan: list[tuple[str, object, list[int]]] = []
    for sym in symbols:
        df = load_daily_bars(sym, lookback + args.span_days + args.horizon)
        if df is None:
            print(f"{sym}: no price data, skipped")
            continue
        positions = pick_dates(len(df), lookback, args.horizon, args.dates, args.span_days)
        if not positions:
            print(f"{sym}: only {len(df)} bars, not enough for lookback {lookback}, skipped")
            continue
        plan.append((sym, df, positions))

    total = sum(len(p) for _, _, p in plan)
    print(f"{len(plan)} symbols, {total} forecasts of {args.horizon} days, {args.paths} paths each "
          f"({total * args.paths} sampled futures).")
    if args.dry_run:
        for sym, df, positions in plan:
            print(f"  {sym}: {len(df)} bars, dates {df.index[positions[0]].date()} .. {df.index[positions[-1]].date()}")
        print("dry run: no model loaded, nothing downloaded beyond prices.")
        return 0
    if total == 0:
        return 1

    try:
        import torch
        torch.manual_seed(args.seed)
    except ImportError:
        pass
    predictor = load_predictor(args)

    rows: list[dict] = []
    started = time.time()
    done = 0
    for sym, df, positions in plan:
        for pos in positions:
            close_t = float(df["close"].iloc[pos])
            ends = forecast_paths(predictor, df, pos, lookback, args.horizon, args.paths, args)
            rets = [e / close_t - 1 for e in ends]
            prev_base = float(df["close"].iloc[pos - args.horizon])
            rows.append({
                "symbol": sym,
                "date": df.index[pos].date().isoformat(),
                "close_t": close_t,
                "actual_ret": float(df["close"].iloc[pos + args.horizon]) / close_t - 1,
                "prev_ret": close_t / prev_base - 1,
                "pred_median_ret": _quantile(rets, 0.5),
                "pred_p10_ret": _quantile(rets, 0.1),
                "pred_p90_ret": _quantile(rets, 0.9),
                "p_up": sum(1 for r in rets if r > 0) / len(rets),
            })
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  {done}/{total} done, {time.time() - started:.0f}s elapsed")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    csv_path = out_dir / f"forecasts-{stamp}.csv"
    import csv
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report = [f"Kronos experiment {stamp}: model {args.model}, horizon {args.horizon}d, lookback {lookback}, "
              f"{args.paths} paths, T={args.temperature}, top_p={args.top_p}", ""]
    report += ["ALL SYMBOLS"] + summarise(rows)
    report += ["", "BY SYMBOL (n is small; read as colour only)"]
    for sym, _, _ in plan:
        sub = [r for r in rows if r["symbol"] == sym]
        hit = sum(1 for r in sub if (r["actual_ret"] > 0) == (r["pred_median_ret"] > 0)) / len(sub)
        report.append(f"  {sym}: n={len(sub)} direction hit {hit:.0%}")
    report += ["", "Results are for past dates only, on adjusted daily bars; they say nothing certain about the future."]
    text = "\n".join(report)
    (out_dir / f"summary-{stamp}.txt").write_text(text, encoding="utf-8")
    print("\n" + text)
    print(f"\nrows: {csv_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

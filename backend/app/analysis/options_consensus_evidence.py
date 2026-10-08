"""Two more evidence parts from data the app already fetches, signed for the trade's direction.

  * Options open interest: the put/call ratio of OPEN INTEREST (positions still held) on the chosen
    expiry, next to the existing put/call ratio of that day's VOLUME. Under BULLISH_OI_RATIO is call-heavy
    (supports a long), over BEARISH_OI_RATIO is put-heavy (supports a short). Needs MIN_TOTAL_OI contracts so a
    thin chain says nothing. Free options data is noisy, so one point at most.
  * Analyst consensus: the consensus EPS for the next report against the last reported EPS. Analysts
    expecting EPS to grow by STRONG_GROWTH_PCT or more support a long; expecting it to fall by WEAK_FALL_PCT
    or more support a short. This is the consensus level, not revisions (the free sources keep no revision
    history, so a change in the consensus cannot be measured).
"""

from __future__ import annotations

from app.data_providers.base import EarningsEstimate, EarningsHistoryEntry, OptionsChain

OPTIONS_OI_SCORE_CAP = 1
BULLISH_OI_RATIO = 0.7
BEARISH_OI_RATIO = 1.0
MIN_TOTAL_OI = 1000

CONSENSUS_SCORE_CAP = 1
STRONG_GROWTH_PCT = 10.0
WEAK_FALL_PCT = -5.0


def open_interest_ratio(chain: OptionsChain | None) -> tuple[float, float, float] | None:
    """(put OI / call OI, put OI, call OI) for the chain, or None when too thin to read."""
    if chain is None:
        return None
    calls = sum(float(c.open_interest or 0) for c in chain.calls)
    puts = sum(float(p.open_interest or 0) for p in chain.puts)
    if calls <= 0 or calls + puts < MIN_TOTAL_OI:
        return None
    return puts / calls, puts, calls


def score_options_open_interest(direction: str | None, chain: OptionsChain | None) -> tuple[int, list[str]]:
    reading = open_interest_ratio(chain)
    if reading is None or direction not in ("long", "short"):
        return 0, []
    ratio, puts, calls = reading
    if ratio < BULLISH_OI_RATIO:
        bullish = True
    elif ratio > BEARISH_OI_RATIO:
        bullish = False
    else:
        return 0, []
    supports = bullish == (direction == "long")
    verb = "supports" if supports else "argues against"
    lean = "call-heavy" if bullish else "put-heavy"
    return (1 if supports else -1) * OPTIONS_OI_SCORE_CAP, [
        f"options open interest is {lean} (put/call {ratio:.2f}, {puts:,.0f} puts vs {calls:,.0f} calls), which {verb} a {direction}"
    ]


def consensus_growth_pct(
    estimate: EarningsEstimate | None, history: list[EarningsHistoryEntry]
) -> tuple[float, float, float] | None:
    """(consensus EPS growth %, consensus EPS, last reported EPS), or None when either figure is missing or the
    last EPS was not positive (a percentage off zero or a loss is not meaningful)."""
    if estimate is None or estimate.eps_estimate is None:
        return None
    reported = [h for h in history if h.eps_actual is not None]
    if not reported:
        return None
    last = max(reported, key=lambda h: h.date).eps_actual
    if last is None or last <= 0:
        return None
    return (estimate.eps_estimate - last) / last * 100, estimate.eps_estimate, last


def score_analyst_consensus(
    direction: str | None, estimate: EarningsEstimate | None, history: list[EarningsHistoryEntry]
) -> tuple[int, list[str]]:
    reading = consensus_growth_pct(estimate, history)
    if reading is None or direction not in ("long", "short"):
        return 0, []
    growth, expected, last = reading
    if growth >= STRONG_GROWTH_PCT:
        bullish = True
    elif growth <= WEAK_FALL_PCT:
        bullish = False
    else:
        return 0, []
    supports = bullish == (direction == "long")
    verb = "supports" if supports else "argues against"
    return (1 if supports else -1) * CONSENSUS_SCORE_CAP, [
        f"analyst consensus EPS ${expected:.2f} is {growth:+.1f}% against the last reported ${last:.2f}, which {verb} a {direction}"
    ]

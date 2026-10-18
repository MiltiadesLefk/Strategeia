"""The evidence parts added to the confidence score after the original ten, all rule-based, all signed for the
trade's direction (what supports a long argues against a short, and the reverse).

Smart Money, buying AND selling (people bought supports a long; people sold or shorted supports a short):
  congress          members of Congress net buying or selling            (analysis/congress_scoring.py)
  funds             followed funds' 13F net buying or selling            (analysis/fund_signals.py)
  ownership         5% holders' new, raised, cut or ended stakes         (analysis/fund_signals.py)
  short_volume      unusually high FINRA short-sale volume               (analysis/short_volume_scoring.py)
  (insiders are one of the original parts: analysis/insider_scoring.py, buying and selling)
Company events and numbers:
  filing_8k         negative-leaning 8-K items                           (analysis/filing_8k_scoring.py)
  financial_health  profitability trend                                  (analysis/financial_health_scoring.py)
  balance_sheet     debt-to-equity, current ratio, free cash flow          (analysis/balance_sheet_scoring.py)
  valuation         P/E extremes plus the DCF and peer comparison        (analysis/valuation_scoring.py)
  analyst_consensus consensus EPS against the last reported EPS          (analysis/options_consensus_evidence.py)
Market evidence:
  relative_strength return against the market (SPY)                      (analysis/price_evidence.py)
  volume_trend      accumulation or distribution over 20 days            (analysis/price_evidence.py)
  options_oi        put/call ratio of open interest                      (analysis/options_consensus_evidence.py)
Caution flags (penalty only, never a bonus, so they are not part of the achievable maximum):
  fed_window        a Fed statement or Chair speech within a day         (analysis/fed_event_window.py)
  post_mentions     a post naming the company in the last 24 hours       (analysis/post_mentions.py)

Every part is capped at its own `cap` either way (a caution flag only down to -cap). A part with no stored
data scores 0 (never a guess); one that fails scores 0 and is logged. The dated parts read only what was
public at the current moment (`app.knowledge` readers). The two price-only parts (relative strength, volume
trend) can be rebuilt for a past date, so a backtest scores them; the parts that need today's company
figures, options chain, consensus or peer data are skipped under a simulated moment. The Smart Money and
filing signals were first recorded silently (analysis/shadow_signals.py) and were promoted on the user's
explicit request before a backtest could test them: their names are in `LIVE_SIGNALS`, so the strategy
version moved when they went live, and the calibration report shows whether they help.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlmodel import Session

from app.analysis.balance_sheet_scoring import score_balance_sheet
from app.analysis.congress_scoring import build_congress_signal
from app.analysis.fed_event_window import build_fed_window_signal
from app.analysis.filing_8k_scoring import build_8k_signal
from app.analysis.financial_health_scoring import score_financial_health
from app.analysis.fund_signals import build_fund_accumulation_signal, build_ownership_signal
from app.analysis.options_consensus_evidence import (
    BEARISH_OI_RATIO,
    BULLISH_OI_RATIO,
    open_interest_ratio,
    score_analyst_consensus,
    score_options_open_interest,
)
from app.analysis.post_mentions import build_post_mentions_signal
from app.analysis.price_evidence import (
    ACCUMULATION_RATIO,
    RELATIVE_STRENGTH_DAYS,
    RELATIVE_STRENGTH_GAP_PCT,
    VOLUME_TREND_DAYS,
    relative_strength,
    score_relative_strength,
    score_volume_trend,
    up_down_volume_ratio,
)
from app.analysis.short_volume_scoring import build_short_volume_signal
from app.analysis.valuation_scoring import score_valuation
from app.data_providers.base import CompanyOverview, EarningsEstimate, EarningsHistoryEntry, FinancialYear, OptionsChain
from app.knowledge.point_in_time import is_simulated
from app.signals.finra import reading_is_fresh, short_volume_ratio_as_of

logger = logging.getLogger(__name__)

# key, label, cap, penalty_only. Order is the display order.
EXTRA_PARTS: tuple[tuple[str, str, int, bool], ...] = (
    ("congress", "Congress", 2, False),
    ("funds", "Funds (13F)", 2, False),
    ("ownership", "5% owners", 2, False),
    ("short_volume", "Short volume", 1, False),
    ("filing_8k", "8-K filings", 1, False),
    ("financial_health", "Financials", 1, False),
    ("balance_sheet", "Balance sheet", 1, False),
    ("valuation", "Valuation", 2, False),
    ("analyst_consensus", "Consensus", 1, False),
    ("relative_strength", "Vs market", 1, False),
    ("volume_trend", "Volume trend", 1, False),
    ("options_oi", "Options OI", 1, False),
    ("fed_window", "Fed window", 1, True),
    ("post_mentions", "Posts", 1, True),
)
EXTRA_LABELS = {key: label for key, label, _cap, _penalty in EXTRA_PARTS}
EXTRA_CAPS = {key: cap for key, _label, cap, _penalty in EXTRA_PARTS}
PENALTY_ONLY = frozenset(key for key, _label, _cap, penalty in EXTRA_PARTS if penalty)
# Caution flags never add to what a plan can earn, so they stay out of the achievable maximum.
EXTRA_MAX_POINTS = sum(cap for _key, _label, cap, penalty in EXTRA_PARTS if not penalty)
# Parts a backtest can rebuild from prices alone.
PRICE_ONLY_PARTS = ("relative_strength", "volume_trend")


@dataclass
class ExtraEvidence:
    points: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    # Why each part scored what it did, including the zeros ("no data" and "checked, nothing there" differ).
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.points.values())

    def to_json(self) -> str:
        # Notes ride along under one key; readers of the points skip it (it is not an int).
        data: dict = dict(self.points)
        if self.notes:
            data["_why"] = self.notes
        return json.dumps(data, separators=(",", ":"))


def _zero_note(build) -> str:
    """The "why 0" text for a part that found nothing; a failure to build it must not lose the part's score."""
    try:
        return build()
    except Exception:  # noqa: BLE001
        logger.exception("could not build a zero-score note")
        return "Scored 0: the rule found nothing to score."


def extra_scores_from_json(raw: str | None) -> dict[str, int] | None:
    """The stored per-part points, or None for a plan made before these parts existed."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}


def extra_notes_from_json(raw: str | None) -> dict[str, str] | None:
    """The stored per-part explanations, or None for a plan made before they were kept."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    notes = data.get("_why") if isinstance(data, dict) else None
    if not isinstance(notes, dict):
        return None
    return {str(k): str(v) for k, v in notes.items()}


def evaluate_extra_evidence(
    direction: str | None,
    session: Session | None,
    symbol: str,
    overview: CompanyOverview | None,
    financial_years: list[FinancialYear],
    *,
    ohlcv: pd.DataFrame | None = None,
    market_ohlcv: pd.DataFrame | None = None,
    options_chain: OptionsChain | None = None,
    estimate: EarningsEstimate | None = None,
    earnings_history: list[EarningsHistoryEntry] | None = None,
    valuation=None,
) -> ExtraEvidence:
    """Never raises: a part that fails scores 0 and is logged."""
    out = ExtraEvidence(points={key: 0 for key in EXTRA_CAPS})

    def record(key: str, points: int, reasons: list[str]) -> None:
        cap = EXTRA_CAPS[key]
        capped = max(-cap, min(0 if key in PENALTY_ONLY else cap, int(points)))
        out.points[key] = capped
        if capped:
            out.reasons.extend(reasons)

    def shadow_part(key: str, build) -> None:
        try:
            signal = build()
            out.notes[key] = signal.reason if signal.available else f"No data: {signal.reason}"
            if signal.available and signal.would_score:
                record(key, signal.would_score, [f"{EXTRA_LABELS[key].lower()}: {signal.reason}"])
        except Exception:  # noqa: BLE001 - one part must never break a plan
            out.notes[key] = "Could not be computed (see the backend log)."
            logger.exception("evidence part %s failed for %s", key, symbol)

    def rule_part(key: str, scorer, when_zero=None) -> None:
        try:
            points, reasons = scorer()
            if reasons:
                out.notes[key] = "; ".join(reasons)
            elif when_zero is not None:
                out.notes[key] = _zero_note(when_zero)
            record(key, points, reasons)
        except Exception:  # noqa: BLE001
            out.notes[key] = "Could not be computed (see the backend log)."
            logger.exception("evidence part %s failed for %s", key, symbol)

    def no_direction(text: str) -> str:
        return text if direction in ("long", "short") else "No trade direction yet, so this part was not scored."

    def relative_strength_zero() -> str:
        reading = relative_strength(ohlcv, market_ohlcv)
        if reading is None:
            return "Not enough price history for the stock or SPY to compare."
        stock, market, gap = reading
        return no_direction(
            f"{RELATIVE_STRENGTH_DAYS}-day return {stock:+.1f}% against the market's {market:+.1f}%: a gap of {abs(gap):.1f} points, "
            f"under the {RELATIVE_STRENGTH_GAP_PCT:g} needed to count."
        )

    def volume_trend_zero() -> str:
        ratio = up_down_volume_ratio(ohlcv)
        if ratio is None:
            return "No volume history to compare up days with down days."
        return no_direction(
            f"Up-day volume is {ratio:.2f}x down-day volume over {VOLUME_TREND_DAYS} days: between {1 / ACCUMULATION_RATIO:.2f}x and "
            f"{ACCUMULATION_RATIO:g}x, so neither accumulation nor distribution."
        )

    def financial_health_zero() -> str:
        if len(financial_years) < 2:
            return f"Needs two reported years of financials; {len(financial_years)} available."
        latest = financial_years[-1]
        if not latest.revenue:
            return f"No revenue reported for {latest.year}, so no margin to judge."
        margin = latest.net_income / latest.revenue * 100
        return no_direction(
            f"{latest.year} net income ${latest.net_income / 1e6:,.0f}M on revenue ${latest.revenue / 1e6:,.0f}M (margin {margin:.1f}%): "
            "neither clearly healthy and improving nor loss-making or sliding."
        )

    def balance_sheet_zero() -> str:
        if not financial_years:
            return "No financials reported."
        latest = financial_years[-1]
        if None in (latest.debt_to_equity, latest.current_ratio, latest.free_cash_flow):
            return f"Debt-to-equity, current ratio or free cash flow not reported for {latest.year} (only Yahoo supplies them)."
        return no_direction(
            f"{latest.year} debt-to-equity {latest.debt_to_equity:.2f}, current ratio {latest.current_ratio:.2f}, "
            f"free cash flow ${latest.free_cash_flow / 1e6:,.0f}M: neither clearly strong nor weak."
        )

    def options_oi_zero() -> str:
        reading = open_interest_ratio(options_chain)
        if reading is None:
            return "No usable options open-interest data for this symbol."
        ratio, puts, calls = reading
        return no_direction(
            f"Put/call open-interest ratio {ratio:.2f} ({puts:,.0f} puts to {calls:,.0f} calls): between "
            f"{BULLISH_OI_RATIO:g} and {BEARISH_OI_RATIO:g}, so neither call-heavy nor put-heavy."
        )

    def valuation_zero() -> str:
        if overview is None:
            return "No company overview, so no P/E to judge."
        return no_direction("P/E, DCF and peer comparison all sit between the cheap and expensive lines, or their inputs were missing.")

    def consensus_zero() -> str:
        if estimate is None:
            return "No consensus EPS estimate for the next report."
        return no_direction("Expected EPS growth is between the lines that count (up 10% or more, down 5% or more).")

    if session is not None:
        if direction in ("long", "short"):
            shadow_part("congress", lambda: build_congress_signal(direction, session, symbol))
            shadow_part("funds", lambda: build_fund_accumulation_signal(direction, session, symbol))
            shadow_part("ownership", lambda: build_ownership_signal(direction, session, symbol))

            def short_volume():
                reading = short_volume_ratio_as_of(session, symbol)
                return build_short_volume_signal(direction, reading, fresh=reading is None or reading_is_fresh(reading))

            shadow_part("short_volume", short_volume)
            shadow_part("filing_8k", lambda: build_8k_signal(direction, session, symbol))
        # Caution flags: the direction of the trade does not matter, only that a tradeable plan exists.
        if direction is not None:
            shadow_part("fed_window", lambda: build_fed_window_signal(session))
            shadow_part("post_mentions", lambda: build_post_mentions_signal(session, symbol))

    # Price-only evidence: rebuilt for a past date in a backtest.
    rule_part("relative_strength", lambda: score_relative_strength(direction, ohlcv, market_ohlcv), relative_strength_zero)
    rule_part("volume_trend", lambda: score_volume_trend(direction, ohlcv), volume_trend_zero)

    if not is_simulated():  # today's company figures, chain, consensus and peers: not rebuilt for a past date
        rule_part("financial_health", lambda: score_financial_health(direction, financial_years), financial_health_zero)
        rule_part("balance_sheet", lambda: score_balance_sheet(direction, financial_years), balance_sheet_zero)
        rule_part("valuation", lambda: score_valuation(direction, overview, financial_years, valuation), valuation_zero)
        rule_part("analyst_consensus", lambda: score_analyst_consensus(direction, estimate, earnings_history or []), consensus_zero)
        rule_part("options_oi", lambda: score_options_open_interest(direction, options_chain), options_oi_zero)
    return out

"""What defines "the strategy", and how to fingerprint it.

A snapshot holds everything that changes what plan / no-trade / position comes
out of the same market data, in three groups:

- `settings`: the user-editable values that feed a decision (the confidence
  bar, risk per trade, execution costs, the portfolio caps, auto-execute).
- `overlay`: present ONLY while the AI Trading Overlay is on. The overlay's
  verdict can stop a trade, so its switches, the model that gives the verdict
  (the decision model if one is set, else the routine model: see
  `AppSettings.effective_decision_model`) and the constants that turn a verdict into a score all belong to the
  strategy while it is on, and none of them do while it is off (with it off
  they cannot change anything), so toggling them then must not mint a version.
- `rules`: the named constants in the code that define the rules (score caps,
  thresholds, the stop and target rules, the exit-scan and entry-drift
  constants). Collected at call time, so a code change to a threshold gives
  the next plan a new version with no one having to remember to bump anything.

Deliberately OUT, so changing them never creates a version:
- Secrets and keys (API keys, the Telegram token and chat id).
- Which model writes the narrative text (the routine model): it changes the
  prose, not a decision. (The overlay's decision model is IN, while the overlay
  is on. With no decision model set it is the routine model, so then the
  routine model counts too; once a decision model is set, the routine one
  stops mattering.)
- `research_mode` (whether research calls may search the web): research only
  produces background a person reads, never a plan, a no-trade or a position,
  and the overlay and the narration ignore it, so it cannot change a decision.
- `scan_universe_size`, `auto_scan_enabled` and `finnhub_enabled`: they change
  WHICH symbols are evaluated, when, or where the data comes from, not what the
  rules decide for a given symbol and given data. Measuring "the rules" across a
  change of universe should not split the sample.
- `paper_starting_cash` and `mark_to_market_interval_minutes`: the size of the
  account and how often the exit scan wakes are not rules (the scan reads every
  bar since entry, so cadence cannot change an outcome).
- Calendar tables and the macro-event date lists (data, maintained by hand), and
  the market-hours constants (they decide *when* a fill may happen, and an
  off-hours plan is simply redone at the open).

Literal numbers buried inside a function body (not a named constant) are not
visible here. `RULES_REVISION` is the manual escape hatch for that: bump it in
the same change as any edit to rule logic that moves no named constant.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any

from app.config import AppSettings

# Bump when rule LOGIC changes in a way no named constant reflects (a new
# condition in a scorer, a changed formula). Part of every fingerprint.
RULES_REVISION = 1

# Settings that always shape a decision, in snapshot order, with the label the
# change list uses. Everything else in AppSettings is excluded (see above).
DECISION_SETTINGS: dict[str, str] = {
    "min_confidence_for_trade": "min confidence",
    "default_risk_pct": "risk per trade %",
    "slippage_bps": "slippage bps",
    "commission_per_trade": "commission per trade",
    "max_concurrent_positions": "max open positions",
    "max_positions_per_sector": "max positions per sector",
    "max_position_pct_of_adv": "max position % of daily volume",
    "max_holding_days": "max holding days",
    "auto_execute_trade_plans": "auto-execute",
    "ai_trading_overlay_enabled": "AI overlay",
}

# Only meaningful while the overlay is on (see module docstring).
OVERLAY_SETTINGS: dict[str, str] = {
    "ai_overlay_scores_confidence": "overlay scores confidence",
    "ai_overlay_objection_action": "overlay objection action",
}

# The code-side rules: module -> named constants to record. Resolved with
# importlib at call time (not imported here) so that (a) a monkeypatched or
# edited constant is picked up and (b) this module never imports the services
# that import it. A name that no longer exists raises, loudly, instead of
# silently dropping out of the fingerprint.
RULE_CONSTANTS: dict[str, tuple[str, ...]] = {
    "app.analysis.trend": (
        "TREND_SLOPE_LOOKBACK", "MOMENTUM_ROC_LOOKBACK", "MOMENTUM_STRONG_THRESHOLD",
        "RSI_STRONG_BULL", "RSI_STRONG_BEAR",
    ),
    "app.analysis.scanner_scoring": (
        "VOLUME_RATIO_THRESHOLD", "NEAR_LEVEL_PCT", "RSI_OVEREXTENDED_LONG", "RSI_OVEREXTENDED_SHORT",
        "POTENTIAL_SETUP_SCORE", "WATCHING_SCORE",
    ),
    "app.analysis.fundamental_scoring": (
        "FUNDAMENTAL_SCORE_CAP", "NEWS_SCORE_CAP", "NEAR_52W_HIGH_PCT", "NEAR_52W_LOW_PCT",
        "EARNINGS_IMMINENT_DAYS", "POSITIVE_NEWS_KEYWORDS", "NEGATIVE_NEWS_KEYWORDS",
    ),
    "app.analysis.market_confirmation": (
        "MARKET_CONFIRMATION_SCORE_CAP", "MARKET_PROXY_SYMBOL", "VIX_REGIME_SCORE_CAP",
        "VIX_ELEVATED_THRESHOLD", "VIX_PROXY_SYMBOL",
    ),
    "app.analysis.options_scoring": ("OPTIONS_SCORE_CAP", "PUT_CALL_BULLISH_THRESHOLD", "PUT_CALL_BEARISH_THRESHOLD"),
    "app.analysis.insider_scoring": ("INSIDER_SCORE_CAP", "MIN_NET_BUY_VALUE"),
    "app.analysis.earnings_history_scoring": (
        "SURPRISE_TRACK_RECORD_CAP", "MIN_QUARTERS_FOR_TRACK_RECORD", "MEANINGFUL_SURPRISE_PCT",
        "CONSISTENCY_THRESHOLD", "REACTION_LOOKAHEAD_DAYS",
    ),
    "app.analysis.expected_move": ("EXPECTED_MOVE_SCORE_CAP", "ELEVATED_RATIO_THRESHOLD", "MIN_DAYS_FOR_RELIABLE_READ"),
    "app.analysis.macro_calendar": ("MACRO_EVENT_SCORE_CAP", "MACRO_EVENT_LOOKAHEAD_DAYS"),
    "app.services.trade_plan_service": (
        "STOP_BUFFER_PCT", "FALLBACK_STOP_PCT", "ATR_PERIOD", "ATR_STOP_MULTIPLE", "TECHNICAL_SCORE_CAP",
        "MAX_SCORE_FOR_CONFIDENCE", "CONFIDENCE_FLOOR", "CONFIDENCE_CEILING",
    ),
    "app.risk.position_sizing": ("FALLBACK_R_MULTIPLES", "MIN_RR_FOR_LEVEL_TARGET"),
    # Which silent signals have been promoted into real scoring (none yet).
    "app.analysis.shadow_signals": ("LIVE_SIGNALS",),
    "app.portfolio.engine": ("EXIT_SCAN_PERIOD", "MAX_ENTRY_DRIFT_PCT"),
}

# Recorded under `overlay`, only while the overlay is on.
OVERLAY_RULE_CONSTANTS: dict[str, tuple[str, ...]] = {
    "app.analysis.ai_overlay_scoring": (
        "AI_OVERLAY_SCORE_CAP", "AI_OVERLAY_STRONG_CONFIDENCE", "AI_OVERLAY_MODERATE_CONFIDENCE",
    ),
}

# Decimal places kept in the fingerprint. Six is far finer than any threshold
# anyone sets and coarse enough that float noise (0.1 + 0.2) cannot split a
# version.
FLOAT_PLACES = 6


def normalize(value: Any) -> Any:
    """A JSON-stable form of a setting or constant: floats rounded, an integral
    float written as the int (so 25.0 and 25 are the same rule), tuples, lists
    and sets as sorted-where-unordered lists. Anything it cannot represent
    raises, because a value silently dropped from the fingerprint is a rule
    change that could never be seen."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        rounded = round(value, FLOAT_PLACES)
        return int(rounded) if rounded == int(rounded) else rounded
    if isinstance(value, (set, frozenset)):
        return sorted(normalize(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    raise TypeError(f"cannot fingerprint a {type(value).__name__}: {value!r}")


def _rule_key(module_path: str, name: str) -> str:
    return f"{module_path.rsplit('.', 1)[-1]}.{name}"


def _collect_constants(table: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for module_path, names in table.items():
        module = importlib.import_module(module_path)
        for name in names:
            out[_rule_key(module_path, name)] = normalize(getattr(module, name))
    return out


def build_snapshot(settings: AppSettings) -> dict[str, Any]:
    """The decision-relevant snapshot of `settings` plus the current code
    constants. Pure: no database, no clock."""
    values = settings.model_dump()
    snapshot: dict[str, Any] = {
        "settings": {name: normalize(values[name]) for name in DECISION_SETTINGS},
        "rules": {"rules_revision": RULES_REVISION, **_collect_constants(RULE_CONSTANTS)},
    }
    if settings.ai_trading_overlay_enabled:
        # The model that answers the verdict, not the one that writes narratives.
        # No provider records none; an unpinned Claude CLI records "" (so an
        # install that never sets a decision model keeps its fingerprint).
        decision_model = settings.effective_decision_model()
        snapshot["overlay"] = {
            **{name: normalize(values[name]) for name in OVERLAY_SETTINGS},
            "llm_provider": settings.llm_provider,
            "llm_model": None if settings.llm_provider == "none" else normalize(decision_model),
            **_collect_constants(OVERLAY_RULE_CONSTANTS),
        }
    if settings.ml_style_enabled:
        # Only while on, so an install that never uses it keeps its fingerprint.
        snapshot["ml"] = {"ml_min_expected_r": normalize(values["ml_min_expected_r"])}
    if settings.liquidity_slippage_enabled:
        # Only while on, so an install that never uses it keeps its fingerprint.
        snapshot["liquidity_slippage"] = {
            "liquidity_slippage_coefficient": normalize(values["liquidity_slippage_coefficient"])
        }
    if settings.scale_out_enabled:
        # Only while on (it changes how a position exits), so an install that never
        # uses it keeps its fingerprint. The trail multiple only matters in trail mode.
        scale_out: dict[str, Any] = {
            "scale_out_fraction": normalize(values["scale_out_fraction"]),
            "scale_out_stop_mode": values["scale_out_stop_mode"],
        }
        if settings.scale_out_stop_mode == "trail":
            scale_out["scale_out_trail_r"] = normalize(values["scale_out_trail_r"])
        snapshot["scale_out"] = scale_out
    return snapshot


def snapshot_json(snapshot: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, fixed separators. Deterministic across
    processes and Python versions (no hash randomisation, no set ordering)."""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(snapshot_json(snapshot).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- change text


def _label(group: str, key: str) -> str:
    if group == "settings":
        return DECISION_SETTINGS.get(key, key)
    if group == "overlay":
        if key in OVERLAY_SETTINGS:
            return OVERLAY_SETTINGS[key]
        if key == "llm_provider":
            return "overlay LLM provider"
        if key == "llm_model":
            return "overlay LLM model"
        return key.split(".", 1)[-1].replace("_", " ").lower()
    # rules: "trade_plan_service.ATR_STOP_MULTIPLE" -> "ATR STOP MULTIPLE" would
    # shout; lower-case it and keep the module out of the way.
    if key == "rules_revision":
        return "rules revision"
    return key.split(".", 1)[-1].replace("_", " ").lower()


def _show(value: Any) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    if value is None:
        return "none"
    if isinstance(value, list):
        shown = ", ".join(str(v) for v in value[:4])
        return f"[{shown}, ... {len(value)} items]" if len(value) > 4 else f"[{shown}]"
    return str(value)


def describe_changes(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """Human-readable differences from `old` to `new`, one line each
    ("min confidence 30 -> 38"). `old` None means there is nothing to compare
    with (the first version) and gives an empty list. Keys present on one side
    only (the overlay group appearing or disappearing) read as set / removed."""
    if old is None:
        return []
    lines: list[str] = []
    for group in ("settings", "overlay", "ml", "liquidity_slippage", "scale_out", "rules"):
        before = old.get(group) or {}
        after = new.get(group) or {}
        if group == "overlay" and before and not after:
            continue  # overlay switched off: the "AI overlay on -> off" line already says it
        for key in sorted(set(before) | set(after)):
            label = _label(group, key)
            if group == "overlay" and "." in key and key not in before:
                continue  # the overlay's rule constants appearing along with it: noise, "AI overlay off -> on" covers it
            if key not in before:
                lines.append(f"{label} set to {_show(after[key])}")
            elif key not in after:
                lines.append(f"{label} removed (was {_show(before[key])})")
            elif before[key] != after[key]:
                prefix = "rule: " if group == "rules" else ""
                lines.append(f"{prefix}{label} {_show(before[key])} -> {_show(after[key])}")
    return lines

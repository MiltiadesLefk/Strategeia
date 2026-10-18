"""What defines "the strategy", and how to fingerprint it.

A snapshot holds everything that changes what plan / no-trade / position comes
out of the same market data, in groups (the AI overlay and committee gate groups of swing trading are gone):

- `settings`: the user-editable values that feed a decision (the confidence
  bar, risk per trade, execution costs, the portfolio caps, auto-execute).
- `rules`: the named constants in the code that define the rules (score caps,
  thresholds, the stop and target rules, the exit-scan and entry-drift
  constants). Collected at call time, so a code change to a threshold gives
  the next plan a new version with no one having to remember to bump anything.

Deliberately OUT, so changing them never creates a version:
- Secrets and keys (API keys, the Telegram token and chat id).
- Which model writes the narrative text (the routine model): it changes the
  prose, not a decision.
- `research_mode` (whether research calls may search the web): research only
  produces background a person reads, never a plan, a no-trade or a position,
  and the narration ignores it, so it cannot change a decision.
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
RULES_REVISION = 5

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
    "hold_stop_pct": "hold disaster stop %",
    "hold_stop_mode": "hold stop trigger (close/touch)",
    "hold_alert_drawdown_pct": "hold drawdown alert %",
    "hold_target_weight_pct": "hold target weight %",
    "hold_buy_only_dips": "hold buys only dips",
    "hold_max_positions": "hold max positions",
    "hold_max_per_sector": "hold max per sector",
    "hold_max_new_per_scan": "hold max new per scan",
    "hold_failed_gate_size_pct": "hold failed-gate size %",
    "hold_min_revenue_growth_pct": "gate min revenue growth %",
    "require_growth_acceleration": "gate requires growth acceleration",
    "evidence_bonus_enabled": "evidence bonus",
    "big_news_buy_enabled": "big-news buys",
    "big_news_size_pct": "big-news size %",
    "big_news_lookback_hours": "big-news lookback hours",

    "news_discovery_enabled": "news discovery",
    "news_discovery_max_per_run": "news discovery max per run",

    "sell_ma200_days": "sell plan days under 200-day",
    "sell_trail_pct": "sell plan trail %",
    "rebuy_cooldown_days": "rebuy cooldown days",
    "hold_add_on_dips": "hold adds on dips",

    "auto_execute_trade_plans": "auto-execute",
    "smart_money_followed_member_multiplier": "smart-money followed-member multiplier",
    "smart_money_scope": "smart-money scope",
    "smart_money_min_score": "smart-money min score",
    "smart_money_max_open_positions": "smart-money max open positions",
    "smart_money_max_new_per_week": "smart-money max new per week",
}

# The code-side rules: module -> named constants to record. Resolved with
# importlib at call time (not imported here) so that (a) a monkeypatched or
# edited constant is picked up and (b) this module never imports the services
# that import it. A name that no longer exists raises, loudly, instead of
# silently dropping out of the fingerprint.
RULE_CONSTANTS: dict[str, tuple[str, ...]] = {
    # The hold gate's own rules (hold_stop_pct and hold_target_weight_pct are settings above).
    "app.analysis.quality_gate": ("DIP_PCT", "DIP_LOOKBACK", "MA_DAYS"),
    "app.portfolio.engine": ("EXIT_SCAN_PERIOD", "MAX_ENTRY_DRIFT_PCT"),
    "app.analysis.evidence_bonus": (
        "BONUS_CAP", "BONUS_FLOOR", "INSIDER_CLUSTER_POINTS", "INSIDER_BUY_POINTS", "CONGRESS_BUY_POINTS", "BIG_NEWS_POINTS", "NEGATIVE_NEWS_POINTS",
    ),
    "app.analysis.big_news": ("BIG_NEWS_PATTERNS", "BIG_8K_ITEMS", "INCLUDE_ANALYST_UPGRADES", "ANALYST_UPGRADE_PATTERNS", "NEGATIVE_PATTERNS"),
    "app.smartmoney.scoring": (
        "WINDOW_DAYS", "RECENT_DAYS", "OLD_RECENCY_FACTOR", "INSIDER_SCORE_CAP", "INSIDER_PER_BUYER",
        "INSIDER_CEO_CFO_EXTRA", "INSIDER_SIZE_TIERS", "HOUSE_BRACKET_POINTS", "HOUSE_EXTRA_MEMBER_POINTS",
        "HOUSE_SELLER_POINTS", "INSIDER_CLUSTER_MIN_BUYERS",
        "INSIDER_CLUSTER_BONUS", "HOUSE_SCORE_CAP", "OWNERSHIP_13D_POINTS", "OWNERSHIP_13G_POINTS",
        "FUND_NEW_POINTS", "FUND_ADD_POINTS", "FUND_SCORE_CAP", "NEGATIVE_8K_POINTS", "SMART_MONEY_MIN_SCORE",
        "REFLAG_DAYS", "REFLAG_SCORE_RISE", "LIQUIDITY_MIN_PRICE", "LIQUIDITY_MIN_DOLLAR_VOLUME",
        "REVIEW_EXPIRY_DAYS",
    ),
    # The pipeline's own rules: which bars the liquidity floor reads, the new-per-week window, the exit-scan window.
    "app.smartmoney.pipeline_rules": (
        "NEW_POSITION_WINDOW_DAYS", "SMART_MONEY_EXIT_SCAN_PERIOD", "LIQUIDITY_LOOKBACK_BARS", "LIQUIDITY_MIN_BARS",
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
    if settings.ml_style_enabled:
        # Only while on, so an install that never uses it keeps its fingerprint.
        snapshot["ml"] = {"ml_min_expected_r": normalize(values["ml_min_expected_r"])}
    if settings.liquidity_slippage_enabled:
        # Only while on, so an install that never uses it keeps its fingerprint.
        snapshot["liquidity_slippage"] = {
            "liquidity_slippage_coefficient": normalize(values["liquidity_slippage_coefficient"])
        }
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
    # rules: "confidence.ATR_STOP_MULTIPLE" -> "ATR STOP MULTIPLE" would
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
    only (a group appearing or disappearing) read as set / removed."""
    if old is None:
        return []
    lines: list[str] = []
    for group in ("settings", "ml", "liquidity_slippage", "rules"):
        before = old.get(group) or {}
        after = new.get(group) or {}
        for key in sorted(set(before) | set(after)):
            label = _label(group, key)
            if key not in before:
                lines.append(f"{label} set to {_show(after[key])}")
            elif key not in after:
                lines.append(f"{label} removed (was {_show(before[key])})")
            elif before[key] != after[key]:
                prefix = "rule: " if group == "rules" else ""
                lines.append(f"{prefix}{label} {_show(before[key])} -> {_show(after[key])}")
    return lines

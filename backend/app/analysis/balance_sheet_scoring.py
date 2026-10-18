"""Balance-sheet strength as a scored confluence check: debt-to-equity, current ratio and free cash flow of the latest year.

Healthy (all three inside the quality gate's bars) supports a long (+1); weak (negative free cash flow, or debt-to-equity
at twice the gate's limit or more) argues against it (-1); anything else, or any missing figure, scores 0 (never a guess).
Only yfinance reports these, so many stocks score 0 with "not reported". Rules only, no AI.
"""

from __future__ import annotations

from app.analysis.quality_gate import MAX_DEBT_TO_EQUITY, MIN_CURRENT_RATIO
from app.data_providers.base import FinancialYear

BALANCE_SHEET_SCORE_CAP = 1
WEAK_DEBT_TO_EQUITY = 2 * MAX_DEBT_TO_EQUITY


def score_balance_sheet(direction: str | None, financial_years: list[FinancialYear]) -> tuple[int, list[str]]:
    if direction not in ("long", "short") or not financial_years:
        return 0, []
    latest = financial_years[-1]
    de, cr, fcf = latest.debt_to_equity, latest.current_ratio, latest.free_cash_flow
    if de is None or cr is None or fcf is None:
        return 0, []
    facts = f"debt-to-equity {de:.2f}, current ratio {cr:.2f}, free cash flow ${fcf / 1e6:,.0f}M in {latest.year}"
    if de <= MAX_DEBT_TO_EQUITY and cr >= MIN_CURRENT_RATIO and fcf > 0:
        healthy = True
    elif fcf < 0 or de >= WEAK_DEBT_TO_EQUITY:
        healthy = False
    else:
        return 0, []
    supports = healthy == (direction == "long")
    points = BALANCE_SHEET_SCORE_CAP if supports else -BALANCE_SHEET_SCORE_CAP
    return points, [f"balance sheet: {'strong' if healthy else 'weak'} ({facts}), which {'supports' if supports else 'argues against'} a {direction}"]

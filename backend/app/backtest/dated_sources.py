"""The dated, non-price data a backtest can rebuild, as a `dated_sources` object.

`BacktestDataProvider` (data_provider.py) hands every request for fundamentals,
earnings history and insider activity to the matching method here, with the symbol
and the simulated moment. Each method reads dated facts through the as-of readers
(`facts_known_as_of` under the hood), so it can only return what was public at that
moment. A part that is switched off has NO method on the object, which is how the
provider knows to treat it as unavailable (score 0), exactly as in a price-only run.

The facts live in the app's real database, not in the run's throwaway one, so every
call opens a short read-only session through `session_factory`. Nothing is written.

What is rebuilt
---------------
* fundamentals: the revenue history (`financials`), dated by SEC filing date. The
  52-week high and low need only prices, so the provider builds them itself.
* insiders: open-market buying from Form 4 filings, dated by SEC acceptance time.
* earnings: the track record of past EPS surprises, dated by report day.

What is deliberately NOT here: news, options, the date of the next earnings report,
the macro calendar and the AI overlay (see backtest/coverage.py for the reasons).

Each method also counts what it answered, so a finished run can say how much of the
dated data actually existed (`availability`). A part that is switched on but whose
facts were never downloaded would otherwise look like "no signal found".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from sqlmodel import Session

from app.backtest.earnings_history import earnings_history_as_of
from app.backtest.fundamentals_history import revenue_history_as_of
from app.data_providers.base import AllProvidersFailedError, EarningsHistoryEntry, FinancialsData, InsiderActivity
from app.knowledge.insider_trades import insider_activity_as_of

PART_FUNDAMENTALS = "fundamentals"
PART_INSIDERS = "insiders"
PART_EARNINGS = "earnings"


class FactBackedSources:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        fundamentals: bool = False,
        insiders: bool = False,
        earnings: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._calls: dict[str, int] = defaultdict(int)
        self._answered: dict[str, int] = defaultdict(int)
        self._symbols: dict[str, set[str]] = defaultdict(set)
        # Only the enabled parts exist as attributes: the provider looks them up by name.
        if fundamentals:
            self.financials = self._financials
        if insiders:
            self.insider_activity = self._insider_activity
        if earnings:
            self.earnings_history = self._earnings_history

    # ---- handlers ----------------------------------------------------------

    def _count(self, part: str, symbol: str, answered: bool) -> None:
        self._calls[part] += 1
        if answered:
            self._answered[part] += 1
            self._symbols[part].add(symbol)

    def _financials(self, symbol: str, moment: datetime) -> FinancialsData:
        with self._session_factory() as session:
            years = revenue_history_as_of(session, symbol, moment)
        self._count(PART_FUNDAMENTALS, symbol, bool(years))
        if not years:
            raise AllProvidersFailedError(f"backtest: no annual revenue was public for {symbol} at {moment}")
        return FinancialsData(symbol=symbol, years=years)

    def _insider_activity(self, symbol: str, moment: datetime) -> InsiderActivity | None:
        with self._session_factory() as session:
            activity = insider_activity_as_of(session, symbol, moment)
        self._count(PART_INSIDERS, symbol, activity is not None)
        return activity

    def _earnings_history(self, symbol: str, moment: datetime) -> list[EarningsHistoryEntry]:
        with self._session_factory() as session:
            history = earnings_history_as_of(session, symbol, moment)
        self._count(PART_EARNINGS, symbol, bool(history))
        return history

    # ---- reporting ---------------------------------------------------------

    def availability(self) -> dict[str, dict[str, int]]:
        """Per enabled part: how many requests were made, how many found data, and
        for how many different symbols."""
        parts = [
            part
            for part, method in (
                (PART_FUNDAMENTALS, "financials"),
                (PART_INSIDERS, "insider_activity"),
                (PART_EARNINGS, "earnings_history"),
            )
            if hasattr(self, method)
        ]
        return {
            part: {
                "requests": self._calls[part],
                "answered": self._answered[part],
                "symbols_with_data": len(self._symbols[part]),
            }
            for part in parts
        }

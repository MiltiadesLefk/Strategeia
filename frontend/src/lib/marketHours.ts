/**
 * Presentation helpers for the US market session.
 *
 * The session itself — weekends, NYSE holidays, 1:00 pm early closes — comes
 * from the backend's calendar (`GET /api/market/session`, see
 * `backend/app/markets.py`) via `useMarketSession`. It used to be computed
 * here with a hardcoded 09:30–16:00 weekday rule that didn't know holidays;
 * keeping a second copy of the holiday table in TypeScript would just be a
 * second place for it to go wrong, and the engine that refuses off-hours
 * fills reads the backend's copy. Only the countdown is computed client-side,
 * from the absolute next_open / next_close times, so it ticks between fetches.
 */

import type { MarketSession } from '../api/types';

export function marketStateLabel(session: Pick<MarketSession, 'state' | 'holiday_name'>): string {
  switch (session.state) {
    case 'open':
      return 'Market open';
    case 'pre':
      return 'Pre-market';
    case 'after':
      return 'After hours';
    case 'closed':
      return 'Market closed';
    case 'holiday':
      return session.holiday_name ? `Closed: ${session.holiday_name}` : 'Market holiday';
  }
}

/** The instant the market next changes state: its close while open, else its next open. */
export function nextBell(session: MarketSession): Date {
  return new Date(session.is_open ? session.next_close : session.next_open);
}

/** Human "opens in 2h 15m" / "closes in 40m (early close)" for the status line. */
export function timeUntilNextTransition(session: MarketSession, now: Date = new Date()): string {
  const gap = formatGap((nextBell(session).getTime() - now.getTime()) / 60_000);
  if (session.is_open) return `closes in ${gap}${session.next_close_is_early ? ' (early close, 1:00 pm ET)' : ''}`;
  return `opens in ${gap}`;
}

/**
 * 24/7 instruments with no session at all — the universe's `<COIN>-USD`
 * crypto pairs. Mirrors backend `markets.is_always_on` (a naming convention,
 * the same one CompanyIcon uses for crypto logos), not a calendar.
 */
export function isAlwaysOpenSymbol(symbol: string): boolean {
  return symbol.toUpperCase().endsWith('-USD');
}

export function formatGap(totalMinutes: number): string {
  const mins = Math.max(0, Math.round(totalMinutes));
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return mins % 60 ? `${hours}h ${mins % 60}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days}d ${hours % 24}h` : `${days}d`;
}

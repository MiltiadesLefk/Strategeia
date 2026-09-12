/**
 * US cash-session status, computed client-side.
 *
 * A trading dashboard's most load-bearing piece of context is "is the market
 * open right now" — which nothing in this app surfaced. It showed a local
 * wall clock instead, and a permanently-green "Live Market Data" badge that
 * was unconditional markup rather than a reading of anything.
 *
 * Done with Intl rather than a tz library so it stays dependency-free and
 * DST-correct. Mirrors backend `app/markets.py`; like that module it
 * deliberately ignores market holidays — the cost of being wrong on
 * Thanksgiving is a mislabeled badge, not a bad trade.
 */

export type MarketState = 'open' | 'pre' | 'after' | 'weekend';

const OPEN_MINUTES = 9 * 60 + 30; // 09:30 ET
const CLOSE_MINUTES = 16 * 60; // 16:00 ET

/** Minutes since midnight, plus weekday, in America/New_York. */
function newYorkParts(at: Date): { minutes: number; weekday: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(at);

  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '0';
  // Intl can render midnight as "24" in hour12:false; normalise it.
  const hour = Number(get('hour')) % 24;
  const weekdayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return {
    minutes: hour * 60 + Number(get('minute')),
    weekday: Math.max(0, weekdayNames.indexOf(get('weekday'))),
  };
}

export function marketState(at: Date = new Date()): MarketState {
  const { minutes, weekday } = newYorkParts(at);
  if (weekday === 0 || weekday === 6) return 'weekend';
  if (minutes < OPEN_MINUTES) return 'pre';
  if (minutes >= CLOSE_MINUTES) return 'after';
  return 'open';
}

export function marketStateLabel(state: MarketState): string {
  switch (state) {
    case 'open':
      return 'Market open';
    case 'pre':
      return 'Pre-market';
    case 'after':
      return 'After hours';
    case 'weekend':
      return 'Market closed';
  }
}

/** Human "opens in 2h 15m" / "closes in 40m" for the status line. */
export function timeUntilNextTransition(at: Date = new Date()): string {
  const { minutes, weekday } = newYorkParts(at);
  const state = marketState(at);

  if (state === 'open') return `closes in ${formatGap(CLOSE_MINUTES - minutes)}`;
  if (state === 'pre') return `opens in ${formatGap(OPEN_MINUTES - minutes)}`;

  // After hours or weekend: count forward to the next weekday open.
  const daysAhead = state === 'weekend' ? (weekday === 6 ? 2 : 1) : weekday === 5 ? 3 : 1;
  const minutesToMidnight = 24 * 60 - minutes;
  return `opens in ${formatGap(minutesToMidnight + (daysAhead - 1) * 24 * 60 + OPEN_MINUTES)}`;
}

function formatGap(totalMinutes: number): string {
  const mins = Math.max(0, Math.round(totalMinutes));
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return mins % 60 ? `${hours}h ${mins % 60}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days}d ${hours % 24}h` : `${days}d`;
}

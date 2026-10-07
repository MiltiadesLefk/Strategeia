import { getDisplayTimeZone } from './timezone';
/**
 * Text and tone for the "as of 14:32 · 3 min ago" label (components/DataFreshness.tsx).
 * Pure and import-free so it can be unit-tested with plain Node
 * (frontend/tests/lib.test.mjs).
 *
 * Honesty rule: the timestamp is only ever either the server's own data
 * timestamp (when an endpoint supplies one) or the moment this browser last
 * received the data. It is never invented or rounded to look fresher.
 */

/**
 * The market-data cache window. The backend keeps price bars for 15 minutes
 * (OHLCV_TTL in data_providers/cache.py), so data older than this on screen
 * can no longer be explained by caching. Keep the two in step.
 */
export const FRESHNESS_STALE_MS = 15 * 60_000;

export interface FreshnessInput {
  /** Epoch ms of the data: the server's timestamp, or when this browser received it. 0/undefined = no data yet. */
  asOf: number | null | undefined;
  now: number;
  /** Where `asOf` came from, so the tooltip can say so. */
  source: 'server' | 'client';
  /** The backend's market state ('open' | 'pre' | 'after' | 'closed' | 'holiday'), or undefined while unknown. */
  marketState?: string;
  holidayName?: string | null;
  /** 24/7 instruments (-USD crypto) have no closed session to report. */
  alwaysOpen?: boolean;
  /** IANA zone for the clock text; the viewer's own zone when omitted. Only tests pass it. */
  timeZone?: string;
}

export interface Freshness {
  clock: string;
  age: string;
  ageMs: number;
  stale: boolean;
  /** "US market closed" (plus the reason), or null while open/unknown/24-7. */
  marketNote: string | null;
  source: 'server' | 'client';
}

export function formatAge(ms: number): string {
  const minutes = Math.floor(Math.max(0, ms) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function dayKey(ts: number, timeZone?: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(ts);
}

/** "14:32", or "Mon 14:32" when it is not today, so an old timestamp can't pass for a recent one. */
export function formatClock(ts: number, now: number, timeZone?: string): string {
  const time = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(ts);
  if (dayKey(ts, timeZone) === dayKey(now, timeZone)) return time;
  const day = new Intl.DateTimeFormat('en-GB', { timeZone, weekday: 'short' }).format(ts);
  return `${day} ${time}`;
}

export function marketClosedNote(state: string | undefined, holidayName?: string | null): string | null {
  switch (state) {
    case 'closed':
      return 'US market closed';
    case 'holiday':
      return holidayName ? `US market closed (${holidayName})` : 'US market closed (holiday)';
    case 'pre':
      return 'US market closed (pre-market)';
    case 'after':
      return 'US market closed (after hours)';
    default:
      return null;
  }
}

export function describeFreshness(input: FreshnessInput): Freshness | null {
  if (!input.asOf || !Number.isFinite(input.asOf)) return null;
  // A browser clock running behind the server clock can put the timestamp in
  // the future; show "just now" rather than a negative age.
  const ageMs = Math.max(0, input.now - input.asOf);
  return {
    clock: formatClock(input.asOf, input.now, input.timeZone ?? getDisplayTimeZone()),
    age: formatAge(ageMs),
    ageMs,
    stale: ageMs > FRESHNESS_STALE_MS,
    marketNote: input.alwaysOpen ? null : marketClosedNote(input.marketState, input.holidayName),
    source: input.source,
  };
}

/** Parse a server timestamp; null when absent or unparseable (never guess). */
export function parseServerTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

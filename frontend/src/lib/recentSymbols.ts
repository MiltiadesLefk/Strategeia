/**
 * The command palette's "recent" symbols, kept in this browser's localStorage.
 * The list handling is pure and import-free (unit-tested with plain Node in
 * frontend/tests/lib.test.mjs); only readRecents/writeRecents touch storage,
 * and both swallow every storage error, because localStorage can be missing,
 * full or blocked (private windows, site-data settings) and a convenience
 * list must never break the palette.
 */

export const RECENTS_KEY = 'strategeia.palette.recent';
export const MAX_RECENTS = 5;

/** Most recent first, no duplicates, capped at `max`. */
export function pushRecent(list: readonly string[], symbol: string, max: number = MAX_RECENTS): string[] {
  return [symbol, ...list.filter((s) => s !== symbol)].slice(0, max);
}

/** Tolerant parse of the stored value: anything that isn't an array of strings yields []. */
export function parseRecents(raw: string | null | undefined, max: number = MAX_RECENTS): string[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return value.filter((v): v is string => typeof v === 'string' && v.length > 0 && v.length <= 20).slice(0, max);
  } catch {
    return [];
  }
}

export function readRecents(): string[] {
  try {
    return parseRecents(window.localStorage.getItem(RECENTS_KEY));
  } catch {
    return [];
  }
}

export function writeRecents(list: readonly string[]): void {
  try {
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(list.slice(0, MAX_RECENTS)));
  } catch {
    // Storage unavailable: the palette just won't remember this one.
  }
}

/**
 * Pure part of the price flash (see lib/useFlash.ts and components/Flash.tsx).
 * Import-free so it can be unit-tested with plain Node (frontend/tests/lib.test.mjs).
 */

export type FlashDirection = 'up' | 'down';

/** How long the flash stays on screen. */
export const FLASH_MS = 900;

/**
 * Which way a value moved, or null when there is nothing to flash: either
 * side missing or not a finite number (loading, an error, "—"), or no actual
 * change. In particular the first value ever shown is not a change, so a page
 * opening does not light up.
 */
export function flashDirection(prev: number | null | undefined, next: number | null | undefined): FlashDirection | null {
  if (prev === null || prev === undefined || next === null || next === undefined) return null;
  if (!Number.isFinite(prev) || !Number.isFinite(next)) return null;
  if (next > prev) return 'up';
  if (next < prev) return 'down';
  return null;
}

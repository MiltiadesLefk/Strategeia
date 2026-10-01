// Unit tests for src/lib/intraday.ts. Run with `node --test tests/intraday.test.mjs`
// (Node strips the TypeScript types itself, so there is no build step).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  INTRADAY_FALLBACK_RANGE,
  formatEtCrosshair,
  formatEtTick,
  isIntradayRange,
} from '../src/lib/intraday.ts';

const secs = (iso) => Date.parse(iso) / 1000;

test('only the 1D and 1W ranges are intraday', () => {
  assert.equal(isIntradayRange('1d'), true);
  assert.equal(isIntradayRange('1w'), true);
  for (const r of ['1mo', '3mo', '6mo', '1y']) assert.equal(isIntradayRange(r), false);
  assert.equal(isIntradayRange(INTRADAY_FALLBACK_RANGE), false);
});

test('the market open is 09:30 on the axis in summer (EDT, UTC-4)', () => {
  assert.equal(formatEtTick(secs('2026-07-15T13:30:00Z'), 'time'), '09:30');
});

test('the market open is still 09:30 in winter (EST, UTC-5): the offset follows DST', () => {
  assert.equal(formatEtTick(secs('2026-01-15T14:30:00Z'), 'time'), '09:30');
});

test('the days around a DST change are labelled correctly', () => {
  // US clocks spring forward on 8 March 2026 (07:00 UTC): the same 09:30 open is 14:30Z before, 13:30Z after.
  assert.equal(formatEtTick(secs('2026-03-06T14:30:00Z'), 'time'), '09:30');
  assert.equal(formatEtTick(secs('2026-03-09T13:30:00Z'), 'time'), '09:30');
});

test('a late-evening UTC time is still the same Eastern day', () => {
  // 01:00 UTC on 2 Oct is 21:00 ET on 1 Oct.
  assert.equal(formatEtTick(secs('2026-10-02T01:00:00Z'), 'day'), 'Oct 1');
});

test('crosshair label carries date, time and the zone name', () => {
  assert.equal(formatEtCrosshair(secs('2026-10-01T13:35:00Z')), 'Oct 1, 09:35 ET');
});

test('midnight never renders as 24:00', () => {
  assert.equal(formatEtTick(secs('2026-07-15T04:00:00Z'), 'time'), '00:00');
});

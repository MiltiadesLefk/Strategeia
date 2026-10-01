// Unit tests for the pure helpers in src/lib. Run with `npm test` (node --test).
// Node strips the TypeScript types itself (Node 22.18+), so there is no test
// framework or build step: the helpers are import-free on purpose.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { FRESHNESS_STALE_MS, describeFreshness, formatAge, formatClock, marketClosedNote, parseServerTime } from '../src/lib/freshness.ts';
import { buildIndex, scoreCommand, scoreField, searchSymbols, subsequenceScore } from '../src/lib/fuzzy.ts';
import { flashDirection } from '../src/lib/flash.ts';
import { MAX_PALETTE_ROWS, buildPaletteItems } from '../src/lib/paletteItems.ts';
import { MAX_RECENTS, parseRecents, pushRecent } from '../src/lib/recentSymbols.ts';
import { isPaletteShortcut } from '../src/lib/paletteShortcut.ts';

const UNIVERSE = [
  { symbol: 'AAPL', name: 'Apple Inc.', sector: 'Information Technology' },
  { symbol: 'MSFT', name: 'Microsoft Corp.', sector: 'Information Technology' },
  { symbol: 'NVDA', name: 'NVIDIA Corp.', sector: 'Information Technology' },
  { symbol: 'NVR', name: 'NVR Inc.', sector: 'Consumer Discretionary' },
  { symbol: 'BAC', name: 'Bank of America Corp.', sector: 'Financials' },
  { symbol: 'BK', name: 'Bank of New York Mellon', sector: 'Financials' },
  { symbol: 'JPM', name: 'JPMorgan Chase & Co.', sector: 'Financials' },
  { symbol: 'XOM', name: 'Exxon Mobil Corp.', sector: 'Energy' },
  { symbol: 'BTC-USD', name: 'Bitcoin', sector: 'Crypto' },
];
const INDEX = buildIndex(UNIVERSE);
const top = (q, n = 5) => searchSymbols(INDEX, q, n).map((e) => e.symbol);

test('an exact ticker wins over longer tickers sharing the prefix', () => {
  assert.equal(top('nvr')[0], 'NVR');
  assert.deepEqual(top('nv'), ['NVR', 'NVDA']); // both are prefixes: the shorter ticker ranks first
});

test('ticker prefix beats company-name match', () => {
  assert.equal(top('aapl')[0], 'AAPL');
  assert.equal(top('app')[0], 'AAPL');
});

test('company-name prefix and word-prefix', () => {
  assert.equal(top('micro')[0], 'MSFT');
  assert.ok(top('america').includes('BAC'));
  assert.equal(top('mellon')[0], 'BK');
});

test('multi-word queries need every word to match', () => {
  assert.deepEqual(top('bank america'), ['BAC']);
  assert.deepEqual(top('bank zzz'), []);
});

test('sector search is the weakest tier', () => {
  const r = top('energy');
  assert.equal(r[0], 'XOM');
});

test('in-order letters find a name, but only for 3+ characters', () => {
  assert.equal(top('mcsft')[0], 'MSFT');
  assert.equal(subsequenceScore('ab', 'abc'), 0);
  assert.ok(subsequenceScore('abc', 'a-b-c') > 0);
  assert.ok(subsequenceScore('abc', 'abc') > subsequenceScore('abc', 'a-b-c'));
});

test('no match returns nothing; blank query returns nothing', () => {
  assert.deepEqual(top('qqqq'), []);
  assert.deepEqual(top('   '), []);
});

test('a leading $ and mixed case are ignored', () => {
  assert.equal(top('$AaPl')[0], 'AAPL');
});

test('results are capped at the limit', () => {
  assert.equal(searchSymbols(INDEX, 'o', 3).length, 3);
});

test('stays fast on a 500-symbol universe', () => {
  const big = Array.from({ length: 500 }, (_, i) => ({ symbol: `T${i}X`, name: `Company number ${i} holdings`, sector: 'Sector' }));
  const index = buildIndex(big);
  const start = performance.now();
  for (let i = 0; i < 100; i++) searchSymbols(index, 'compn hold', 12);
  assert.ok(performance.now() - start < 500, 'a hundred 500-symbol searches should take well under half a second');
});

test('scoreField tiers are ordered', () => {
  const exact = scoreField('abc', 'abc');
  const prefix = scoreField('ab', 'abc');
  const word = scoreField('bc', 'a bc');
  const inside = scoreField('bc', 'abcd');
  const fuzzy = scoreField('acd', 'abcd');
  assert.ok(exact > prefix && prefix > word && word > inside && inside > fuzzy && fuzzy > 0);
});

test('scoreCommand matches labels and keywords', () => {
  assert.ok(scoreCommand('port', 'Portfolio') > 0);
  assert.ok(scoreCommand('scan', 'Market Scan') > 0);
  assert.ok(scoreCommand('positions', 'Portfolio', 'positions paper') > 0);
  assert.equal(scoreCommand('zzz', 'Portfolio', 'positions'), 0);
  assert.ok(scoreCommand('port', 'Portfolio') > scoreCommand('port', 'Settings', 'portal'));
});

// ---- freshness ----------------------------------------------------------

const MIN = 60_000;
const T0 = Date.UTC(2026, 9, 1, 14, 32, 0); // 2026-10-01 14:32 UTC

test('formatAge', () => {
  assert.equal(formatAge(0), 'just now');
  assert.equal(formatAge(59_000), 'just now');
  assert.equal(formatAge(3 * MIN), '3 min ago');
  assert.equal(formatAge(59 * MIN), '59 min ago');
  assert.equal(formatAge(60 * MIN), '1h ago');
  assert.equal(formatAge(25 * 60 * MIN), '1d ago');
  assert.equal(formatAge(-5000), 'just now');
});

test('formatClock shows the weekday when it is not today', () => {
  assert.equal(formatClock(T0, T0 + 3 * MIN, 'UTC'), '14:32');
  assert.equal(formatClock(T0, T0 + 24 * 60 * MIN, 'UTC'), 'Thu 14:32');
});

test('describeFreshness: fresh vs stale around the 15 minute cache window', () => {
  const base = { asOf: T0, source: 'client', timeZone: 'UTC' };
  const fresh = describeFreshness({ ...base, now: T0 + FRESHNESS_STALE_MS });
  assert.equal(fresh.stale, false, 'exactly at the cache window is still explained by caching');
  assert.equal(fresh.clock, '14:32');
  assert.equal(fresh.age, '15 min ago');
  const stale = describeFreshness({ ...base, now: T0 + FRESHNESS_STALE_MS + 1 });
  assert.equal(stale.stale, true);
});

test('describeFreshness: no data yet means no label, and a future timestamp is clamped', () => {
  assert.equal(describeFreshness({ asOf: 0, now: T0, source: 'client' }), null);
  assert.equal(describeFreshness({ asOf: undefined, now: T0, source: 'client' }), null);
  const skewed = describeFreshness({ asOf: T0 + 10 * MIN, now: T0, source: 'server', timeZone: 'UTC' });
  assert.equal(skewed.ageMs, 0);
  assert.equal(skewed.stale, false);
});

test('describeFreshness: market-closed note, except for 24/7 instruments', () => {
  const base = { asOf: T0, now: T0 + MIN, source: 'client', timeZone: 'UTC' };
  assert.equal(describeFreshness({ ...base, marketState: 'open' }).marketNote, null);
  assert.equal(describeFreshness({ ...base }).marketNote, null, 'unknown state says nothing');
  assert.equal(describeFreshness({ ...base, marketState: 'closed' }).marketNote, 'US market closed');
  assert.equal(describeFreshness({ ...base, marketState: 'holiday', holidayName: 'Thanksgiving' }).marketNote, 'US market closed (Thanksgiving)');
  assert.equal(describeFreshness({ ...base, marketState: 'after' }).marketNote, 'US market closed (after hours)');
  assert.equal(describeFreshness({ ...base, marketState: 'closed', alwaysOpen: true }).marketNote, null);
  assert.equal(marketClosedNote('pre'), 'US market closed (pre-market)');
});

test('parseServerTime never guesses', () => {
  assert.equal(parseServerTime(null), null);
  assert.equal(parseServerTime('not a date'), null);
  assert.equal(parseServerTime('2026-10-01T14:32:00Z'), T0);
});

// ---- flash --------------------------------------------------------------

test('flashDirection only reports real numeric changes', () => {
  assert.equal(flashDirection(100, 101), 'up');
  assert.equal(flashDirection(100, 99.99), 'down');
  assert.equal(flashDirection(100, 100), null);
  assert.equal(flashDirection(undefined, 100), null, 'first value is not a change');
  assert.equal(flashDirection(null, 100), null);
  assert.equal(flashDirection(100, undefined), null);
  assert.equal(flashDirection(100, Number.NaN), null);
});

// ---- palette rows ---------------------------------------------------------

const ids = (rows) => rows.map((r) => r.id);

test('palette: a symbol query lists the symbol, then its trade-plan action', () => {
  const rows = buildPaletteItems(INDEX, 'nvda', []);
  assert.equal(rows[0].id, 'symbol:NVDA');
  assert.equal(rows[0].to, '/analysis?symbol=NVDA');
  assert.equal(rows[0].shiftTo, '/trade-plans?symbol=NVDA');
  const plan = rows.find((r) => r.id === 'plan:NVDA');
  assert.equal(plan.to, '/trade-plans?symbol=NVDA');
  assert.equal(plan.section, 'Actions');
});

test('palette: a page-name query leads with the page', () => {
  assert.equal(buildPaletteItems(INDEX, 'portf', [])[0].id, 'nav:/portfolio');
  assert.equal(buildPaletteItems(INDEX, 'settings', [])[0].to, '/settings');
});

test('palette: sections stay contiguous so headers render once each', () => {
  for (const q of ['b', 'bank', 'nv', 'scan', 'trade']) {
    const seen = [];
    for (const r of buildPaletteItems(INDEX, q, [])) if (seen[seen.length - 1] !== r.section) seen.push(r.section);
    assert.equal(new Set(seen).size, seen.length, `sections interleave for "${q}": ${seen}`);
  }
});

test('palette: empty query shows recents (known symbols only) then every page', () => {
  const rows = buildPaletteItems(INDEX, '', ['MSFT', 'GONE', 'AAPL']);
  assert.deepEqual(ids(rows).slice(0, 2), ['symbol:MSFT', 'symbol:AAPL']);
  assert.equal(rows.filter((r) => r.section === 'Go to').length, 6);
});

test('palette: never renders more than the row cap, even against a 500-symbol universe', () => {
  const big = buildIndex(Array.from({ length: 500 }, (_, i) => ({ symbol: `Q${i}`, name: `Quality ${i} Corp`, sector: 'Industrials' })));
  assert.ok(buildPaletteItems(big, 'q', []).length <= MAX_PALETTE_ROWS);
  assert.ok(buildPaletteItems(big, 'quality', []).length <= MAX_PALETTE_ROWS);
});

test('palette: no match gives no rows', () => {
  assert.deepEqual(buildPaletteItems(INDEX, 'qqqq', []), []);
});

test('recents: newest first, deduplicated, capped', () => {
  assert.deepEqual(pushRecent(['A', 'B', 'C'], 'B'), ['B', 'A', 'C']);
  const many = ['1', '2', '3', '4', '5'];
  assert.equal(pushRecent(many, '6').length, MAX_RECENTS);
  assert.equal(pushRecent(many, '6')[0], '6');
});

test('recents: a corrupt stored value is ignored, not thrown', () => {
  assert.deepEqual(parseRecents('{not json'), []);
  assert.deepEqual(parseRecents('{"a":1}'), []);
  assert.deepEqual(parseRecents(null), []);
  assert.deepEqual(parseRecents('["AAPL", 5, "", "MSFT"]'), ['AAPL', 'MSFT']);
});

test('shortcut: Ctrl+K and Cmd+K only', () => {
  const base = { key: 'k', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false };
  assert.equal(isPaletteShortcut({ ...base, ctrlKey: true }), true);
  assert.equal(isPaletteShortcut({ ...base, metaKey: true }), true);
  assert.equal(isPaletteShortcut({ ...base, ctrlKey: true, key: 'K' }), true);
  assert.equal(isPaletteShortcut(base), false);
  assert.equal(isPaletteShortcut({ ...base, ctrlKey: true, shiftKey: true }), false);
  assert.equal(isPaletteShortcut({ ...base, ctrlKey: true, key: 'j' }), false);
});

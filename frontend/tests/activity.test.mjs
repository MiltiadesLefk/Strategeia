// Unit tests for src/lib/activity.ts: tracking never changes what a write returns or throws.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { trackWrite } from '../src/lib/activity.ts';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test('a fast write returns its value', async () => {
  assert.equal(await trackWrite('/api/trade-plans/generate', async () => 42), 42);
});

test('a failing write rethrows the original error', async () => {
  const boom = new Error('nope');
  await assert.rejects(trackWrite('/api/trade-plans/generate', () => Promise.reject(boom)), boom);
});

test('a slow write resolves with its value', async () => {
  const value = await trackWrite('/api/scan/auto-trade', async () => {
    await sleep(300);
    return 'ok';
  });
  assert.equal(value, 'ok');
});

test('silent paths pass straight through', async () => {
  assert.equal(await trackWrite('/api/auth/login', async () => 'in'), 'in');
});

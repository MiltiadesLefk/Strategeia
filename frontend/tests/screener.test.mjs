// Unit tests for the CSV export and the TradingView embed builder (pure helpers in src/lib).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { csvCell, toCsv } from '../src/lib/csv.ts';
import { buildWidgetConfig, buildEmbedUrl, toTradingViewSymbol } from '../src/lib/tradingView.ts';

test('csv quotes commas, quotes and line breaks', () => {
  assert.equal(csvCell('a,b'), '"a,b"');
  assert.equal(csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(csvCell('x\ny'), '"x\ny"');
  assert.equal(csvCell('plain'), 'plain');
});

test('csv neutralises spreadsheet formulas in text but not in numbers', () => {
  assert.equal(csvCell('=SUM(A1)'), "'=SUM(A1)");
  assert.equal(csvCell('@cmd'), "'@cmd");
  assert.equal(csvCell('-5 text'), "'-5 text");
  assert.equal(csvCell(-5), '-5');
});

test('csv writes blanks for missing and non-finite values', () => {
  assert.equal(csvCell(null), '');
  assert.equal(csvCell(undefined), '');
  assert.equal(csvCell(NaN), '');
  assert.equal(toCsv(['a', 'b'], [[1, null]]), 'a,b\r\n1,\r\n');
});

test('TradingView symbol mapping', () => {
  assert.equal(toTradingViewSymbol('AAPL'), 'AAPL');
  assert.equal(toTradingViewSymbol('brk-b'), 'BRK.B');
  assert.equal(toTradingViewSymbol('BTC-USD'), 'COINBASE:BTCUSD');
  assert.equal(toTradingViewSymbol('^GSPC'), 'SP:SPX');
  assert.equal(toTradingViewSymbol(''), null);
  assert.equal(toTradingViewSymbol('A</script>'), null);
});

test('widget config: ticker tape needs no symbol, others do', () => {
  assert.ok(buildWidgetConfig('ticker-tape'));
  assert.equal(buildWidgetConfig('symbol-overview', null), null);
  const cfg = buildWidgetConfig('technical-analysis', 'MSFT');
  assert.equal(cfg?.symbol, 'MSFT');
  assert.equal(cfg?.colorTheme, 'dark');
});

test('embed address is the TradingView frame with the settings encoded in the hash', () => {
  const url = buildEmbedUrl('ticker-tape', { locale: 'en', label: '"></iframe><script>x</script>' });
  assert.ok(url.startsWith('https://www.tradingview-widget.com/embed-widget/ticker-tape/?locale=en#'));
  const hash = url.split('#')[1];
  assert.ok(!/[<>"' ]/.test(hash), 'hash must be fully encoded');
  assert.equal(JSON.parse(decodeURIComponent(hash)).label, '"></iframe><script>x</script>');
  assert.ok(buildEmbedUrl('mini-chart', { locale: 'zz!' }).includes('/mini-symbol-overview/?locale=en#'));
});

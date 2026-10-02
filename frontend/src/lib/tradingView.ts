/**
 * Builds TradingView's free embeddable widgets (https://www.tradingview.com/widget/).
 * Pure and import-free so it can be unit-tested with plain Node
 * (frontend/tests/screener.test.mjs).
 *
 * Idea from OpenTerminal and OpenStock (embedding TradingView's free widgets);
 * OpenStock is AGPL-3.0 and none of its code was read or copied.
 *
 * TradingView's own embed script does one thing: it creates an iframe pointing at
 * https://www.tradingview-widget.com/embed-widget/<name>/ with the settings as
 * URL-encoded JSON in the hash. We build that same iframe address ourselves, so
 * TradingView's script is never run inside this app's page at all, and the frame
 * lives on TradingView's own origin: it cannot read this app's cookies, storage or
 * page. (An earlier approach, running their script inside a sandboxed srcdoc frame,
 * was dropped: their script reads document.cookie and drew nothing without
 * allow-same-origin, which would have given it this app's origin.)
 *
 * Nothing in this file touches the network or the DOM. The component
 * (components/TradingViewWidget.tsx) only uses the address after a click.
 */

export type TvWidgetKind = 'symbol-overview' | 'technical-analysis' | 'mini-chart' | 'ticker-tape';

/** Where TradingView serves each widget's iframe. */
export const TV_EMBED_BASE = 'https://www.tradingview-widget.com/embed-widget/';
export const TV_HOME_URL = 'https://www.tradingview.com/';

const EMBED_NAME: Record<TvWidgetKind, string> = {
  'symbol-overview': 'symbol-overview',
  'technical-analysis': 'technical-analysis',
  'mini-chart': 'mini-symbol-overview',
  'ticker-tape': 'ticker-tape',
};

// Tickers whose Yahoo-style name differs from TradingView's.
const INDEX_SYMBOLS: Record<string, string> = {
  '^GSPC': 'SP:SPX',
  '^IXIC': 'NASDAQ:IXIC',
  '^DJI': 'DJ:DJI',
  '^VIX': 'CBOE:VIX',
};
const TV_SYMBOL = /^[A-Z0-9:._!-]{1,30}$/;

/**
 * Our (Yahoo-style) ticker as TradingView names it, or null when there is no
 * safe mapping. BTC-USD becomes COINBASE:BTCUSD, BRK-B becomes BRK.B, a plain
 * US ticker is left for TradingView to resolve.
 */
export function toTradingViewSymbol(symbol: string): string | null {
  const s = symbol.trim().toUpperCase();
  if (!s) return null;
  if (INDEX_SYMBOLS[s]) return INDEX_SYMBOLS[s];
  let mapped = s;
  if (s.endsWith('-USD')) mapped = `COINBASE:${s.slice(0, -4)}USD`;
  else if (/^[A-Z0-9]+-[A-Z]$/.test(s)) mapped = s.replace('-', '.');
  return TV_SYMBOL.test(mapped) ? mapped : null;
}

/** The tape on the Market Terminal: the three US indexes and two big names, all with TradingView's own names. */
export const DEFAULT_TAPE: { proName: string; title: string }[] = [
  { proName: 'SP:SPX', title: 'S&P 500' },
  { proName: 'NASDAQ:IXIC', title: 'Nasdaq' },
  { proName: 'DJ:DJI', title: 'Dow Jones' },
  { proName: 'CBOE:VIX', title: 'VIX' },
  { proName: 'COINBASE:BTCUSD', title: 'Bitcoin' },
];

export function buildWidgetConfig(kind: TvWidgetKind, symbol?: string | null): Record<string, unknown> | null {
  const common = { locale: 'en', colorTheme: 'dark', isTransparent: false };
  if (kind === 'ticker-tape') {
    return { ...common, symbols: DEFAULT_TAPE, showSymbolLogo: true, displayMode: 'adaptive' };
  }
  const tv = symbol ? toTradingViewSymbol(symbol) : null;
  if (!tv) return null;
  if (kind === 'symbol-overview') {
    return { ...common, symbols: [[symbol ?? tv, `${tv}|12M`]], chartOnly: false, width: '100%', height: '100%', autosize: true, showVolume: true, scalePosition: 'right', scaleMode: 'Normal', chartType: 'area', dateRanges: ['1d|1', '1m|30', '3m|60', '12m|1D', 'all|1M'] };
  }
  if (kind === 'technical-analysis') {
    return { ...common, symbol: tv, interval: '1D', width: '100%', height: '100%', showIntervalTabs: true, displayMode: 'single' };
  }
  return { ...common, symbol: tv, width: '100%', height: '100%', dateRange: '12M', autosize: true, largeChartUrl: '' };
}

/**
 * The iframe address for a widget. `locale` goes in the query, everything else in the
 * hash as encoded JSON, the way TradingView's own script builds it. Encoding means no
 * symbol or label can break out of the address.
 */
export function buildEmbedUrl(kind: TvWidgetKind, config: Record<string, unknown>): string {
  const { locale, ...settings } = config;
  const lang = typeof locale === 'string' && /^[a-z]{2}(_[A-Z]{2})?$/.test(locale) ? locale : 'en';
  const payload = { ...settings, utm_source: '', utm_medium: 'widget', utm_campaign: EMBED_NAME[kind] };
  return `${TV_EMBED_BASE}${EMBED_NAME[kind]}/?locale=${lang}#${encodeURIComponent(JSON.stringify(payload))}`;
}

/** localStorage key remembering that the third-party notice was read. It never loads anything by itself. */
export const TV_NOTICE_KEY = 'strategeia.tradingview.noticeSeen';

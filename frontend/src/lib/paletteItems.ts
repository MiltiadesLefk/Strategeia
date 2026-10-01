/**
 * Builds the rows the command palette shows for a query. Pure and import-free
 * (unit-tested with plain Node in frontend/tests/lib.test.mjs); the component
 * only renders what this returns.
 */

// The explicit .ts extension lets plain Node load this file for the unit tests.
import { scoreCommand, searchSymbolsScored, type IndexedSymbol, type SearchableSymbol } from './fuzzy.ts';

/** Rows rendered at once. A 500-symbol universe would otherwise mean 500 DOM rows per keystroke. */
export const MAX_PALETTE_ROWS = 12;
// Of those, at most this many are symbols, so a broad query ("co") can't push
// every page and action off the list.
const MAX_SYMBOL_ROWS = 8;
// "Generate trade plan for X" is offered for the best few matches only.
const MAX_PLAN_ROWS = 2;
// Pages and actions are shown for a query only when they match at least this
// well, so one stray letter doesn't surface "Settings" under every symbol.
const MIN_COMMAND_SCORE = 400;

export type PaletteSection = 'Recent' | 'Symbols' | 'Actions' | 'Go to';

export interface PaletteItem {
  id: string;
  section: PaletteSection;
  label: string;
  detail: string;
  /** Route to navigate to. */
  to: string;
  /** Symbol to push onto the recents list when this row is chosen. */
  symbol?: string;
  /** Where Shift+Enter goes (symbol rows only): the Trade Plans page with the symbol selected. */
  shiftTo?: string;
}

/** The pages, in the sidebar's order. Mirrors layout/Sidebar.tsx NAV_ITEMS. */
export const NAV_COMMANDS: { to: string; label: string; keywords: string }[] = [
  { to: '/', label: 'Dashboard', keywords: 'home overview summary' },
  { to: '/scan', label: 'Market Scan', keywords: 'scanner setups signals watchlist' },
  { to: '/analysis', label: 'Analysis', keywords: 'chart technical fundamentals news research' },
  { to: '/trade-plans', label: 'Trade Plans', keywords: 'plan generate entry stop target' },
  { to: '/portfolio', label: 'Portfolio', keywords: 'positions paper account equity pnl' },
  { to: '/settings', label: 'Settings', keywords: 'config providers ai telegram watchlist' },
];

export const analysisHref = (symbol: string) => `/analysis?symbol=${encodeURIComponent(symbol)}`;
export const tradePlanHref = (symbol: string) => `/trade-plans?symbol=${encodeURIComponent(symbol)}`;

function symbolItem(entry: SearchableSymbol, section: PaletteSection): PaletteItem {
  return {
    id: `symbol:${entry.symbol}`,
    section,
    label: entry.symbol,
    detail: [entry.name, entry.sector].filter(Boolean).join(' · '),
    to: analysisHref(entry.symbol),
    symbol: entry.symbol,
    shiftTo: tradePlanHref(entry.symbol),
  };
}

function navItems(query: string): { item: PaletteItem; score: number }[] {
  return NAV_COMMANDS.map((c) => ({
    score: query ? scoreCommand(query, c.label, c.keywords) : 1,
    item: { id: `nav:${c.to}`, section: 'Go to' as const, label: c.label, detail: 'Open page', to: c.to },
  })).filter((r) => r.score >= (query ? MIN_COMMAND_SCORE : 1));
}

export function buildPaletteItems<T extends SearchableSymbol>(
  index: readonly IndexedSymbol<T>[],
  query: string,
  recents: readonly string[],
): PaletteItem[] {
  const q = query.trim();

  if (!q) {
    const bySymbol = new Map(index.map((ix) => [ix.entry.symbol, ix.entry]));
    const recentRows = recents
      .map((s) => bySymbol.get(s))
      .filter((e): e is T => e !== undefined)
      .map((e) => symbolItem(e, 'Recent'));
    return [...recentRows, ...navItems('').map((r) => r.item)].slice(0, MAX_PALETTE_ROWS);
  }

  const symbols = searchSymbolsScored(index, q, MAX_SYMBOL_ROWS);
  const symbolRows = symbols.map((s) => symbolItem(s.entry, 'Symbols'));
  const planRows: PaletteItem[] = symbols.slice(0, MAX_PLAN_ROWS).map(({ entry }) => ({
    id: `plan:${entry.symbol}`,
    section: 'Actions',
    label: `Generate trade plan for ${entry.symbol}`,
    detail: `Opens Trade Plans with ${entry.symbol} selected`,
    to: tradePlanHref(entry.symbol),
    symbol: entry.symbol,
  }));
  const pages = navItems(q).sort((a, b) => b.score - a.score);
  const pageRows = pages.map((r) => r.item);

  // Whichever group matched best leads: typing "port" lists the Portfolio page
  // before an unrelated symbol, "nvda" lists the symbol first. Symbols win
  // ties, since they are what the palette is for. The trade-plan actions
  // follow the symbols they belong to.
  const symbolsLead = (symbols[0]?.score ?? 0) >= (pages[0]?.score ?? 0);
  const ordered = symbolsLead ? [...symbolRows, ...planRows, ...pageRows] : [...pageRows, ...symbolRows, ...planRows];
  return ordered.slice(0, MAX_PALETTE_ROWS);
}

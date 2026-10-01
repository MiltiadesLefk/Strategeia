import { lazy, type ComponentType } from 'react';

/**
 * Every page is its own JS chunk, downloaded the first time its route is
 * visited, so opening the Dashboard doesn't also pay for the Settings form or
 * the chart libraries only other pages draw with. The shell (layout, sidebar,
 * login) stays in the main bundle so those never wait on a download.
 *
 * Each page is declared once here as a loader; the same loader feeds both the
 * lazy component and the prefetch below, so a prefetched page is the very
 * module the router later asks for (the browser fetches it once).
 *
 * There is deliberately no automatic retry: once a dynamic import of a URL has
 * failed, the browser remembers the failure for that URL, so retrying it can
 * not succeed. The route boundary offers a reload instead.
 */
const loaders = {
  '/': () => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })),
  '/scan': () => import('./pages/MarketScanPage').then((m) => ({ default: m.MarketScanPage })),
  '/analysis': () => import('./pages/AnalysisPage').then((m) => ({ default: m.AnalysisPage })),
  '/trade-plans': () => import('./pages/TradePlansPage').then((m) => ({ default: m.TradePlansPage })),
  '/portfolio': () => import('./pages/PortfolioPage').then((m) => ({ default: m.PortfolioPage })),
  '/smart-money': () => import('./pages/SmartMoneyPage').then((m) => ({ default: m.SmartMoneyPage })),
  '/terminal': () => import('./pages/MarketTerminalPage').then((m) => ({ default: m.MarketTerminalPage })),
  '/backtests': () => import('./pages/BacktestsPage').then((m) => ({ default: m.BacktestsPage })),
  '/settings': () => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
} satisfies Record<string, () => Promise<{ default: ComponentType }>>;

export type PagePath = keyof typeof loaders;

export const DashboardPage = lazy(loaders['/']);
export const MarketScanPage = lazy(loaders['/scan']);
export const AnalysisPage = lazy(loaders['/analysis']);
export const TradePlansPage = lazy(loaders['/trade-plans']);
export const PortfolioPage = lazy(loaders['/portfolio']);
export const SmartMoneyPage = lazy(loaders['/smart-money']);
export const MarketTerminalPage = lazy(loaders['/terminal']);
export const BacktestsPage = lazy(loaders['/backtests']);
export const SettingsPage = lazy(loaders['/settings']);

const started = new Set<string>();

/** Starts downloading a page's chunk without rendering it, so the click that
 * follows a hover finds it already in the browser cache. Safe to call
 * repeatedly. A failed prefetch is ignored here; if the file really is gone
 * the navigation that follows reaches the route's error boundary. */
export function prefetchRoute(path: string): void {
  if (!(path in loaders) || started.has(path)) return;
  started.add(path);
  loaders[path as PagePath]().catch(() => undefined);
}

/** After first paint, quietly fetch every other page while the browser is
 * idle, one at a time so it never competes with the page being looked at.
 * Skipped on a connection that has asked to save data. */
export function prefetchAllWhenIdle(): void {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
  if (connection?.saveData) return;

  const paths = Object.keys(loaders);
  const schedule = (fn: () => void) =>
    typeof window.requestIdleCallback === 'function' ? window.requestIdleCallback(fn, { timeout: 5000 }) : window.setTimeout(fn, 2000);

  const next = () => {
    const path = paths.shift();
    if (!path) return;
    prefetchRoute(path);
    schedule(next);
  };
  schedule(next);
}

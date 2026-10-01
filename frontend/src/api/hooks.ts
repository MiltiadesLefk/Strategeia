import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  AnalysisResponse,
  AppSettings,
  ArchiveResponse,
  AuthStatus,
  AutoScanResponse,
  DashboardSummary,
  EquityPoint,
  LoginRequest,
  LoginResponse,
  MarketSession,
  Position,
  PortfolioStats,
  ResearchResponse,
  ScanResponse,
  SettingsStatus,
  SettingsUpdateRequest,
  TestConnectionArg,
  TestConnectionResponse,
  TradePlan,
  UniverseEntry,
  WatchlistResponse,
  WatchlistSymbolCheck,
} from './types';
import type { CalibrationReport } from './types';
import type { MissedTradeRefresh, MissedTradeReport } from './types';
import type { StrategyHistory } from './types';
import type { CacheClearResponse, CacheStatus } from './types';
import type {
  SmartMoneyInsiderClusters,
  SmartMoneyInsiderSummary,
  SmartMoneyInsiderTrades,
  SmartMoneyRefreshResponse,
  SmartMoneySide,
  SmartMoneyStatus,
} from './types';
import type { WatcherEvent, WatcherRunResponse, WatchersResponse, WatcherStatus } from './types';
import type {
  BacktestBaseline,
  BacktestBenchmarks,
  BacktestEquityPoint,
  BacktestHistoryCoverage,
  BacktestMetrics,
  BacktestRun,
  BacktestScorecard,
  BacktestStartRequest,
  BacktestTrade,
} from './types';

export const qk = {
  scan: (symbols?: string) => ['scan', symbols] as const,
  analysis: (symbol: string, range: string) => ['analysis', symbol, range] as const,
  research: (symbol: string) => ['research', symbol] as const,
  archive: (symbol: string) => ['archive', symbol] as const,
  tradePlans: ['trade-plans'] as const,
  positions: ['positions'] as const,
  stats: ['stats'] as const,
  calibration: ['calibration'] as const,
  missedTrades: ['missed-trades'] as const,
  watchers: ['watchers'] as const,
  watcherEvents: ['watcher-events'] as const,
  strategyVersions: ['strategy-versions'] as const,
  equityCurve: ['equity-curve'] as const,
  dashboard: ['dashboard'] as const,
  settings: ['settings'] as const,
  settingsStatus: ['settings-status'] as const,
  authStatus: ['auth-status'] as const,
  marketSession: ['market-session'] as const,
  cacheStatus: ['cache-status'] as const,
  watchlist: ['watchlist'] as const,
};

// Refetch just after the next bell (open or close), so the badge and the
// Execute buttons flip within seconds of it — bounded so a sleeping laptop
// or a skewed clock still catches up within a few minutes.
const MARKET_SESSION_MIN_REFETCH_MS = 5_000;
const MARKET_SESSION_MAX_REFETCH_MS = 5 * 60_000;
const MARKET_SESSION_BELL_MARGIN_MS = 2_000;

/**
 * The US market session from the backend's calendar — the one source of
 * truth for holidays and early closes, shared with the engine that refuses
 * off-hours fills. Components tick their own countdowns from next_open /
 * next_close between fetches.
 */
export function useMarketSession() {
  return useQuery({
    queryKey: qk.marketSession,
    queryFn: () => api.get<MarketSession>('/api/market/session'),
    refetchInterval: (query) => {
      const session = query.state.data;
      if (!session) return MARKET_SESSION_MAX_REFETCH_MS;
      const nextBell = new Date(session.is_open ? session.next_close : session.next_open).getTime();
      const wait = nextBell - Date.now() + MARKET_SESSION_BELL_MARGIN_MS;
      return Math.min(Math.max(wait, MARKET_SESSION_MIN_REFETCH_MS), MARKET_SESSION_MAX_REFETCH_MS);
    },
  });
}

export function useScan(symbols?: string) {
  return useQuery({
    queryKey: qk.scan(symbols),
    queryFn: () => api.get<ScanResponse>(`/api/scan${symbols ? `?symbols=${symbols}` : ''}`),
  });
}

export function useRunAutoScanNow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AutoScanResponse>('/api/scan/auto-trade'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.tradePlans });
      queryClient.invalidateQueries({ queryKey: qk.positions });
      queryClient.invalidateQueries({ queryKey: qk.stats });
      queryClient.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useUniverse() {
  return useQuery({
    queryKey: ['universe'] as const,
    queryFn: () => api.get<UniverseEntry[]>('/api/universe'),
    staleTime: Infinity, // only changes when the watchlist is saved or reset, which invalidates this
  });
}

/**
 * `enabled: false` defers the request — used by Portfolio's position cards so
 * N open positions don't fire N simultaneous 1-year analysis requests on page
 * load for charts that are mostly below the fold. (The fetch itself isn't
 * wasted: the same response draws the candlestick chart. It just doesn't need
 * to happen before the card is on screen.)
 */
export function useAnalysis(symbol: string | null, range: string = '3mo', enabled: boolean = true) {
  return useQuery({
    queryKey: qk.analysis(symbol ?? '', range),
    queryFn: () => api.get<AnalysisResponse>(`/api/analysis/${symbol}?range=${range}`),
    enabled: !!symbol && enabled,
  });
}

export function useResearch(symbol: string | null) {
  return useQuery({
    queryKey: qk.research(symbol ?? ''),
    queryFn: () => api.get<ResearchResponse>(`/api/research/${symbol}`),
    enabled: !!symbol,
  });
}

/**
 * What the dated archive holds for one symbol. Read-only on the backend. The
 * research request is what fills the archive, so callers should mount this
 * after the research data has loaded (or it can show the counts from before
 * that visit).
 */
export function useArchive(symbol: string | null) {
  return useQuery({
    queryKey: qk.archive(symbol ?? ''),
    queryFn: () => api.get<ArchiveResponse>(`/api/archive/${symbol}`),
    enabled: !!symbol,
  });
}

export function useTradePlans() {
  return useQuery({ queryKey: qk.tradePlans, queryFn: () => api.get<TradePlan[]>('/api/trade-plans') });
}

export function useGenerateTradePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => api.post<TradePlan>('/api/trade-plans/generate', { symbol }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.tradePlans });
      // A plan generated under changed settings creates a new strategy version.
      queryClient.invalidateQueries({ queryKey: qk.strategyVersions });
    },
  });
}

export function useStrategyVersions() {
  return useQuery({ queryKey: qk.strategyVersions, queryFn: () => api.get<StrategyHistory>('/api/strategy/versions') });
}

export function usePositions() {
  return useQuery({ queryKey: qk.positions, queryFn: () => api.get<Position[]>('/api/portfolio/positions') });
}

export function useOpenPosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tradePlanId: number) => api.post<Position>('/api/portfolio/positions', { trade_plan_id: tradePlanId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.positions });
      queryClient.invalidateQueries({ queryKey: qk.stats });
      queryClient.invalidateQueries({ queryKey: qk.tradePlans });
      queryClient.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useClosePosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (positionId: number) => api.post<Position>(`/api/portfolio/positions/${positionId}/close`, { reason: 'manual' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.positions });
      queryClient.invalidateQueries({ queryKey: qk.stats });
      queryClient.invalidateQueries({ queryKey: qk.equityCurve });
      queryClient.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

/** Write (or rewrite) the AI lesson for one closed position. A model failure comes back as a 200
 *  with `lesson_error` set on the position, so a refetch of the positions list shows it. */
export function useWriteLesson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (positionId: number) => api.post<Position>(`/api/portfolio/positions/${positionId}/lesson`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.positions });
    },
  });
}

export function usePortfolioStats() {
  return useQuery({ queryKey: qk.stats, queryFn: () => api.get<PortfolioStats>('/api/portfolio/stats') });
}

export function useResetPortfolio() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<PortfolioStats>('/api/portfolio/reset'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.positions });
      queryClient.invalidateQueries({ queryKey: qk.stats });
      queryClient.invalidateQueries({ queryKey: qk.equityCurve });
      queryClient.invalidateQueries({ queryKey: qk.tradePlans });
      queryClient.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useEquityCurve() {
  return useQuery({ queryKey: qk.equityCurve, queryFn: () => api.get<EquityPoint[]>('/api/portfolio/equity-curve') });
}

export function useDashboardSummary() {
  return useQuery({ queryKey: qk.dashboard, queryFn: () => api.get<DashboardSummary>('/api/dashboard/summary') });
}

export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api.get<AppSettings>('/api/settings') });
}

export function useSettingsStatus() {
  return useQuery({
    queryKey: qk.settingsStatus,
    queryFn: () => api.get<SettingsStatus>('/api/settings/status'),
    refetchInterval: 60_000,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: SettingsUpdateRequest) => api.put<AppSettings>('/api/settings', req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.settings });
      // Sidebar's online/offline pills otherwise only catch up on the next
      // 60s poll — a save that changes provider/keys should reflect there
      // immediately, not up to a minute later.
      queryClient.invalidateQueries({ queryKey: qk.settingsStatus });
    },
  });
}

// AuthGate's own hooks — see components/AuthGate.tsx. Kept in this file
// alongside every other endpoint wrapper per this project's own rule
// (api/hooks.ts wraps every backend endpoint, no ad-hoc fetch calls in
// components) rather than living next to the component that happens to be
// their only caller today.

export function useAuthStatus() {
  return useQuery({
    queryKey: qk.authStatus,
    queryFn: () => api.get<AuthStatus>('/api/auth/status'),
    // No retry: a 401 here would just mean "not logged in", not a
    // transient failure worth retrying — react-query's default retry
    // would otherwise turn one wrong-password check into a burst of
    // requests against the same lockout counter.
    retry: false,
    // Catches a session that expired (session_lifetime_days elapsed, or
    // the backend restarted with a fresh session_secret — see
    // app/auth.py) within a minute, same poll cadence as the sidebar's
    // useSettingsStatus, rather than only re-checking on the next full
    // page load/window focus. main.tsx's global 401 handler covers the
    // gap between polls: any OTHER request hitting a 401 invalidates this
    // query immediately too.
    refetchInterval: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: LoginRequest) => api.post<LoginResponse>('/api/auth/login', req),
    onSuccess: () => {
      // A 200 here already means the cookie is set and valid — writing
      // the known result directly (rather than invalidateQueries, which
      // only SCHEDULES a refetch) makes AuthGate show the real app on
      // this same tick instead of one more request-and-render cycle
      // later, and sidesteps any ordering question with the
      // queryClient.clear() a previous logout may have just run.
      queryClient.setQueryData(qk.authStatus, { authenticated: true });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<LoginResponse>('/api/auth/logout'),
    onSuccess: () => {
      // Order matters: queryClient.clear() tears down every query
      // observer, authStatus's included — AuthGate's useAuthStatus() was
      // subscribed to the entry clear() just destroyed, and a
      // setQueryData call made AFTER that teardown was observed to write
      // into the cache without ever notifying AuthGate, leaving the real
      // app rendered under a logged-out session. Setting the known result
      // FIRST (while AuthGate's subscription is still live, so it
      // actually re-renders to the login screen) then removing everything
      // ELSE avoids that — the next login still starts from a clean
      // slate, just without ever tearing down the one query the UI is
      // currently keyed off.
      queryClient.setQueryData(qk.authStatus, { authenticated: false });
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== qk.authStatus[0] });
    },
  });
}

export function useTestConnection() {
  return useMutation({
    // `tier` only matters for 'llm': the routine model (narratives, the default) or the decision model (AI overlay).
    // `overrides` carries unsaved form values so the test uses what is typed, not only what is saved.
    mutationFn: (arg: TestConnectionArg) =>
      api.post<TestConnectionResponse>('/api/settings/test-connection', typeof arg === 'string' ? { target: arg } : arg),
  });
}

export function useCalibration() {
  return useQuery({ queryKey: qk.calibration, queryFn: () => api.get<CalibrationReport>('/api/portfolio/calibration') });
}

/** What the trades the app declined would have earned. Read-only on the server. */
export function useMissedTrades() {
  return useQuery({ queryKey: qk.missedTrades, queryFn: () => api.get<MissedTradeReport>('/api/missed-trades') });
}

/** Computes the missed-trade outcomes that are new or still open, then reloads the report. */
export function useRefreshMissedTrades() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<MissedTradeRefresh>('/api/missed-trades/refresh'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.missedTrades }),
  });
}

/** Read-only numbers for the Settings page's data-cache card. */
export function useCacheStatus() {
  return useQuery({ queryKey: qk.cacheStatus, queryFn: () => api.get<CacheStatus>('/api/cache/status') });
}

export function useClearCache() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<CacheClearResponse>('/api/cache/clear'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.cacheStatus }),
  });
}

/** The watchlist the Settings card edits: which layer is active, the saved list, the limits. */
export function useWatchlist() {
  return useQuery({ queryKey: qk.watchlist, queryFn: () => api.get<WatchlistResponse>('/api/watchlist') });
}

// A saved or reset watchlist changes what every symbol picker, scan and the
// dashboard's top setups are built from, so all of those refetch.
function useAfterWatchlistChange() {
  const queryClient = useQueryClient();
  return (data: WatchlistResponse) => {
    queryClient.setQueryData(qk.watchlist, data);
    queryClient.invalidateQueries({ queryKey: ['universe'] });
    queryClient.invalidateQueries({ queryKey: ['scan'] });
    queryClient.invalidateQueries({ queryKey: qk.dashboard });
  };
}

export function useSaveWatchlist() {
  const after = useAfterWatchlistChange();
  return useMutation({
    mutationFn: (symbols: string[]) => api.put<WatchlistResponse>('/api/watchlist', { symbols }),
    onSuccess: after,
  });
}

export function useResetWatchlist() {
  const after = useAfterWatchlistChange();
  return useMutation({
    mutationFn: () => api.delete<WatchlistResponse>('/api/watchlist'),
    onSuccess: after,
  });
}

/** Checks that a candidate symbol really returns a quote. Saves nothing. */
export function useValidateWatchlistSymbol() {
  return useMutation({
    mutationFn: (symbol: string) => api.post<WatchlistSymbolCheck>('/api/watchlist/validate', { symbol }),
  });
}

// ---- Backtest Lab ----

export const backtestKeys = {
  list: ['backtests'] as const,
  run: (id: number) => ['backtests', id] as const,
  trades: (id: number) => ['backtests', id, 'trades'] as const,
  equity: (id: number) => ['backtests', id, 'equity'] as const,
  metrics: (id: number) => ['backtests', id, 'metrics'] as const,
  benchmarks: (id: number) => ['backtests', id, 'benchmarks'] as const,
  baseline: (id: number) => ['backtests', id, 'baseline'] as const,
  scorecard: (id: number, query: string) => ['backtests', id, 'scorecard', query] as const,
  coverage: (symbols: string) => ['backtests', 'history-coverage', symbols] as const,
};

const BACKTEST_POLL_MS = 2_000;
const backtestActive = (status: string | undefined) => status === 'queued' || status === 'running';

/** Past runs, newest first; polls while any of them is still going. */
export function useBacktests() {
  return useQuery({
    queryKey: backtestKeys.list,
    queryFn: () => api.get<BacktestRun[]>('/api/backtests?limit=100'),
    refetchInterval: (query) => (query.state.data?.some((r) => backtestActive(r.status)) ? BACKTEST_POLL_MS : false),
  });
}

/** One run with its progress; polls until it reaches a final state. */
export function useBacktest(id: number | null) {
  return useQuery({
    queryKey: backtestKeys.run(id ?? 0),
    queryFn: () => api.get<BacktestRun>(`/api/backtests/${id}`),
    enabled: id !== null,
    refetchInterval: (query) => (backtestActive(query.state.data?.status) ? BACKTEST_POLL_MS : false),
  });
}

/** The statistics of a run. `ready` is true once the main run has saved results (also while the baseline is still going). */
export function useBacktestMetrics(id: number | null, ready: boolean, refresh: boolean) {
  return useQuery({
    queryKey: backtestKeys.metrics(id ?? 0),
    queryFn: () => api.get<BacktestMetrics>(`/api/backtests/${id}/metrics`),
    enabled: id !== null && ready,
    refetchInterval: refresh ? 5_000 : false,
  });
}

export function useBacktestTrades(id: number | null, ready: boolean) {
  return useQuery({
    queryKey: backtestKeys.trades(id ?? 0),
    queryFn: () => api.get<BacktestTrade[]>(`/api/backtests/${id}/trades`),
    enabled: id !== null && ready,
  });
}

export function useBacktestEquity(id: number | null, ready: boolean) {
  return useQuery({
    queryKey: backtestKeys.equity(id ?? 0),
    queryFn: () => api.get<BacktestEquityPoint[]>(`/api/backtests/${id}/equity`),
    enabled: id !== null && ready,
  });
}

export function useBacktestBenchmarks(id: number | null, ready: boolean) {
  return useQuery({
    queryKey: backtestKeys.benchmarks(id ?? 0),
    queryFn: () => api.get<BacktestBenchmarks>(`/api/backtests/${id}/benchmarks`),
    enabled: id !== null && ready,
  });
}

/** `refresh`: poll while the random runs are still finishing. */
export function useBacktestBaseline(id: number | null, ready: boolean, refresh: boolean) {
  return useQuery({
    queryKey: backtestKeys.baseline(id ?? 0),
    queryFn: () => api.get<BacktestBaseline>(`/api/backtests/${id}/baseline`),
    enabled: id !== null && ready,
    refetchInterval: refresh ? 4_000 : false,
  });
}

export function useBacktestScorecard(id: number | null, ready: boolean, refresh: boolean, query = '') {
  return useQuery({
    queryKey: backtestKeys.scorecard(id ?? 0, query),
    queryFn: () => api.get<BacktestScorecard>(`/api/backtests/${id}/scorecard${query}`),
    enabled: id !== null && ready,
    refetchInterval: refresh ? 5_000 : false,
  });
}

/** What price history is stored for these symbols (and SPY, ^VIX). Read-only: nothing is downloaded. */
export function useBacktestHistoryCoverage(symbols: string[]) {
  const joined = symbols.join(',');
  return useQuery({
    queryKey: backtestKeys.coverage(joined),
    queryFn: () => api.get<BacktestHistoryCoverage>(`/api/backtests/history-coverage?symbols=${encodeURIComponent(joined)}`),
  });
}

export function useStartBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BacktestStartRequest) => api.post<{ id: number; status: string }>('/api/backtests', body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backtestKeys.list }),
  });
}

export function useCancelBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<{ id: number; status: string }>(`/api/backtests/${id}/cancel`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backtestKeys.list }),
  });
}

/** Installed watchers with their saved state. Read-only on the server. */
export function useWatchers() {
  return useQuery({ queryKey: qk.watchers, queryFn: () => api.get<WatchersResponse>('/api/watchers') });
}

/** The newest recorded watcher events, suppressed ones included. */
export function useWatcherEvents(limit = 20) {
  return useQuery({
    queryKey: [...qk.watcherEvents, limit],
    queryFn: () => api.get<WatcherEvent[]>(`/api/watchers/events?limit=${limit}`),
  });
}

function useInvalidateWatchers() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: qk.watchers });
    queryClient.invalidateQueries({ queryKey: qk.watcherEvents });
  };
}

/** Poll one watcher now. */
export function useRunWatcher() {
  const invalidate = useInvalidateWatchers();
  return useMutation({
    mutationFn: (name: string) => api.post<WatcherRunResponse>(`/api/watchers/${encodeURIComponent(name)}/run`),
    onSuccess: invalidate,
  });
}

/** Turn one watcher on or off (on top of the master switch). */
export function useSetWatcherEnabled() {
  const invalidate = useInvalidateWatchers();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.put<WatcherStatus>(`/api/watchers/${encodeURIComponent(name)}`, { enabled }),
    onSuccess: invalidate,
  });
}

const smartMoneyKeys = {
  all: ['smart-money'] as const,
  status: ['smart-money', 'status'] as const,
  trades: (days: number, side: string, symbol: string, minValue: number) =>
    ['smart-money', 'insiders', days, side, symbol, minValue] as const,
  clusters: (days: number, symbol: string) => ['smart-money', 'clusters', days, symbol] as const,
  summary: (symbol: string) => ['smart-money', 'summary', symbol] as const,
};

export function useSmartMoneyStatus() {
  return useQuery({ queryKey: smartMoneyKeys.status, queryFn: () => api.get<SmartMoneyStatus>('/api/smart-money/status') });
}

export function useSmartMoneyInsiders(params: { days: number; side: SmartMoneySide; symbol: string; minValue: number }) {
  const q = new URLSearchParams({ days: String(params.days), side: params.side, min_value: String(params.minValue) });
  if (params.symbol) q.set('symbol', params.symbol);
  return useQuery({
    queryKey: smartMoneyKeys.trades(params.days, params.side, params.symbol, params.minValue),
    queryFn: () => api.get<SmartMoneyInsiderTrades>(`/api/smart-money/insiders?${q}`),
  });
}

export function useSmartMoneyClusters(days: number, symbol: string) {
  const q = new URLSearchParams({ days: String(days) });
  if (symbol) q.set('symbol', symbol);
  return useQuery({
    queryKey: smartMoneyKeys.clusters(days, symbol),
    queryFn: () => api.get<SmartMoneyInsiderClusters>(`/api/smart-money/insiders/clusters?${q}`),
  });
}

export function useSmartMoneySummary(symbol: string) {
  return useQuery({
    queryKey: smartMoneyKeys.summary(symbol),
    queryFn: () => api.get<SmartMoneyInsiderSummary>(`/api/smart-money/insiders/summary/${encodeURIComponent(symbol)}`),
    enabled: !!symbol,
  });
}

/** Load Form 4 filings from SEC for the watchlist (a few symbols per call), then refetch every Smart Money view. */
export function useRefreshSmartMoneyInsiders() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SmartMoneyRefreshResponse>('/api/smart-money/insiders/refresh'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: smartMoneyKeys.all }),
  });
}

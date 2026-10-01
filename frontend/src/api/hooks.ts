import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  AnalysisResponse,
  AppSettings,
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
  TestConnectionResponse,
  TradePlan,
  UniverseEntry,
} from './types';

export const qk = {
  scan: (symbols?: string) => ['scan', symbols] as const,
  analysis: (symbol: string, range: string) => ['analysis', symbol, range] as const,
  research: (symbol: string) => ['research', symbol] as const,
  tradePlans: ['trade-plans'] as const,
  positions: ['positions'] as const,
  stats: ['stats'] as const,
  equityCurve: ['equity-curve'] as const,
  dashboard: ['dashboard'] as const,
  settings: ['settings'] as const,
  settingsStatus: ['settings-status'] as const,
  authStatus: ['auth-status'] as const,
  marketSession: ['market-session'] as const,
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
    staleTime: Infinity, // static bundled list, never changes at runtime
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

export function useTradePlans() {
  return useQuery({ queryKey: qk.tradePlans, queryFn: () => api.get<TradePlan[]>('/api/trade-plans') });
}

export function useGenerateTradePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => api.post<TradePlan>('/api/trade-plans/generate', { symbol }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.tradePlans }),
  });
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
    mutationFn: (target: 'llm' | 'finnhub' | 'telegram') => api.post<TestConnectionResponse>('/api/settings/test-connection', { target }),
  });
}

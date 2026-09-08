import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  AnalysisResponse,
  AppSettings,
  AutoScanResponse,
  DashboardSummary,
  EquityPoint,
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
};

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

export function useAnalysis(symbol: string | null, range: string = '3mo') {
  return useQuery({
    queryKey: qk.analysis(symbol ?? '', range),
    queryFn: () => api.get<AnalysisResponse>(`/api/analysis/${symbol}?range=${range}`),
    enabled: !!symbol,
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
    queryKey: ['settings-status'] as const,
    queryFn: () => api.get<SettingsStatus>('/api/settings/status'),
    refetchInterval: 60_000,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: SettingsUpdateRequest) => api.put<AppSettings>('/api/settings', req),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.settings }),
  });
}

export function useTestConnection() {
  return useMutation({
    mutationFn: (target: 'llm' | 'finnhub' | 'telegram') => api.post<TestConnectionResponse>('/api/settings/test-connection', { target }),
  });
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { ForecastModelDetail, ForecastModelSummary, ForecastPrediction, ForecastStatus } from './forecastTypes';

const ROOT = ['forecast-lab'] as const;
const forecastKeys = {
  status: [...ROOT, 'status'] as const,
  models: [...ROOT, 'models'] as const,
  model: (id: number) => [...ROOT, 'model', id] as const,
  predictions: [...ROOT, 'predictions'] as const,
};

export function useForecastStatus() {
  return useQuery({ queryKey: forecastKeys.status, queryFn: () => api.get<ForecastStatus>('/api/forecast-lab/status') });
}

export function useForecastModels() {
  return useQuery({ queryKey: forecastKeys.models, queryFn: () => api.get<ForecastModelSummary[]>('/api/forecast-lab/models') });
}

export function useForecastModel(id: number | null) {
  return useQuery({
    queryKey: forecastKeys.model(id ?? 0),
    queryFn: () => api.get<ForecastModelDetail>(`/api/forecast-lab/models/${id}`),
    enabled: id !== null,
  });
}

export function useForecastPredictions() {
  return useQuery({
    queryKey: forecastKeys.predictions,
    queryFn: () => api.get<ForecastPrediction[]>('/api/forecast-lab/predictions?limit=50'),
  });
}

function useForecastMutation<TArg, TResult>(fn: (arg: TArg) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ROOT }),
  });
}

/** Training only ever happens from this explicit call. */
export function useTrainForecastModel() {
  return useForecastMutation((runIds: number[]) =>
    api.post<ForecastModelDetail>('/api/forecast-lab/models/train', { run_ids: runIds }),
  );
}

export function useActivateForecastModel() {
  return useForecastMutation((id: number) => api.post<ForecastModelDetail>(`/api/forecast-lab/models/${id}/activate`));
}

export function useDeactivateForecastModel() {
  return useForecastMutation((_: void) => api.post<{ ok: boolean }>('/api/forecast-lab/models/deactivate'));
}

export function useDeleteForecastModel() {
  return useForecastMutation((id: number) => api.delete<{ ok: boolean }>(`/api/forecast-lab/models/${id}`));
}

export function useCreateMlSleeve() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<unknown>('/api/forecast-lab/sleeve'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ROOT });
      queryClient.invalidateQueries({ queryKey: ['sleeves'] });
    },
  });
}

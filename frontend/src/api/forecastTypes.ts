// Forecast Lab (ML style). Mirrors the backend forecast schemas (app/schemas/forecast_schemas.py).
export interface ForecastRun {
  id: number;
  finished_at: string | null;
  closed_trades: number;
}
export interface ForecastModelSummary {
  id: number;
  created_at: string;
  active: boolean;
  run_ids: number[];
  n_rows: number;
  n_test: number;
  verdict: 'not_enough_data' | 'edge_out_of_sample' | 'no_clear_edge' | 'inverted' | string;
}
export interface ForecastMean {
  n: number;
  mean: number | null;
  low: number | null;
  high: number | null;
}
export interface ForecastMetrics {
  validation_l2?: number;
  test_l2?: number;
  test_baseline_l2?: number;
  ic?: { n: number; ic: number | null; low: number | null; high: number | null };
  all_test_trades?: ForecastMean;
  kept_by_model?: ForecastMean;
  stopped_by_model?: ForecastMean;
  min_expected_r?: number;
  verdict?: string;
}
export interface ForecastModelDetail extends ForecastModelSummary {
  feature_names: string[];
  best_iteration: number;
  params: Record<string, unknown>;
  split: Record<string, number | string>;
  metrics: ForecastMetrics;
  importance: { feature: string; mean_abs_shap: number }[];
  notes: string[];
}
export interface ForecastStatus {
  extras_available: boolean;
  extras_missing: string[];
  extras_message: string;
  enabled: boolean;
  min_expected_r: number;
  min_rows: number;
  ml_sleeve_key: string | null;
  active_model: ForecastModelSummary | null;
  runs: ForecastRun[];
}
export interface ForecastPrediction {
  id: number;
  plan_id: number;
  symbol: string | null;
  created_at: string;
  model_id: number | null;
  available: boolean;
  expected_r: number | null;
  min_expected_r: number | null;
  stopped_trade: boolean;
  top_features: { feature: string; value: number; shap: number }[];
  base_value: number | null;
  note: string | null;
}

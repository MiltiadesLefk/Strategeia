export interface UniverseEntry {
  symbol: string;
  name: string;
  sector: string;
}

export interface ScanResult {
  symbol: string;
  price: number;
  change_pct_24h: number;
  signal: 'potential_setup' | 'watching' | 'no_signal';
  score: number;
  direction: 'long' | 'short' | null;
  trend: string;
  momentum: string;
  sparkline: number[];
}

export interface ScanResponse {
  results: ScanResult[];
  errors: string[];
}

export interface AutoScanResponse {
  generated: string[];
  no_trade: string[];
}

export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SeriesPoint {
  date: string;
  value: number;
}

export interface AnalysisResponse {
  symbol: string;
  price: number;
  ema20: number;
  ema50: number;
  rsi14: number;
  trend: string;
  momentum: string;
  support: number[];
  resistance: number[];
  /** ATR14 in price terms, and as a % of price — the comparable form. */
  atr14?: number | null;
  atr_pct?: number | null;
  macd?: number | null;
  macd_signal?: number | null;
  candles: Candle[];
  ema20_series: SeriesPoint[];
  ema50_series: SeriesPoint[];
  bollinger_upper_series?: SeriesPoint[];
  bollinger_lower_series?: SeriesPoint[];
  rsi_series?: SeriesPoint[];
  macd_series?: SeriesPoint[];
  macd_signal_series?: SeriesPoint[];
  macd_histogram_series?: SeriesPoint[];
  /** Empty on daily ranges by design — VWAP is an intraday measure. */
  vwap_series?: SeriesPoint[];
  insight_text: string;
  ai_provider: string;
  ai_error: string | null;
}

export interface FinancialYear {
  year: number;
  revenue: number;
  net_income: number;
}

export interface NewsItem {
  headline: string;
  source: string;
  url: string;
  published_at: string;
}

export interface ResearchResponse {
  symbol: string;
  name: string;
  price: number;
  change: number | null;
  change_pct: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  revenue_ttm: number | null;
  eps_ttm: number | null;
  revenue_yoy_pct: number | null;
  week52_low: number | null;
  week52_high: number | null;
  financials: FinancialYear[];
  news: NewsItem[];
  catalysts: string[];
  earnings_date: string | null;
  earnings_days_until: number | null;
  earnings_fiscal_label: string | null;
  earnings_eps_estimate: number | null;
  earnings_revenue_estimate: number | null;
  ai_summary: string;
  ai_provider: string;
  ai_error: string | null;
  generated_at: string;
}

export interface TradePlan {
  id: number | null;
  symbol: string;
  direction: 'long' | 'short' | null;
  reason?: string | null;
  entry?: number | null;
  stop?: number | null;
  tp1?: number | null;
  tp2?: number | null;
  rr1?: number | null;
  rr2?: number | null;
  suggested_shares?: number | null;
  account_risk_dollars?: number | null;
  potential_gain?: number | null;
  potential_risk?: number | null;
  /** Size was cut to what the account can actually fund. */
  capped_by_cash?: boolean | null;
  /** ATR14, and how many ATRs the stop sits from entry. Under ~1 the stop is
   *  inside the instrument's normal daily range and noise will take it out. */
  atr?: number | null;
  stop_atr_multiple?: number | null;
  confidence_score?: number | null;
  time_horizon?: string | null;
  ai_take_text?: string | null;
  ai_provider?: string | null;
  status?: string | null;
  created_at?: string | null;
  technical_score?: number | null;
  fundamental_score?: number | null;
  news_score?: number | null;
  market_confirmation_score?: number | null;
  vix_regime_score?: number | null;
  options_score?: number | null;
  insider_score?: number | null;
  signal_reasons?: string | null;
  ai_opinion_stance?: 'bullish' | 'bearish' | 'neutral' | null;
  ai_opinion_score?: number | null;
  ai_opinion_text?: string | null;
  ai_news_assessment?: string | null;
}

export interface Position {
  id: number;
  trade_plan_id: number | null;
  symbol: string;
  direction: string;
  entry_price: number;
  /** What the plan asked for, vs entry_price = the actual fill. */
  planned_entry_price?: number | null;
  stop_loss: number;
  tp1: number;
  tp2: number;
  shares: number;
  opened_at: string;
  status: 'open' | 'closed';
  closed_at: string | null;
  close_price: number | null;
  close_reason: string | null;
  realized_pnl: number | null;
  realized_r: number | null;
  /** Round-trip commission; realized_pnl is already net of it. */
  fees_paid?: number | null;
}

export interface PortfolioStats {
  total_trades: number;
  win_rate: number;
  total_return: number;
  avg_rr: number | null;
  active_positions: number;
  portfolio_value: number;
  starting_cash: number;
  current_cash: number;
}

export interface EquityPoint {
  timestamp: string;
  equity_value: number;
  cash_balance: number;
}

export interface DashboardSummary {
  stats: PortfolioStats;
  markets_scanned: number;
  potential_setups: number;
  top_setups: ScanResult[];
  latest_trade_plan: TradePlan | null;
  top_pick_trade_plan: TradePlan | null;
}

export interface AppSettings {
  llm_provider: string;
  /** Masked hint like "••••ab12", or "" if unset — the real key is never sent to the client. */
  openrouter_api_key: string;
  openrouter_model: string;
  orcarouter_api_key: string;
  orcarouter_model: string;
  openai_api_key: string;
  openai_model: string;
  gemini_api_key: string;
  gemini_model: string;
  finnhub_enabled: boolean;
  finnhub_api_key: string;
  telegram_bot_token: string;
  telegram_chat_id: string;
  scan_universe_size: number;
  paper_starting_cash: number;
  default_risk_pct: number;
  mark_to_market_interval_minutes: number;
  auto_execute_trade_plans: boolean;
  auto_scan_enabled: boolean;
  max_concurrent_positions: number;
  ai_trading_overlay_enabled: boolean;
}

export interface SettingsUpdateRequest {
  llm_provider?: string;
  openrouter_api_key?: string;
  openrouter_model?: string;
  orcarouter_api_key?: string;
  orcarouter_model?: string;
  openai_api_key?: string;
  openai_model?: string;
  gemini_api_key?: string;
  gemini_model?: string;
  finnhub_enabled?: boolean;
  finnhub_api_key?: string;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  scan_universe_size?: number;
  paper_starting_cash?: number;
  default_risk_pct?: number;
  mark_to_market_interval_minutes?: number;
  auto_execute_trade_plans?: boolean;
  auto_scan_enabled?: boolean;
  max_concurrent_positions?: number;
  ai_trading_overlay_enabled?: boolean;
}

export interface TestConnectionResponse {
  ok: boolean;
  message: string;
}

export interface SettingsStatus {
  ai_online: boolean;
  ai_provider: string;
  ai_overlay_online: boolean;
  finnhub_online: boolean;
  telegram_online: boolean;
}

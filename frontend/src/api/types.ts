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
  /** Options-implied +/-% move by the nearest usable expiration, and the
   *  median actual +/-% move this stock has made around its last reported
   *  earnings dates. Informational only, tradeable plans only — neither is a
   *  prediction of direction. */
  expected_move_pct?: number | null;
  historical_earnings_move_pct?: number | null;
  confidence_score?: number | null;
  /** The raw evidence points behind confidence_score, and the most the engine
   *  can award. Confidence is quantised — only 17 values are reachable, 6.25
   *  apart — so the percentage alone implies precision it does not have. */
  confidence_points?: number | null;
  confidence_points_max?: number | null;
  time_horizon?: string | null;
  ai_take_text?: string | null;
  ai_provider?: string | null;
  status?: string | null;
  /** What happened at the auto-execute step, in plain English — executed,
   *  skipped with a reason, held for manual review because the AI Trading
   *  Overlay's stance was the flat opposite of the rule-based direction, or
   *  not executed because the market was closed (see redo_at).
   *  With ai_overlay_vetoes_trade on (the default once the overlay is on) a
   *  contradiction never reaches this step — the evaluation ends as a
   *  no_trade record with the overlay named in `reason` instead. */
  auto_execute_note?: string | null;
  /** Set while a plan made with its market closed waits to be redone from
   *  fresh data at the next open (ISO UTC). Such a plan can't be executed by
   *  hand — only the fresh plan can. */
  redo_at?: string | null;
  created_at?: string | null;
  technical_score?: number | null;
  fundamental_score?: number | null;
  news_score?: number | null;
  market_confirmation_score?: number | null;
  vix_regime_score?: number | null;
  options_score?: number | null;
  insider_score?: number | null;
  /** One-directional risk flags (0 or a penalty, never a bonus): options
   *  market pricing an outsized move, and a scheduled FOMC/CPI/jobs release
   *  in the next day. */
  expected_move_score?: number | null;
  macro_event_score?: number | null;
  /** Genuine +/-, like fundamental_score: a consistent beat/miss streak on
   *  reported (not upcoming) consensus EPS. */
  earnings_surprise_score?: number | null;
  /** What the AI Trading Overlay's disagreement cost the confidence score:
   *  0 or negative, never positive. Distinct from ai_opinion_score below,
   *  which is the AI's own stated conviction in its own stance. */
  ai_overlay_score?: number | null;
  signal_reasons?: string | null;
  ai_opinion_stance?: 'bullish' | 'bearish' | 'neutral' | null;
  /** The AI's answer to "would you take this trade?" — a different question
   *  from ai_opinion_stance (where it thinks the stock goes), and the one
   *  actually acted on. Null when the model gave no usable verdict. */
  ai_trade_verdict?: 'take' | 'pass' | null;
  ai_opinion_score?: number | null;
  ai_opinion_text?: string | null;
  ai_news_assessment?: string | null;
}

/** GET /api/market/session — the US session from the backend's one calendar
 *  (weekends, NYSE holidays, 1:00 pm early closes). */
export type MarketSessionState = 'open' | 'pre' | 'after' | 'closed' | 'holiday';

export interface MarketSession {
  state: MarketSessionState;
  is_open: boolean;
  /** The first open / close still ahead (ISO UTC). While open, next_open is
   *  the NEXT trading day's; next_close is 1:00 pm ET on an early-close day. */
  next_open: string;
  next_close: string;
  next_close_is_early: boolean;
  /** Set only when state is 'holiday'. */
  holiday_name: string | null;
  as_of: string;
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
  /** How closed trades ended: close_reason -> count, from the closed rows. */
  exit_reasons: Record<string, number>;
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
  /** Model the Claude Code CLI is pinned to ("sonnet", "opus", a full id...). "" = don't pin (whatever the CLI is set to). */
  claude_cli_model: string;
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
  /** Trading days before a stalled position is closed at the close; 0 = no limit. */
  max_holding_days: number;
  ai_trading_overlay_enabled: boolean;
  ai_overlay_scores_confidence: boolean;
  ai_overlay_objection_action: AiOverlayObjectionAction;
  min_confidence_for_trade: number;
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
  claude_cli_model?: string;
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
  max_holding_days?: number;
  ai_trading_overlay_enabled?: boolean;
  ai_overlay_scores_confidence?: boolean;
  ai_overlay_objection_action?: AiOverlayObjectionAction;
  min_confidence_for_trade?: number;
}

export interface TestConnectionResponse {
  ok: boolean;
  message: string;
}

/** What the AI Trading Overlay's objection actually does. One choice, not a
 *  set of flags: "cancel" and "hold" fire on the same trigger and cancel
 *  always wins, so they can never both be in effect. */
export type AiOverlayObjectionAction = 'cancel' | 'hold' | 'none';

export interface SettingsStatus {
  ai_online: boolean;
  ai_provider: string;
  /** Model the provider is pinned to ("" when it has no pin or the pin is blank). */
  ai_model: string;
  ai_overlay_online: boolean;
  finnhub_online: boolean;
  telegram_online: boolean;
}

export interface AuthStatus {
  authenticated: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  ok: boolean;
  message: string;
}

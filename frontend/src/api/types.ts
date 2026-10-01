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

export interface ArchivedNewsItem {
  headline: string;
  publisher: string;
  url: string;
  published_at: string;
  known_at: string;
  known_at_basis: 'source' | 'fetched' | 'derived';
  fetched_at: string;
}

export interface ArchivedFundamentals {
  known_at: string;
  fetched_at: string;
  revenue_ttm: number | null;
  eps_ttm: number | null;
  market_cap: number | null;
  latest_fiscal_year: number | null;
  financial_years: number;
}

export interface ArchiveTotals {
  news_count: number;
  fundamentals_count: number;
  symbols: number;
  first_archived_at: string | null;
  last_archived_at: string | null;
}

export interface ArchiveResponse {
  symbol: string;
  news_count: number;
  fundamentals_count: number;
  first_archived_at: string | null;
  last_archived_at: string | null;
  recent_news: ArchivedNewsItem[];
  recent_fundamentals: ArchivedFundamentals[];
  totals: ArchiveTotals;
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
  /** Model that gave that verdict (the overlay's decision tier); null when the overlay did not run. */
  ai_decision_model?: string | null;
  /** How the overlay's reply was read: 'structured' (matched the form), 'lenient'
   *  (pulled out of a damaged reply) or 'failed' (answered, nothing usable). */
  ai_opinion_parse?: 'structured' | 'lenient' | 'failed' | null;
  /** Newline-separated figures the overlay quoted that were not in the data it
   *  was given. An honesty signal only; never affects the verdict or score. */
  ai_grounding_warnings?: string | null;
  ai_opinion_score?: number | null;
  ai_opinion_text?: string | null;
  ai_news_assessment?: string | null;
  /** Number of the strategy version (rules + decision-relevant settings) this
   *  plan was made under; null/absent for plans from before versioning. */
  strategy_version?: number | null;
  /** Silent signals: recorded, never part of confidence_score. */
  shadow_signals?: ShadowSignal[] | null;
}

export interface ShadowSignal {
  name: string;
  value?: string | null;
  /** Points it WOULD have added (direction-signed, capped); never counted. */
  would_score: number;
  reason: string;
  available: boolean;
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
  /** The strategy version of the plan this position came from (read through
   *  the plan); null for a manual position or a pre-versioning plan. */
  strategy_version?: number | null;
  /** Best/worst price during the trade (MFE/MAE), non-negative: how far it went in the
   *  trade's favour / against it, as % of entry and in R (multiples of |entry - stop|).
   *  Recorded when a trade closes (null if never recorded); live for an open position. */
  mfe_pct?: number | null;
  mae_pct?: number | null;
  mfe_r?: number | null;
  mae_r?: number | null;
  /** How the exit was placed in time: 'daily' | 'hourly' | 'daily_ambiguous_stop_first' |
   *  'hourly_ambiguous_stop_first'; null for a manual close or a row from before this was recorded. */
  exit_resolution?: string | null;
  /** Whether the rest of the entry day was checked hour by hour: 'hourly' | 'daily_only'; null = not yet. */
  entry_day_check?: string | null;
  /** The AI-written lesson for a closed trade (2-4 sentences). Null until one is written, and
   *  also null when the AI could not write one: nothing templated stands in (see lesson_error). */
  lesson_text?: string | null;
  lesson_provider?: string | null;
  lesson_model?: string | null;
  /** When the lesson was written, or last tried. */
  lesson_at?: string | null;
  /** Why the last attempt failed (short); an earlier lesson, if any, is kept. */
  lesson_error?: string | null;
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
  /** Best/worst price during closed trades (MFE/MAE), averaged in R. */
  excursions: ExcursionStats;
}

export interface ExcursionGroup {
  n: number;
  avg_mfe_r: number | null;
  avg_mae_r: number | null;
}

export interface ExcursionStats {
  closed_trades: number;
  /** Closed trades that have the figures: the sample for everything below. */
  measured: number;
  winners: ExcursionGroup;
  losers: ExcursionGroup;
  /** Mean realized R / MFE R over winners (0-1): how much of the best price reached was banked. */
  exit_efficiency: number | null;
  exit_efficiency_n: number;
  /** Losers that had been at least 1R in profit first. */
  losers_reached_1r: number;
  /** Winners that came within 0.2R of the stop (MAE of 0.8R or more). */
  winners_near_stop: number;
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
  /** Decision-tier models (the AI overlay's verdict). "" = use the provider's routine model above. */
  claude_cli_decision_model: string;
  openrouter_decision_model: string;
  orcarouter_decision_model: string;
  openai_decision_model: string;
  gemini_decision_model: string;
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
  watchers_enabled: boolean;
  watchers_action: WatchersAction;
  watchers_poll_minutes: number;
  max_concurrent_positions: number;
  /** Trading days before a stalled position is closed at the close; 0 = no limit. */
  max_holding_days: number;
  ai_trading_overlay_enabled: boolean;
  ai_overlay_scores_confidence: boolean;
  ai_overlay_objection_action: AiOverlayObjectionAction;
  min_confidence_for_trade: number;
  research_mode: ResearchMode;
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
  claude_cli_decision_model?: string;
  openrouter_decision_model?: string;
  orcarouter_decision_model?: string;
  openai_decision_model?: string;
  gemini_decision_model?: string;
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
  watchers_enabled?: boolean;
  watchers_action?: WatchersAction;
  watchers_poll_minutes?: number;
  max_concurrent_positions?: number;
  max_holding_days?: number;
  ai_trading_overlay_enabled?: boolean;
  ai_overlay_scores_confidence?: boolean;
  ai_overlay_objection_action?: AiOverlayObjectionAction;
  min_confidence_for_trade?: number;
  research_mode?: ResearchMode;
}

export interface TestConnectionResponse {
  ok: boolean;
  message: string;
}

/** Unsaved Settings-form values a test button sends so it tests what is typed, not what is saved.
 *  Nothing here is persisted; a missing field (or a blank secret) means "use the saved value". */
export interface TestConnectionOverrides {
  llm_provider?: string;
  claude_cli_model?: string;
  claude_cli_decision_model?: string;
  openrouter_api_key?: string;
  openrouter_model?: string;
  openrouter_decision_model?: string;
  orcarouter_api_key?: string;
  orcarouter_model?: string;
  orcarouter_decision_model?: string;
  openai_api_key?: string;
  openai_model?: string;
  openai_decision_model?: string;
  gemini_api_key?: string;
  gemini_model?: string;
  gemini_decision_model?: string;
  finnhub_api_key?: string;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
}

export type TestConnectionArg =
  | 'llm'
  | 'finnhub'
  | 'telegram'
  | { target: 'llm' | 'finnhub' | 'telegram'; tier?: 'routine' | 'decision'; overrides?: TestConnectionOverrides };

/** What the AI Trading Overlay's objection actually does. One choice, not a
 *  set of flags: "cancel" and "hold" fire on the same trigger and cancel
 *  always wins, so they can never both be in effect. */
export type AiOverlayObjectionAction = 'cancel' | 'hold' | 'none';

/** Whether AI calls made for research (never the overlay or the narration) may search the web. */
export type ResearchMode = 'our_data_only' | 'allow_web_search';

export interface SettingsStatus {
  ai_online: boolean;
  ai_provider: string;
  /** Model the provider is pinned to ("" when it has no pin or the pin is blank). */
  ai_model: string;
  /** Model that answers the AI overlay's verdict, only when it differs from ai_model ("" otherwise). */
  ai_decision_model: string;
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

// --- Confidence calibration (GET /api/portfolio/calibration) ---

export interface CalibrationBand {
  label: string;
  min_points: number;
  max_points: number;
  n: number;
  wins: number;
  /** Percent, 0-100. Null for an empty band. */
  win_rate: number | null;
  /** Wilson 95% interval on the win rate, percent. */
  win_rate_low: number | null;
  win_rate_high: number | null;
  avg_r: number | null;
  /** Bootstrap 95% interval on the average R; null below two trades. */
  avg_r_low: number | null;
  avg_r_high: number | null;
  total_pnl: number;
  small_sample: boolean;
}

export type CalibrationVerdict = 'not_enough_data' | 'no_variation' | 'no_clear_relationship' | 'positive' | 'negative';

/** Spearman rank correlation (information coefficient) with its sample and interval. */
export interface CalibrationIc {
  key: string;
  label: string;
  n: number;
  ic: number | null;
  /** Permutation test, two-sided. */
  p_value: number | null;
  /** Set for score components only: p-value adjusted for testing several at once. */
  p_value_adjusted: number | null;
  ci_low: number | null;
  ci_high: number | null;
  verdict: CalibrationVerdict;
  note: string;
  /** Score components only: trades where the component was not zero. */
  n_nonzero: number | null;
}

export interface CalibrationReport {
  closed_trades: number;
  analyzed_trades: number;
  excluded: { no_linked_plan: number; missing_r: number; outside_bands: number; total: number };
  points_max: number;
  min_trades_for_reading: number;
  min_trades_per_band: number;
  reliable: boolean;
  headline: string;
  bands: CalibrationBand[];
  overall: CalibrationBand | null;
  ic: CalibrationIc;
  ic_by_direction: CalibrationIc[];
  components: CalibrationIc[];
  components_tested: number;
  notes: string[];
}

// --- Missed trades (GET /api/missed-trades, POST /api/missed-trades/refresh) ---

export type MissedTradeCategoryKey =
  | 'ai_veto'
  | 'low_confidence'
  | 'held_by_ai'
  | 'not_executed'
  | 'neutral_trend'
  | 'unclassified';

/** One kind of missed trade: counts, then the result of the ones that have one. */
export interface MissedTradeCategory {
  key: MissedTradeCategoryKey;
  label: string;
  /** Plans of this kind. */
  n: number;
  /** Hypothetical trades that reached a stop, TP1 or the time limit: the only ones the statistics use. */
  resolved: number;
  /** Still open: marked at the latest close, not final. */
  open: number;
  /** Need a refresh (or their prices could not be loaded). */
  awaiting: number;
  /** Counted but never simulated: no trend, a duplicate of a held position, a plan waiting for its redo. */
  not_simulated: number;
  wins: number;
  /** Percent of resolved, with a Wilson 95% interval. */
  win_rate: number | null;
  win_rate_low: number | null;
  win_rate_high: number | null;
  avg_r: number | null;
  /** Bootstrap 95% interval on the average R; null below two resolved trades. */
  avg_r_low: number | null;
  avg_r_high: number | null;
  total_r: number;
  /** Mean mark of the open ones. Not a result. */
  open_avg_r: number | null;
  small_sample: boolean;
  details: { detail: string; label: string; n: number }[];
}

export interface MissedTakenStats {
  n: number;
  wins: number;
  win_rate: number | null;
  win_rate_low: number | null;
  win_rate_high: number | null;
  avg_r: number | null;
  avg_r_low: number | null;
  avg_r_high: number | null;
  total_r: number;
  small_sample: boolean;
}

export type MissedQuestionVerdict =
  | 'too_early'
  | 'no_data'
  | 'unclear'
  | 'saved'
  | 'cost'
  | 'bar_justified'
  | 'bar_costs';

export interface MissedQuestion {
  key: 'ai_veto' | 'confidence_bar';
  question: string;
  verdict: MissedQuestionVerdict;
  answer: string;
}

export type MissedTradeState = 'resolved' | 'open' | 'awaiting' | 'not_simulated';

export interface MissedTradeItem {
  plan_id: number;
  symbol: string;
  created_at: string;
  category: MissedTradeCategoryKey;
  category_label: string;
  detail: string | null;
  detail_label: string | null;
  reason: string | null;
  state: MissedTradeState;
  direction: string | null;
  confidence_score: number;
  entry: number | null;
  stop: number | null;
  tp1: number | null;
  /** Final when state is "resolved", a mark when "open". */
  r_multiple: number | null;
  exit_reason: string | null;
  exit_date: string | null;
  /** "plan" (its own stored levels) or "reconstructed" (rebuilt with the live rules). */
  trade_source: string | null;
  note: string | null;
}

export interface MissedTradeReport {
  plans_considered: number;
  awaiting_refresh: number;
  min_trades_for_reading: number;
  last_computed_at: string | null;
  categories: MissedTradeCategory[];
  taken: MissedTakenStats;
  questions: MissedQuestion[];
  trades: MissedTradeItem[];
  trades_listed: number;
  caveats: string[];
}

export interface MissedTradeRefresh {
  considered: number;
  computed: number;
  resolved: number;
  still_open: number;
  no_data: number;
  not_simulatable: number;
  skipped_resolved: number;
  remaining: number;
  busy: boolean;
  failed_symbols: string[];
}

/** GET /api/strategy/versions — which rules and settings produced the plans. */
export interface StrategyVersion {
  id: number;
  number: number;
  fingerprint: string;
  created_at: string;
  label: string | null;
  /** {settings, rules} plus overlay while the AI Trading Overlay is on. */
  settings_snapshot: Record<string, Record<string, unknown>>;
  /** What changed from the previous version, one plain line each. */
  changes: string[];
  plans: number;
  no_trades: number;
  positions_opened: number;
  closed_trades: number;
}

export interface StrategyHistory {
  /** Newest first. */
  versions: StrategyVersion[];
  /** Plans made before versioning existed. */
  unversioned_plans: number;
  /** The version the current settings map to; null until they produce a plan. */
  current_number: number | null;
}

/** The on-disk half of the provider cache; mirrors schemas/cache_schemas.py. */
export interface PersistentCacheStatus {
  file_name: string;
  entries: number;
  /** Rows whose time-to-live hasn't run out; the rest are kept as last-known-good. */
  fresh_entries: number;
  payload_bytes: number;
  file_bytes: number;
  max_bytes: number;
  oldest_stored_at: string | null;
  newest_stored_at: string | null;
  pending_writes: number;
  errors: number;
  skipped_unencodable: number;
}

export interface HistoryStoreStatus {
  symbols: number;
  bars: number;
  first_date: string | null;
  last_date: string | null;
  file_bytes: number;
  /** Symbols whose newest stored bar is oldest. */
  stalest: { symbol: string; last_date: string }[];
}

export interface CacheStatus {
  memory_entries: number;
  memory_stale_entries: number;
  /** Since the process started: memory_hits, disk_hits, coalesced_hits, misses,
   *  stale_served, stale_served_from_disk, fetch_failures. */
  counters: Record<string, number>;
  hit_rate: number | null;
  /** null when persistence is off (PERSIST_CACHE_DB=false). */
  persistent: PersistentCacheStatus | null;
  history: HistoryStoreStatus;
}

export interface CacheClearResponse {
  cleared: boolean;
  message: string;
}

export type WatchlistLayer = 'dev_filter' | 'custom' | 'bundled';

export interface WatchlistEntry {
  symbol: string;
  name: string;
  sector: string;
  /** false when the sector is "Unknown": the sector cap has no opinion on it. */
  sector_known: boolean;
}

export interface WatchlistResponse {
  active_layer: WatchlistLayer;
  /** What scans and screens actually use, in scan order. */
  entries: WatchlistEntry[];
  /** What the editor works on: the saved list if there is one, else the bundled list. */
  editable_entries: WatchlistEntry[];
  has_custom: boolean;
  custom_updated_at: string | null;
  /** Set when a saved file exists but could not be read. */
  custom_error: string | null;
  dev_filter: string[] | null;
  bundled_size: number;
  min_symbols: number;
  max_symbols: number;
  scan_universe_size: number;
  scanned_count: number;
}

export interface WatchlistSymbolCheck {
  symbol: string;
  valid: boolean;
  name: string | null;
  sector: string | null;
  sector_known: boolean;
  in_catalogue: boolean;
  message: string | null;
}

// ---- Backtest Lab (GET /api/backtests...) ----

export interface BacktestProgress {
  days_done: number;
  days_total: number;
  current_date: string | null;
  /** "main" while the real run is going, "baseline" during the random runs, null when idle. */
  phase: 'main' | 'baseline' | null;
  baseline_seeds_done: number | null;
  baseline_seeds_total: number | null;
}

export interface BacktestRun {
  id: number;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  cancel_requested: boolean;
  progress: BacktestProgress;
  strategy_fingerprint: string | null;
  params: {
    symbols?: string[];
    start?: string;
    end?: string;
    decision_every_n_days?: number;
    run_baseline?: boolean;
    baseline_runs?: number;
    include_fundamentals?: boolean;
    include_insiders?: boolean;
    include_earnings?: boolean;
    overrides?: Record<string, number>;
    effective_settings?: Record<string, number | string | boolean>;
    [key: string]: unknown;
  };
  coverage: BacktestCoverage | null;
  summary: Record<string, number | string | boolean | null | Record<string, unknown>> | null;
}

export interface BacktestCoverage {
  summary: string;
  profile?: string;
  included?: { fundamentals: boolean; insiders: boolean; earnings: boolean };
  live_points_max: number;
  achievable_points: number;
  achievable_max_confidence_pct: number;
  active_parts: { part: string; label: string; points_max: number; note: string | null }[];
  inactive_parts: { part: string; label: string; live_points_max: number; reason: string }[];
  min_confidence_for_trade: number;
  bar_points_needed: number | null;
  bar_reachable: boolean;
}

export interface BacktestTrade {
  id: number;
  symbol: string;
  direction: string;
  status: string;
  entry_date: string;
  entry_price: number;
  stop_loss: number;
  tp1: number;
  shares: number;
  exit_date: string | null;
  exit_price: number | null;
  close_reason: string | null;
  realized_pnl: number | null;
  realized_r: number | null;
  holding_days: number | null;
  confidence_points: number | null;
  confidence_points_max: number | null;
}

export interface BacktestEquityPoint {
  day: string;
  equity: number;
  cash: number;
  open_positions: number;
}

export interface BacktestStartRequest {
  symbols: string[];
  start: string;
  end: string;
  decision_every_n_days: number;
  overrides: Record<string, number>;
  run_baseline: boolean;
  baseline_runs: number;
  include_fundamentals?: boolean;
  include_insiders?: boolean;
  include_earnings?: boolean;
}

export interface BacktestPeriodReturn {
  year: number;
  month?: number;
  return_pct: number;
  days: number;
  partial: boolean;
}

export interface BacktestMetrics {
  conventions: Record<string, string>;
  period: { first_day: string | null; last_day: string | null; calendar_days: number; trading_days: number; years: number | null; annualised: boolean };
  returns: {
    starting_equity: number;
    final_equity: number;
    total_return_pct: number | null;
    cagr_pct: number | null;
    volatility_pct: number | null;
    sharpe: number | null;
    sortino: number | null;
    calmar: number | null;
    best_day_pct: number | null;
    worst_day_pct: number | null;
  };
  drawdown: {
    max_drawdown_pct: number;
    peak_date: string | null;
    trough_date: string | null;
    recovery_date: string | null;
    max_drawdown_days: number;
    longest_underwater_days: number;
    underwater_now: boolean;
  };
  trades: {
    closed_trades: number;
    open_at_end: number;
    wins: number;
    losses: number;
    win_rate_pct: number | null;
    win_rate_low_pct: number | null;
    win_rate_high_pct: number | null;
    average_r: number | null;
    average_r_low: number | null;
    average_r_high: number | null;
    expectancy_usd: number | null;
    total_pnl: number;
    profit_factor: number | null;
    average_win: number | null;
    average_loss: number | null;
    payoff_ratio: number | null;
    trades_per_year: number | null;
    average_holding_days: number | null;
    longest_losing_streak: number;
    total_fees: number;
  };
  exposure: { days_with_a_position: number; exposure_pct: number | null; average_open_positions: number | null };
  yearly_returns: BacktestPeriodReturn[];
  monthly_returns: BacktestPeriodReturn[];
  exit_reasons: { reason: string; count: number; share_pct: number; average_r: number | null; total_pnl: number }[];
  by_direction: { direction: string; trades: number; share_pct: number; win_rate_pct: number | null; average_r: number | null; total_pnl: number }[];
  drawdown_series: { day: string; drawdown_pct: number }[];
  run_status: string;
  partial: boolean;
}

export interface BacktestReferenceLine {
  available: boolean;
  reason?: string;
  label?: string;
  symbol?: string;
  basis?: string;
  survivor_biased?: boolean;
  symbols_used?: string[];
  symbols_excluded?: { symbol: string; reason: string }[];
  equity?: { day: string; equity: number }[];
  total_return_pct?: number | null;
  sharpe?: number | null;
  max_drawdown_pct?: number | null;
}

export interface BacktestBenchmarks {
  available: boolean;
  reason?: string;
  costs?: string;
  spy: BacktestReferenceLine;
  comparison: {
    strategy_return_pct: number;
    spy_return_pct: number | null;
    excess_return_pct: number | null;
    strategy_sharpe: number | null;
    spy_sharpe: number | null;
    outperform_days_pct: number | null;
    days_compared: number;
    beta: number | null;
    alpha_annual_pct: number | null;
    correlation: number | null;
    n: number;
    beta_t?: number | null;
    alpha_t?: number | null;
    r_squared?: number | null;
    [key: string]: unknown;
  } | null;
  equal_weight: BacktestReferenceLine;
}

export interface BacktestPlacement {
  real: number;
  n: number;
  percentile: number;
  chance_random_matches: number;
  random_mean: number;
  random_median: number;
  random_p5: number;
  random_p95: number;
  random_min: number;
  random_max: number;
}

export interface BacktestBaseline {
  available: boolean;
  reason?: string;
  status?: string;
  note?: string | null;
  requested_runs?: number;
  entry_probability?: number | null;
  seeds?: { seed: number; total_return_pct: number | null; sharpe: number | null; average_r: number | null; win_rate_pct: number | null; trade_count: number; max_drawdown_pct: number }[];
  real?: { total_return_pct: number | null; sharpe: number | null; average_r: number | null } | null;
  placement?: Record<string, BacktestPlacement | null>;
  enough_seeds?: boolean;
  caveat?: string;
  notes?: string[];
}

export interface BacktestScorecard {
  note: string;
  criteria: { min_trades: number; max_drawdown_pct: number; year_share: number; baseline_percentile: number };
  checks: { key: string; label: string; criterion: string; status: 'pass' | 'fail' | 'insufficient_data'; actual: string; detail?: string | null }[];
  counts: { pass: number; fail: number; insufficient_data: number; total: number };
  banners: { key: string; level: 'warning' | 'info'; text: string }[];
}

export interface BacktestHistoryCoverage {
  benchmarks: BacktestSymbolCoverage[];
  symbols: BacktestSymbolCoverage[];
  missing: string[];
  benchmarks_missing: string[];
  latest_end_date: string | null;
  preload_command: string | null;
  warmup_note: string;
}

export interface BacktestSymbolCoverage {
  symbol: string;
  stored: boolean;
  first_date: string | null;
  last_date: string | null;
  bars: number;
}

// ---- Watchers ----------------------------------------------------------------

export type WatchersAction = 'record' | 'alert' | 'alert_and_reevaluate';

export interface WatcherEvent {
  id: number;
  watcher: string;
  symbol: string | null;
  kind: string;
  headline: string;
  severity: string;
  known_at: string;
  source_ref: string | null;
  details: Record<string, unknown>;
  /** Why the event did nothing beyond being recorded (cooldown, daily cap); null if it fired. */
  suppressed_reason: string | null;
}

export interface WatcherStatus {
  name: string;
  description: string;
  enabled: boolean;
  poll_interval_seconds: number;
  cooldown_seconds: number;
  daily_fire_cap: number;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  fires_today: number;
  recent_events: WatcherEvent[];
}

export interface WatchersResponse {
  master_enabled: boolean;
  action: string;
  poll_minutes: number;
  watchers: WatcherStatus[];
}

export interface WatcherRunResponse {
  watcher: string;
  ran: boolean;
  skipped_reason: string | null;
  error: string | null;
  new_events: number;
  duplicates: number;
  fired: number;
  suppressed: number;
  actions: string[];
}

export type SmartMoneySide = 'buys' | 'sells' | 'all';

export interface SmartMoneyInsiderTrade {
  symbol: string;
  insider: string | null;
  role_tags: string[];
  officer_title: string | null;
  code: string | null;
  side: 'buy' | 'sell' | 'other';
  shares: number | null;
  price: number | null;
  value: number | null;
  transaction_date: string | null;
  known_at: string;
  filed_after_days: number | null;
  is_10b5_1: boolean;
  filing_url: string | null;
}

export interface SmartMoneyInsiderTrades {
  days: number;
  side: SmartMoneySide;
  symbol: string | null;
  min_value: number;
  total: number;
  shown: number;
  buy_value: number;
  sell_value: number;
  buy_count: number;
  sell_count: number;
  trades: SmartMoneyInsiderTrade[];
}

export interface SmartMoneyInsiderCluster {
  symbol: string;
  start_date: string;
  end_date: string;
  insider_count: number;
  trade_count: number;
  total_value: number;
  unpriced_trades: number;
  role_tags: string[];
  insiders: string[];
  any_10b5_1: boolean;
  visible_from: string;
}

export interface SmartMoneyInsiderClusters {
  days: number;
  symbols_checked: number;
  clusters: SmartMoneyInsiderCluster[];
}

export interface SmartMoneyInsiderSummary {
  symbol: string;
  window_days: number;
  data_loaded: boolean;
  buy_count: number;
  sell_count: number;
  buy_value: number;
  sell_value: number;
  net_value: number;
  cluster_count: number;
  newest_filing: string | null;
  would_score_long: number;
  would_score_short: number;
  score_reasons: string[];
}

export interface SmartMoneyStatus {
  has_data: boolean;
  trade_rows: number;
  symbols: number;
  oldest_filing: string | null;
  newest_filing: string | null;
  last_stored_at: string | null;
  symbol_list: string[];
  sec_contact_is_placeholder: boolean;
  ingest_command: string;
}

export interface SmartMoneyRefreshResponse {
  symbols_requested: number;
  symbols_processed: number;
  symbols_remaining: number;
  filings_seen: number;
  filings_ingested: number;
  rows_created: number;
  unknown_symbols: string[];
  errors: string[];
}

export interface ValidationKnob {
  name: string;
  label: string;
  kind: 'int' | 'float';
  low: number;
  high: number;
  help: string;
  live_value: number;
}

export interface ValidationOptions {
  knobs: ValidationKnob[];
  min_folds: number;
  max_folds: number;
  default_folds: number;
  modes: string[];
  default_mode: string;
  default_train_ratio: number;
  min_train_ratio: number;
  max_train_ratio: number;
  default_embargo_days: number;
  max_embargo_days: number;
  min_window_days: number;
  max_values_per_knob: number;
  max_variants: number;
  max_total_runs: number;
}

export interface ValidationRequest {
  symbols: string[];
  start: string;
  end: string;
  folds: number;
  mode: string;
  train_ratio: number;
  embargo_days: number;
  grid: Record<string, number[]>;
  overrides: Record<string, number>;
  decision_every_n_days: number;
}

export interface ValidationProgress {
  runs_done: number;
  runs_total: number;
  label: string | null;
  days_done: number;
  days_total: number;
}

export interface ValidationWindowStats {
  total_return_pct: number | null;
  sharpe: number | null;
  average_r: number | null;
  win_rate_pct: number | null;
  trade_count: number;
  max_drawdown_pct: number | null;
  trading_days: number;
  open_at_end?: number;
}

export interface ValidationFold {
  index: number;
  train: { start: string; end: string; days: number };
  test: { start: string; end: string; days: number };
  embargo_days: number;
  trials: { variant_index: number; params: Record<string, number>; is: ValidationWindowStats }[];
  selected: {
    variant_index: number;
    params: Record<string, number>;
    basis: string;
    in_sample: ValidationWindowStats;
    out_of_sample: ValidationWindowStats;
  };
}

export interface ValidationDsr {
  available: boolean;
  reason?: string;
  n_trials: number;
  n_variants: number;
  n_observations: number;
  confidence_level: number;
  sharpe_annualised?: number;
  skewness?: number;
  kurtosis?: number;
  expected_max_sharpe_annualised?: number;
  trial_sharpe_std_annualised?: number;
  trial_spread_known?: boolean;
  psr?: number;
  dsr?: number;
  min_track_record_years?: number | null;
  reading?: string[];
}

export interface ValidationResult {
  status: string;
  mode: string;
  folds_requested: number;
  embargo_days: number;
  selection_basis: string;
  variants: { index: number; params: Record<string, number> }[];
  n_variants: number;
  n_trials: number;
  runs_total: number;
  period: { first_day: string; last_day: string; trading_days: number };
  folds: ValidationFold[];
  aggregate: (ValidationWindowStats & {
    folds_positive: number;
    folds: number;
    in_sample_sharpe_mean: number | null;
    out_of_sample_sharpe_mean: number | null;
    calendar_days: number;
  }) | null;
  dsr: ValidationDsr | null;
  oos_equity: { day: string; equity: number; open_positions: number }[];
}

export interface ValidationScorecard {
  note: string;
  checks: { key: string; label: string; criterion: string; status: 'pass' | 'fail' | 'insufficient_data'; actual: string; detail?: string | null }[];
  counts: { pass: number; fail: number; insufficient_data: number; total: number };
  banners: { key: string; level: 'warning' | 'info'; text: string }[];
}

export interface BacktestValidation {
  id: number;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  cancel_requested: boolean;
  progress: ValidationProgress;
  strategy_fingerprint: string | null;
  params: Record<string, unknown>;
  result?: ValidationResult | null;
  scorecard?: ValidationScorecard | null;
}

// ---- Replay ("What if?": a settings change re-decided on past plans, POST /api/replay) ----

export interface ReplayOverrides {
  min_confidence_for_trade?: number;
  ai_overlay_objection_action?: 'cancel' | 'hold' | 'none';
  ai_overlay_scores_confidence?: boolean;
  allowed_directions?: 'both' | 'long' | 'short';
}

export interface ReplayStats {
  taken: number;
  resolved: number;
  open: number;
  no_result: number;
  wins: number;
  win_rate: number | null;
  win_rate_low: number | null;
  win_rate_high: number | null;
  avg_r: number | null;
  avg_r_low: number | null;
  avg_r_high: number | null;
  total_r: number;
  small_sample: boolean;
}

export interface ReplayFlip {
  plan_id: number;
  symbol: string;
  created_at: string;
  direction: string | null;
  flip: 'now_taken' | 'now_skipped';
  why: string;
  confidence_points: number | null;
  points_after: number | null;
  result_state: 'resolved' | 'open' | 'none';
  result_r: number | null;
  strategy_version: number | null;
}

export interface ReplayResult {
  overrides: Record<string, unknown>;
  decisions: number;
  truncated: boolean;
  before: ReplayStats;
  after: ReplayStats;
  flipped_count: number;
  now_taken: number;
  now_skipped: number;
  flips_without_result: number;
  flips: ReplayFlip[];
  fixed_count: number;
  baseline_disagrees: number;
  versions: { strategy_version: number | null; decisions: number }[];
  replayable: Record<string, string>;
  caveats: string[];
}

export type TerminalWindow = '1d' | '5d' | '1m';
export type VixRegime = 'elevated' | 'calm';

export interface HeatmapTile {
  symbol: string;
  name: string;
  change_pct: number;
  market_cap: number | null;
  weight: number;
}

export interface HeatmapSector {
  sector: string;
  avg_change_pct: number;
  weight: number;
  tiles: HeatmapTile[];
}

export interface SectorEtfTile {
  symbol: string;
  sector: string;
  change_pct: number | null;
}

export interface HeatmapResponse {
  window: TerminalWindow;
  as_of: string;
  sampled: number;
  universe_size: number;
  requested_limit: number;
  weighting: 'market_cap' | 'equal';
  sectors: HeatmapSector[];
  sector_etfs: SectorEtfTile[];
  missing: string[];
}

export interface MacroTile {
  id: string;
  label: string;
  group: 'index' | 'volatility' | 'currency' | 'yield';
  symbol: string;
  unit: 'index' | 'percent';
  available: boolean;
  value: number | null;
  change: number | null;
  change_pct: number | null;
  as_of: string | null;
  source: string | null;
  sparkline: number[];
}

export interface YieldPoint {
  label: string;
  months: number;
  value: number | null;
}

export interface YieldCurve {
  points: YieldPoint[];
  spread_10y_3m: number | null;
  spread_10y_2y: number | null;
  inverted: boolean | null;
  note: string | null;
}

export interface MacroResponse {
  as_of: string;
  tiles: MacroTile[];
  yield_curve: YieldCurve;
  vix_value: number | null;
  vix_regime: VixRegime | null;
  vix_threshold: number;
}

export interface TerminalMover {
  symbol: string;
  name: string;
  change_pct: number;
  price: number;
}

export interface TerminalSectorMove {
  sector: string;
  avg_change_pct: number;
  count: number;
}

export interface BreadthStats {
  advancers: number;
  decliners: number;
  unchanged: number;
  new_highs: number;
  new_lows: number;
  high_low_judged: number;
  total: number;
}

export interface RecapResponse {
  as_of: string;
  sampled: number;
  universe_size: number;
  breadth: BreadthStats;
  top_gainers: TerminalMover[];
  top_losers: TerminalMover[];
  sector_leaders: TerminalSectorMove[];
  sector_laggards: TerminalSectorMove[];
  etf_leaders: SectorEtfTile[];
  etf_laggards: SectorEtfTile[];
  vix_value: number | null;
  vix_change_pct: number | null;
  vix_regime: VixRegime | null;
  summary: string;
  ai_paragraph: string | null;
  ai_provider: string | null;
  missing: string[];
}

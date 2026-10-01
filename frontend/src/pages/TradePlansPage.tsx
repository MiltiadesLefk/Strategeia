import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAnalysis, useGenerateTradePlan, useMarketSession, useOpenPosition, useTradePlans } from '../api/hooks';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { DirectionBadge, TradePlanStatusBadge } from '../components/Badge';
import { PipelineSteps } from '../components/PipelineSteps';
import { MissedTradesCard } from '../components/MissedTradesCard';
import { StrategyHistoryCard } from '../components/StrategyHistoryCard';
import { TickerLink } from '../components/TickerLink';
import { RatioGauge } from '../components/RatioGauge';
import { IconBadge } from '../components/IconBadge';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber } from '../components/common';
import { isAlwaysOpenSymbol, marketStateLabel, timeUntilNextTransition } from '../lib/marketHours';
import { useNow } from '../lib/useNow';
import type { ApiError } from '../api/client';
import type { MarketSession, TradePlan } from '../api/types';

const STATUS_FILTERS = [
  { value: 'active', label: 'Pending & Executed' },
  { value: 'all', label: 'All statuses' },
  { value: 'pending', label: 'Pending only' },
  { value: 'executed', label: 'Executed only' },
  { value: 'no_trade', label: 'No Trade only' },
  { value: 'discarded', label: 'Discarded only' },
];

function AiOpinionBlock({ plan }: { plan: TradePlan }) {
  if (!plan.ai_opinion_text) return null;
  const stanceBadgeClass =
    plan.ai_opinion_stance === 'bullish' ? 'badge-green' : plan.ai_opinion_stance === 'bearish' ? 'badge-red' : 'badge-neutral';
  return (
    <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <IconBadge variant="info" size={26} />
        <span style={{ fontWeight: 700, fontSize: 13 }}>AI Second Opinion</span>
        {plan.ai_decision_model && (
          <span className="text-muted" style={{ fontSize: 12 }} title="The model that gave this verdict (Settings → AI Narrative Provider → Decision model).">
            AI overlay · {plan.ai_decision_model}
          </span>
        )}
        {plan.ai_opinion_stance && <span className={`badge ${stanceBadgeClass}`}>{plan.ai_opinion_stance}</span>}
        {plan.ai_trade_verdict && (
          <span
            className={`badge ${plan.ai_trade_verdict === 'pass' ? 'badge-red' : 'badge-green'}`}
            title={
              plan.ai_trade_verdict === 'pass'
                ? "The AI would not take this trade. This — not the stance beside it — is the answer acted on."
                : 'The AI would take this trade, even if its directional read differs.'
            }
          >
            {plan.ai_trade_verdict === 'pass' ? 'would not take' : 'would take'}
          </span>
        )}
        {plan.ai_opinion_score != null && (
          <span className="text-muted tabular-nums" style={{ fontSize: 12 }}>
            {plan.ai_opinion_score}%
          </span>
        )}
      </div>
      {plan.ai_opinion_parse === 'failed' && (
        <div className="badge badge-amber" style={{ display: 'block', marginBottom: 8, whiteSpace: 'normal' }} role="status">
          AI overlay answered but its reply could not be read. No stance or verdict was recorded, so it had no say in
          this trade. The raw reply is shown below.
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
        {plan.ai_opinion_text}
      </div>
      {plan.ai_opinion_parse === 'lenient' && (
        <div className="text-muted" style={{ fontSize: 11, marginTop: 4 }}>
          The reply was not in the exact format asked for; the fields above were recovered from it.
        </div>
      )}
      {plan.ai_grounding_warnings && (
        <div
          style={{ marginTop: 8, fontSize: 12, lineHeight: 1.5 }}
          title="Figures the AI quoted that were not in the data it was given. Shown for your information only; they never change the verdict or the score."
        >
          <span style={{ fontWeight: 700, color: 'var(--amber)' }}>Check these figures:</span>{' '}
          <span className="text-muted">{plan.ai_grounding_warnings.split('\n').filter(Boolean).join('; ')}</span>
        </div>
      )}
      {plan.ai_news_assessment && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 2 }}>AI's read of the news</div>
          <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            {plan.ai_news_assessment}
          </div>
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 11, marginTop: 6, opacity: 0.7 }}>
        Independent read from the AI provider (Settings → AI Trading Overlay). It cannot pick the direction, entry,
        stop or size — but a flat disagreement costs confidence and can stop the trade.
      </div>
    </div>
  );
}

/** The viewer's own clock for a UTC instant: "Mon 16:45". The notes quote New
 *  York time (the rule is defined there); this says what that means locally. */
function formatLocalTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

/**
 * Why Execute is unavailable for a pending plan right now, or null. Mirrors
 * the two refusals POST /api/portfolio/positions makes, so the button is
 * disabled with the reason rather than failing on click:
 * - a plan made while the market was closed waits for its redo at the next
 *   open, and only the fresh plan may execute (D10 = C) — even in the minutes
 *   between the bell and the redo;
 * - an equity can't be filled while the US market is closed: the only price
 *   is the last session's close. Crypto never closes.
 */
function executeBlockedReason(plan: TradePlan, session: MarketSession | undefined, now: Date): string | null {
  if (plan.redo_at) {
    return (
      'Made while the market was closed: it will be redone from fresh data at the next open ' +
      `(${formatLocalTime(plan.redo_at)} your time), and only that fresh plan can be executed.`
    );
  }
  if (isAlwaysOpenSymbol(plan.symbol) || !session || session.is_open) return null;
  return (
    `${marketStateLabel(session)}: the only price now is the last session's close, which nobody can trade at. ` +
    `Execute is available once the US market opens (${timeUntilNextTransition(session, now)}).`
  );
}

/** "v3": the strategy version the plan was made under (see Strategy history). */
function StrategyVersionChip({ version }: { version: number | null | undefined }) {
  if (version == null) return null;
  return (
    <span
      className="badge badge-neutral"
      title={`Made under strategy version ${version}: the rules and settings in force when this plan was generated.`}
    >
      v{version}
    </span>
  );
}

function TradePlanCard({ plan }: { plan: TradePlan }) {
  const { mutate: open, isPending, isSuccess, error: openError } = useOpenPosition();
  const { data: analysis } = useAnalysis(plan.direction ? plan.symbol : null);
  const { data: session } = useMarketSession();
  const now = useNow();

  if (plan.direction === null) {
    return (
      <div className="card" style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <IconBadge variant="info" size={44} />
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <TickerLink symbol={plan.symbol} iconSize={24} fontWeight={700} style={{ fontSize: 16 }} />
            <span className="badge badge-neutral">No Trade Plan</span>
            <StrategyVersionChip version={plan.strategy_version} />
          </div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{plan.reason ?? 'No trade plan generated'}</div>
          <div className="text-muted" style={{ fontSize: 13, maxWidth: 480 }}>
            This isn't an error — {plan.symbol} was fully evaluated (price, volume, indicators, fundamentals, news,
            earnings) and just didn't clear the bar for a trade plan this time. Quality over quantity: pick a
            different symbol from the dropdown above, or check back after the next scan.
          </div>
          {plan.signal_reasons && (
            <div className="text-muted" style={{ fontSize: 12, marginTop: 8, marginBottom: plan.ai_opinion_text ? 12 : 0 }}>
              Confidence {plan.confidence_score}% — {plan.signal_reasons}
            </div>
          )}
          <AiOpinionBlock plan={plan} />
        </div>
      </div>
    );
  }

  const levels: PriceLevel[] =
    plan.entry != null && plan.stop != null && plan.tp1 != null && plan.tp2 != null
      ? [
          { price: plan.entry, color: '#2563eb', title: 'Entry' },
          { price: plan.stop, color: '#ef4444', title: 'SL' },
          { price: plan.tp1, color: '#10b981', title: 'TP1' },
          { price: plan.tp2, color: '#10b981', title: 'TP2' },
        ]
      : [];

  const executed = plan.status === 'executed' || isSuccess;
  const blockedReason = executeBlockedReason(plan, session, now);

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <TickerLink symbol={plan.symbol} iconSize={32} fontWeight={700} style={{ fontSize: 16 }} />
          <DirectionBadge direction={plan.direction} />
        </div>
        <span className="text-muted" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <StrategyVersionChip version={plan.strategy_version} />
          {plan.ai_provider && plan.ai_provider !== 'none' ? `Generated by ${plan.ai_provider}` : 'Rule-based plan'}
        </span>
      </div>

      <div className="split-row" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="grid stat-grid">
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Entry
              </div>
              <div className="tabular-nums">{formatMoney(plan.entry)}</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Stop
              </div>
              <div className="tabular-nums text-red">{formatMoney(plan.stop)}</div>
              {/* A stop's distance only means something relative to how much
                  the instrument moves in a day. Under ~1 ATR it sits inside
                  normal noise and gets taken out by nothing in particular —
                  worth seeing before you execute, not after. */}
              {plan.stop_atr_multiple != null && (
                <div
                  className="tabular-nums"
                  style={{ fontSize: 10, color: plan.stop_atr_multiple < 1 ? 'var(--amber)' : 'var(--text-muted)' }}
                  title={plan.atr != null ? `ATR14 ${formatMoney(plan.atr)}` : undefined}
                >
                  {formatNumber(plan.stop_atr_multiple, 1)}× ATR{plan.stop_atr_multiple < 1 ? ' — inside daily noise' : ''}
                </div>
              )}
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                TP1 / TP2
              </div>
              <div className="tabular-nums text-green">
                {formatMoney(plan.tp1)} / {formatMoney(plan.tp2)}
              </div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Position Size
              </div>
              <div className="tabular-nums">{plan.suggested_shares} shares</div>
              {plan.capped_by_cash && (
                <div style={{ fontSize: 10, color: 'var(--amber)' }}>capped by available cash</div>
              )}
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Account Risk
              </div>
              <div className="tabular-nums">{formatMoney(plan.account_risk_dollars)}</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Time Horizon
              </div>
              <div className="tabular-nums">{plan.time_horizon ?? '—'}</div>
            </div>
          </div>
          <RatioGauge ratio={plan.rr1 ?? 0} label="R:R Ratio" size={80} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {analysis && <CandlestickChart candles={analysis.candles} levels={levels} height={220} />}
          <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <IconBadge variant="info" size={26} />
              <span style={{ fontWeight: 700, fontSize: 13 }}>{plan.ai_provider && plan.ai_provider !== 'none' ? 'AI Take' : "Analyst's Take"}</span>
            </div>
            <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
              {plan.ai_take_text} <span style={{ opacity: 0.6 }}>({plan.ai_provider === 'none' ? 'rule-based' : plan.ai_provider})</span>
            </div>
          </div>
          {plan.signal_reasons && (
            <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14 }}>
              <div style={{ display: 'flex', gap: 12, marginBottom: 8, fontSize: 11, flexWrap: 'wrap' }}>
                <span className="text-muted">
                  Technical <span className="tabular-nums">{plan.technical_score ?? 0}</span>
                </span>
                <span className="text-muted">
                  Fundamentals{' '}
                  <span className={`tabular-nums ${(plan.fundamental_score ?? 0) > 0 ? 'text-green' : (plan.fundamental_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {(plan.fundamental_score ?? 0) > 0 ? '+' : ''}
                    {plan.fundamental_score ?? 0}
                  </span>
                </span>
                <span className="text-muted">
                  News{' '}
                  <span className={`tabular-nums ${(plan.news_score ?? 0) > 0 ? 'text-green' : (plan.news_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {(plan.news_score ?? 0) > 0 ? '+' : ''}
                    {plan.news_score ?? 0}
                  </span>
                </span>
                <span className="text-muted" title="Weekly-timeframe + broad market (SPY) agreement">
                  Confluence{' '}
                  <span
                    className={`tabular-nums ${(plan.market_confirmation_score ?? 0) > 0 ? 'text-green' : (plan.market_confirmation_score ?? 0) < 0 ? 'text-red' : ''}`}
                  >
                    {(plan.market_confirmation_score ?? 0) > 0 ? '+' : ''}
                    {plan.market_confirmation_score ?? 0}
                  </span>
                </span>
                <span className="text-muted" title="VIX regime — only ever a penalty when elevated (>=25), never a bonus">
                  VIX{' '}
                  <span className={`tabular-nums ${(plan.vix_regime_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {(plan.vix_regime_score ?? 0) > 0 ? '+' : ''}
                    {plan.vix_regime_score ?? 0}
                  </span>
                </span>
                <span className="text-muted" title="Options put/call volume skew agreement (no chain data for most crypto/thin names)">
                  Options{' '}
                  <span className={`tabular-nums ${(plan.options_score ?? 0) > 0 ? 'text-green' : (plan.options_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {(plan.options_score ?? 0) > 0 ? '+' : ''}
                    {plan.options_score ?? 0}
                  </span>
                </span>
                <span
                  className="text-muted"
                  title="Open-market insider buying from SEC Form 4 filings, last 90 days. Selling is deliberately never scored — executives sell for diversification, taxes and scheduled 10b5-1 plans, so only buying carries information."
                >
                  Insider{' '}
                  <span className={`tabular-nums ${(plan.insider_score ?? 0) > 0 ? 'text-green' : (plan.insider_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {(plan.insider_score ?? 0) > 0 ? '+' : ''}
                    {plan.insider_score ?? 0}
                  </span>
                </span>
                <span
                  className="text-muted"
                  title="Consistent beat/miss streak on reported (already-happened) consensus EPS, last several quarters. Real history, not a forecast of the next print."
                >
                  Earnings Track{' '}
                  <span className={`tabular-nums ${(plan.earnings_surprise_score ?? 0) > 0 ? 'text-green' : (plan.earnings_surprise_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {(plan.earnings_surprise_score ?? 0) > 0 ? '+' : ''}
                    {plan.earnings_surprise_score ?? 0}
                  </span>
                </span>
                <span
                  className="text-muted"
                  title="Options market pricing a move meaningfully larger than this stock's own recent range. A magnitude read, not a direction call — penalizes a long and a short on the same setup identically."
                >
                  Expected Move{' '}
                  <span className={`tabular-nums ${(plan.expected_move_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {plan.expected_move_score ?? 0}
                  </span>
                </span>
                <span
                  className="text-muted"
                  title="A scheduled FOMC decision, CPI release or jobs report today or tomorrow — market-wide event risk, same for every plan on a given day, hand-maintained from federalreserve.gov and bls.gov."
                >
                  Macro Event{' '}
                  <span className={`tabular-nums ${(plan.macro_event_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {plan.macro_event_score ?? 0}
                  </span>
                </span>
                <span
                  className="text-muted"
                  title="The AI Trading Overlay's second opinion, scored. Only ever a penalty (0 to -3, scaled by how sure the AI says it is) and never a bonus: the AI is shown the rule-based verdict, so its agreement is weak evidence, and it must not be able to talk the engine into a trade the rules rejected."
                >
                  AI Overlay{' '}
                  <span className={`tabular-nums ${(plan.ai_overlay_score ?? 0) < 0 ? 'text-red' : ''}`}>
                    {plan.ai_overlay_score ?? 0}
                  </span>
                </span>
              </div>
              {(plan.expected_move_pct != null || plan.historical_earnings_move_pct != null) && (
                <div className="text-muted" style={{ fontSize: 11, marginTop: -6, marginBottom: 8 }}>
                  {plan.expected_move_pct != null && <>Options imply ±{formatNumber(plan.expected_move_pct, 1)}% by next expiration</>}
                  {plan.expected_move_pct != null && plan.historical_earnings_move_pct != null && ' · '}
                  {plan.historical_earnings_move_pct != null && (
                    <>historically moves ±{formatNumber(plan.historical_earnings_move_pct, 1)}% on earnings</>
                  )}
                </div>
              )}
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--text-muted)' }}>
                {plan.signal_reasons.split('; ').map((reason) => (
                  <li key={reason} style={{ marginBottom: 2 }}>
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <AiOpinionBlock plan={plan} />
        </div>
      </div>

      <div className="grid stat-grid" style={{ marginBottom: 16 }}>
        <div className="card" style={{ background: 'var(--card-alt)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <IconBadge variant="up" />
          <div>
            <div className="text-muted" style={{ fontSize: 11 }}>
              Potential Gain
            </div>
            <div className="tabular-nums text-green" style={{ fontWeight: 700 }}>
              {formatMoney(plan.potential_gain)}
            </div>
          </div>
        </div>
        <div className="card" style={{ background: 'var(--card-alt)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <IconBadge variant="down" />
          <div>
            <div className="text-muted" style={{ fontSize: 11 }}>
              Potential Risk
            </div>
            <div className="tabular-nums text-red" style={{ fontWeight: 700 }}>
              {formatMoney(plan.potential_risk)}
            </div>
          </div>
        </div>
        <div className="card" style={{ background: 'var(--card-alt)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <IconBadge variant="info" />
          <div>
            {/* Deliberately "Confidence", not "Win Probability" — this is a
                rule-based scanner score, not a backtested win rate. See
                notes/Decisions.md on not fabricating historical stats. */}
            <div className="text-muted" style={{ fontSize: 11 }}>
              Confidence
            </div>
            <div className="tabular-nums" style={{ fontWeight: 700, marginBottom: 4 }}>
              {plan.confidence_score}%
              {plan.confidence_points != null && plan.confidence_points_max != null && (
                <span
                  className="text-muted"
                  style={{ fontWeight: 400, fontSize: 11, marginLeft: 6 }}
                  title="Confidence is the share of the engine's evidence points this setup earned. Only 17 values are reachable, about 6 points apart — the percentage is not a continuous scale."
                >
                  {plan.confidence_points}/{plan.confidence_points_max} pts
                </span>
              )}
            </div>
            <div style={{ width: '100%', height: 4, borderRadius: 999, background: 'var(--border)', overflow: 'hidden' }}>
              <div style={{ width: `${plan.confidence_score ?? 0}%`, height: '100%', background: 'var(--indigo-light)' }} />
            </div>
          </div>
        </div>
      </div>

      {openError && <ErrorBanner message={(openError as ApiError).message} />}

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 16,
          flexWrap: 'wrap',
          background: 'var(--card-alt)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 16,
        }}
      >
        {executed ? (
          <div>
            <div style={{ fontWeight: 700, marginBottom: 4 }} className="text-green">
              ✓ Executed
            </div>
            <div className="text-muted" style={{ fontSize: 13, maxWidth: 480 }}>
              Opened automatically as a simulated paper position — see it on the Portfolio page. No real money or
              broker order is involved.
            </div>
          </div>
        ) : plan.status === 'pending' ? (
          <>
            <div>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>
                {plan.redo_at ? 'Waiting for the next open' : blockedReason ? 'Market closed' : 'Ready to Execute?'}
              </div>
              <div className="text-muted" style={{ fontSize: 13, maxWidth: 480 }}>
                {plan.redo_at ? (
                  // The backend's own note says what happened and when, in
                  // New York time; the viewer's clock is added alongside.
                  <>
                    {plan.auto_execute_note ?? 'Made while the market was closed: it will be redone from fresh data at the next open.'}{' '}
                    ({formatLocalTime(plan.redo_at)} your time.)
                  </>
                ) : (
                  (blockedReason ??
                  'This trade plan was built from real-time market data. Review it, then execute it as a simulated paper position — no real money or broker order is involved.')
                )}
              </div>
              {/* Distinguishes "pending because auto-execute is off" from
                  "pending because auto-execute deliberately declined to fire" —
                  the AI Trading Overlay disagreement is the one case worth a
                  visibly different (amber, not muted) treatment, since it's
                  the app actively asking you to make the call it wouldn't
                  make unattended. A deferred plan's note is already the text
                  above. */}
              {plan.redo_at ? null : plan.auto_execute_note?.startsWith('Auto-execute held') ? (
                <div className="badge badge-amber" style={{ marginTop: 10, maxWidth: 480, whiteSpace: 'normal', display: 'inline-block' }}>
                  ⚠ {plan.auto_execute_note}
                </div>
              ) : (
                plan.auto_execute_note && (
                  <div className="text-muted" style={{ fontSize: 12, marginTop: 8, maxWidth: 480 }}>
                    {plan.auto_execute_note}
                  </div>
                )
              )}
            </div>
            <button
              className="btn btn-primary"
              disabled={isPending || blockedReason !== null}
              title={blockedReason ?? undefined}
              onClick={() => plan.id && open(plan.id)}
            >
              {isPending ? 'Executing…' : 'Execute Trade Plan'}
            </button>
          </>
        ) : (
          <div className="text-muted" style={{ fontSize: 13 }}>
            This plan was superseded by a newer one generated for {plan.symbol}.
          </div>
        )}
      </div>
    </div>
  );
}

export function TradePlansPage() {
  // URL-backed, not a hardcoded default — arriving via "Generate Trade Plan"
  // from a symbol's Analysis page carries the symbol along (?symbol=X);
  // landing here directly requires an explicit choice, same as Analysis.
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol');
  const [statusFilter, setStatusFilter] = useState('active');
  const { mutate: generate, data: latest, isPending, error: generateError } = useGenerateTradePlan();
  const { data: history, isLoading: historyLoading } = useTradePlans();

  const filteredHistory = useMemo(() => {
    if (!history) return [];
    if (statusFilter === 'all') return history;
    if (statusFilter === 'active') return history.filter((p) => p.status === 'pending' || p.status === 'executed');
    return history.filter((p) => p.status === statusFilter);
  }, [history, statusFilter]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ fontSize: 22 }}>Trade Plans</h1>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <CompanyDropdown value={symbol ?? ''} onChange={(s) => setParams({ symbol: s })} />
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => symbol && generate(symbol)}
            disabled={isPending || !symbol}
            title={symbol ? undefined : 'Choose a company first'}
          >
            {isPending ? 'Generating…' : 'Generate Trade Plan'}
          </button>
        </div>
      </div>

      {generateError && <ErrorBanner message={(generateError as ApiError).message} />}
      {latest && (
        <>
          <PipelineSteps />
          <TradePlanCard plan={latest} />
        </>
      )}

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <h3>History</h3>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={{ width: 200 }}>
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
        {historyLoading && <LoadingSpinner />}
        {history && filteredHistory.length === 0 && (
          <EmptyState>{history.length === 0 ? 'No trade plans yet.' : 'No trade plans match this filter.'}</EmptyState>
        )}
        {filteredHistory.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>R:R</th>
                <th>Confidence</th>
                <th>Strategy</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredHistory.map((p) => (
                <tr key={p.id}>
                  <td style={{ fontWeight: 600 }}>
                    <TickerLink symbol={p.symbol} iconSize={24} />
                  </td>
                  <td>
                    <DirectionBadge direction={p.direction} />
                  </td>
                  <td className="tabular-nums">{formatMoney(p.entry)}</td>
                  <td className="tabular-nums">{p.rr1 ? `${formatNumber(p.rr1)}:1` : '—'}</td>
                  <td className="tabular-nums">{p.confidence_score ?? '—'}</td>
                  <td className="tabular-nums">{p.strategy_version != null ? `v${p.strategy_version}` : '—'}</td>
                  <td>
                    <TradePlanStatusBadge status={p.status} />
                    {p.redo_at && (
                      <div className="text-muted" style={{ fontSize: 11, marginTop: 2 }} title={p.auto_execute_note ?? undefined}>
                        redo at the open, {formatLocalTime(p.redo_at)}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <MissedTradesCard />
      <StrategyHistoryCard />
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getDisplayTimeZone } from '../lib/timezone';
import { SilentSignals } from '../components/SilentSignals';
import { ScoreTable, type ScorePart } from '../components/ScoreTable';
import { Link, useSearchParams } from 'react-router-dom';
import {
  qk,
  useAnalysis,
  useCommitteeRun,
  useClearTradePlans,
  useGenerateTradePlan,
  useMarketSession,
  useNewsCards,
  useOpenPosition,
  useSettings,
  useTradePlans,
} from '../api/hooks';
import { LiveRun } from './CommitteePage';
import { SleeveSwitcher, useSelectedSleeve } from '../components/SleeveSwitcher';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { DirectionBadge, TradePlanStatusBadge } from '../components/Badge';
import { PipelineSteps } from '../components/PipelineSteps';
import { MissedTradesCard } from '../components/MissedTradesCard';
import { ReplayCard } from '../components/ReplayCard';
import { StrategyHistoryCard } from '../components/StrategyHistoryCard';
import { NewsCardChips } from '../components/NewsCardChips';
import { TickerLink } from '../components/TickerLink';
import { IconBadge } from '../components/IconBadge';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatRelativeTime, isSafeHttpUrl } from '../components/common';
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
          <span className="text-muted" style={{ fontSize: 12 }} title="The model that gave this verdict (Settings → AI → Decision model).">
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
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', timeZone: getDisplayTimeZone() });
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
  const committeeRun = useCommitteeRun(plan.committee_run_id ?? null);
  const committeeBusy = committeeRun.data?.status === 'queued' || committeeRun.data?.status === 'running';
  const { data: settings } = useSettings();

  if (plan.direction === null) {
    return (
      <div className="card" style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <IconBadge variant="info" size={44} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <TickerLink symbol={plan.symbol} iconSize={24} fontWeight={700} style={{ fontSize: 16 }} />
              <span className="badge badge-neutral">No Trade Plan</span>
              <StrategyVersionChip version={plan.strategy_version} />
            </div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{plan.reason ?? 'No trade plan generated'}</div>
            <div className="text-muted" style={{ fontSize: 13, maxWidth: 560 }}>
              This isn't an error — {plan.symbol} was fully evaluated (price, volume, indicators, fundamentals, news,
              earnings) and just didn't clear the bar for a trade plan this time. Quality over quantity: pick a
              different symbol from the dropdown above, or check back after the next scan.
            </div>
          </div>
          <DecisionChecklist plan={plan} minConfidence={settings?.min_confidence_for_trade} />
          <AiOpinionBlock plan={plan} />
          <PlanNews plan={plan} />
          <SilentSignals signals={plan.shadow_signals} />
          <div>
            <h3 style={{ marginBottom: 4 }}>Score breakdown</h3>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
              Every part of the evidence, what it scored and why. Parts with no signal are dimmed.
            </div>
            <ScoreTable parts={buildScoreParts(plan)} direction={null} />
          </div>
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
  const blockedReason = committeeBusy ? 'The AI Committee is still reading this plan.' : executeBlockedReason(plan, session, now);

  const parts = buildScoreParts(plan);

  const executePanel = (
      <div
        style={{
          display: 'flex',
          gap: 12,
          background: 'var(--card-alt)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 16,
          flexDirection: 'column',
          alignItems: 'stretch',
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
  );

  return (
    <div className="card plan-card">
      <div className="plan-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <TickerLink symbol={plan.symbol} iconSize={36} fontWeight={700} style={{ fontSize: 20 }} />
          <DirectionBadge direction={plan.direction} />
          {executed && <span className="badge badge-green">Executed</span>}
        </div>
        <span className="text-muted" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <StrategyVersionChip version={plan.strategy_version} />
          {plan.ai_provider && plan.ai_provider !== 'none' ? `Generated by ${plan.ai_provider}` : 'Rule-based plan'}
        </span>
      </div>

      <div className="plan-figures">
        <div className="plan-figure">
          <div className="plan-figure-label">Entry</div>
          <div className="plan-figure-value tabular-nums">{formatMoney(plan.entry)}</div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Stop</div>
          <div className="plan-figure-value tabular-nums text-red">{formatMoney(plan.stop)}</div>
          {/* A stop under ~1 ATR sits inside normal daily noise: worth seeing before you execute, not after. */}
          {plan.stop_atr_multiple != null && (
            <div
              className="tabular-nums"
              style={{ fontSize: 11, color: plan.stop_atr_multiple < 1 ? 'var(--amber)' : 'var(--text-muted)' }}
              title={plan.atr != null ? `ATR14 ${formatMoney(plan.atr)}` : undefined}
            >
              {formatNumber(plan.stop_atr_multiple, 1)}× ATR{plan.stop_atr_multiple < 1 ? ', inside daily noise' : ''}
            </div>
          )}
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Take profit 1</div>
          <div className="plan-figure-value tabular-nums text-green">{formatMoney(plan.tp1)}</div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Take profit 2</div>
          <div className="plan-figure-value tabular-nums text-green">{formatMoney(plan.tp2)}</div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Position size</div>
          <div className="plan-figure-value tabular-nums">{plan.suggested_shares} shares</div>
          <div className="text-muted tabular-nums" style={{ fontSize: 11 }}>
            risking {formatMoney(plan.account_risk_dollars)}
            {plan.capped_by_cash && <span style={{ color: 'var(--amber)' }}>, capped by cash</span>}
          </div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Potential gain</div>
          <div className="plan-figure-value tabular-nums text-green">{formatMoney(plan.potential_gain)}</div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Potential loss</div>
          <div className="plan-figure-value tabular-nums text-red">{formatMoney(plan.potential_risk)}</div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Reward to risk</div>
          <div className="plan-figure-value tabular-nums">{plan.rr1 != null ? `${formatNumber(plan.rr1, 1)} : 1` : '—'}</div>
        </div>
        <div className="plan-figure">
          <div className="plan-figure-label">Time horizon</div>
          <div className="plan-figure-value">{plan.time_horizon ?? '—'}</div>
        </div>
        {/* Deliberately "Confidence", not "Win Probability": a rule-based scanner score, not a backtested win
            rate (notes/Decisions.md). */}
        <div className="plan-figure">
          <div className="plan-figure-label">Confidence</div>
          <div className="plan-figure-value tabular-nums">
            {plan.confidence_score}%
            {plan.confidence_points != null && plan.confidence_points_max != null && (
              <span
                className="text-muted"
                style={{ fontWeight: 400, fontSize: 11, marginLeft: 6 }}
                title="Confidence is the share of the engine's evidence points this setup earned. Only a few values are reachable, so the percentage is not a continuous scale."
              >
                {plan.confidence_points}/{plan.confidence_points_max} pts
              </span>
            )}
          </div>
          <div className="plan-meter">
            <div style={{ width: `${plan.confidence_score ?? 0}%` }} />
          </div>
        </div>
      </div>

      <div className="plan-body">
        <div className="plan-col">
          {analysis && <CandlestickChart candles={analysis.candles} levels={levels} height={340} />}
          <div className="plan-panel">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <IconBadge variant="info" size={26} />
              <span style={{ fontWeight: 700, fontSize: 13 }}>{plan.ai_provider && plan.ai_provider !== 'none' ? 'AI take' : "Analyst's take"}</span>
            </div>
            <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
              {plan.ai_take_text} <span style={{ opacity: 0.6 }}>({plan.ai_provider === 'none' ? 'rule-based' : plan.ai_provider})</span>
            </div>
          </div>
          {plan.signal_reasons && (
            <div className="plan-panel">
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Why this plan</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                {plan.signal_reasons.split('; ').map((reason) => (
                  <li key={reason} style={{ marginBottom: 2 }}>
                    {reason}
                  </li>
                ))}
              </ul>
              {(plan.expected_move_pct != null || plan.historical_earnings_move_pct != null) && (
                <div className="text-muted" style={{ fontSize: 12, marginTop: 8 }}>
                  {plan.expected_move_pct != null && <>Options imply ±{formatNumber(plan.expected_move_pct, 1)}% by next expiration</>}
                  {plan.expected_move_pct != null && plan.historical_earnings_move_pct != null && ' · '}
                  {plan.historical_earnings_move_pct != null && (
                    <>historically moves ±{formatNumber(plan.historical_earnings_move_pct, 1)}% on earnings</>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="plan-col">
          {openError && <ErrorBanner message={(openError as ApiError).message} />}
          {executePanel}
          <CommitteeNote plan={plan} />
          <DecisionChecklist plan={plan} minConfidence={settings?.min_confidence_for_trade} />
          <SilentSignals signals={plan.shadow_signals} />
          <AiOpinionBlock plan={plan} />
          <PlanNews plan={plan} />
        </div>
      </div>

      <div style={{ marginTop: 28 }}>
        <h3 style={{ marginBottom: 4 }}>Score breakdown</h3>
        <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
          Every part of the evidence, what it scored and why. Parts with no signal are dimmed.
        </div>
        <ScoreTable
          parts={parts}
          direction={plan.direction}
          committee={{
            rating: committeeRun.data?.rating ?? plan.committee_rating ?? null,
            badgeClass: COMMITTEE_RATING_CLASS[committeeRun.data?.rating ?? plan.committee_rating ?? ''] ?? 'badge badge-neutral',
            note: plan.committee_note ?? null,
            running: committeeBusy,
            gateOn: plan.committee_run_id != null || !!settings?.committee_gate_enabled,
          }}
        />
      </div>
    </div>
  );
}

const COMMITTEE_RATING_CLASS: Record<string, string> = {
  Buy: 'badge badge-green',
  Overweight: 'badge badge-green',
  Hold: 'badge badge-neutral',
  Underweight: 'badge badge-amber',
  Sell: 'badge badge-red',
};

/** What the AI Committee made of this plan: live while it reads, then the verdict, with every report one click away. */
function CommitteeNote({ plan }: { plan: TradePlan }) {
  const { data: settings } = useSettings();
  const queryClient = useQueryClient();
  const runId = plan.committee_run_id ?? null;
  const run = useCommitteeRun(runId);
  const status = run.data?.status;
  const active = status === 'queued' || status === 'running';
  const [showAll, setShowAll] = useState(false);
  const wasActive = useRef(false);

  // When the run ends, reload the plans: the verdict may have changed the plan (cancelled it, added its note).
  useEffect(() => {
    if (active) {
      wasActive.current = true;
    } else if (wasActive.current && status) {
      wasActive.current = false;
      queryClient.invalidateQueries({ queryKey: qk.tradePlans });
    }
  }, [active, status, queryClient]);

  if (runId === null && plan.direction == null) return null; // a no-trade decision never reaches the committee
  const rating = run.data?.rating ?? plan.committee_rating ?? null;
  const showing = active || showAll;
  const done = run.data ? run.data.steps.filter((s) => s.status === 'done').length : 0;
  const total = run.data ? run.data.steps.filter((s) => s.status !== 'skipped').length : 0;
  return (
    <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14 }} data-testid="plan-committee">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>AI Committee</span>
        {rating && <span className={COMMITTEE_RATING_CLASS[rating] ?? 'badge badge-neutral'}>{rating}</span>}
        {active && (
          <span className="badge badge-amber">
            reading… {done} of {total} steps
          </span>
        )}
        {runId !== null && !active && (
          <button type="button" className="btn btn-secondary" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Hide the debate' : 'Read the full debate'}
          </button>
        )}
        {runId !== null && (
          <Link to={`/committee?run=${runId}`} style={{ fontSize: 12 }}>
            Open on the Committee page
          </Link>
        )}
      </div>
      {plan.committee_note && (
        <div className="text-muted" style={{ fontSize: 12, marginTop: 6 }}>
          {plan.committee_note}
        </div>
      )}
      {!plan.committee_note && runId === null && (
        <div className="text-muted" style={{ fontSize: 12, marginTop: 6 }}>
          {settings?.committee_gate_enabled ? 'Not read by the committee.' : 'Not read: the committee gate is off (Settings, AI tab).'}
        </div>
      )}
      {showing && runId !== null && (
        <div style={{ marginTop: 12 }}>
          <LiveRun id={runId} />
        </div>
      )}
    </div>
  );
}

// The parts added after the original ten (backend analysis/live_evidence.py). Each is signed for the trade's
// direction; the last two are caution flags that can only subtract.
const EXTRA_PARTS: [string, string, string][] = [
  ['congress', 'Congress', 'Members of Congress net buying (supports a long) or net selling (supports a short), by filing date, last 45 days. A net of 4 members is worth 2 points. Reports lag by up to 45 days.'],
  ['funds', 'Funds', 'Followed funds 13F changes: net buying supports a long, net selling supports a short. 3 or more funds is worth 2 points. Data is up to 45 days old.'],
  ['ownership', '5% owners', 'Holders of 5% or more: a new Schedule 13D or a raised stake is buying, a cut stake or falling under 5% is selling. Last 90 days, up to 2 points.'],
  ['short_volume', 'Short vol', 'Unusually high FINRA short-sale volume against its own baseline: argues against a long, supports a short. Short volume is not short interest.'],
  ['filing_8k', '8-K', 'Negative-leaning 8-K items filed in the last 3 days (bankruptcy, delisting, restatement, executive change): against a long, mildly for a short.'],
  ['financial_health', 'Financials', 'Profitability trend from the reported years: profitable and improving supports a long, loss-making or sliding profits support a short.'],
  ['valuation', 'Valuation', 'P/E at the extremes (15 or less is cheap, over 60 or no earnings is expensive unless revenue grows 30%+) plus the DCF and peer comparison: cheap supports a long, expensive supports a short. Up to 2 points.'],
  ['analyst_consensus', 'Consensus', 'Consensus EPS for the next report against the last reported EPS: expected growth of 10% or more supports a long, an expected fall of 5% or more supports a short.'],
  ['relative_strength', 'Vs market', 'The stock\'s 63-day return against SPY: leading by 5 points or more supports a long, lagging by that much supports a short.'],
  ['volume_trend', 'Volume trend', 'Volume on up days against down days over 20 days: accumulation supports a long, distribution supports a short.'],
  ['options_oi', 'Options OI', 'Put/call ratio of open interest (positions still held): call-heavy supports a long, put-heavy supports a short.'],
  ['fed_window', 'Fed window', 'Caution flag: a Fed statement or Chair speech within a day. Only ever subtracts, whichever way the trade goes.'],
  ['post_mentions', 'Posts', 'Caution flag: a post naming this company in the last 24 hours (an unofficial archive). Only ever subtracts.'],
];

/** Each saved headline for the symbol with the AI's rating of it (sentiment, materiality, event type, summary). */
function PlanNews({ plan }: { plan: TradePlan }) {
  const { data } = useNewsCards(plan.symbol);
  const note = plan.extra_notes?.news;
  const items = data?.items ?? [];
  return (
    <div className="plan-panel">
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>
        News and how each headline was rated
        {plan.news_score != null && (
          <span className="text-muted tabular-nums" style={{ fontWeight: 400, marginLeft: 8 }}>
            news part: {plan.news_score > 0 ? '+' : ''}
            {plan.news_score} pts
          </span>
        )}
      </div>
      {note && (
        <div className="text-muted" style={{ fontSize: 12, marginBottom: 8, lineHeight: 1.5 }}>
          Why the news part scored this way: {note}
        </div>
      )}
      {items.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 12 }}>
          No headlines are saved for {plan.symbol}.
        </div>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {items.slice(0, 10).map((n) => (
            <li key={n.url || n.headline}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {isSafeHttpUrl(n.url) ? (
                  <a href={n.url} target="_blank" rel="noreferrer">
                    {n.headline}
                  </a>
                ) : (
                  n.headline
                )}
              </div>
              <div className="text-muted" style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <span>{n.publisher}</span>
                <span>{formatRelativeTime(n.known_at)}</span>
                {n.card ? <NewsCardChips card={n.card} /> : <span className="badge badge-neutral">not rated by the AI</span>}
              </div>
              {n.card?.one_line_summary && (
                <div className="text-muted" style={{ fontSize: 12, marginTop: 2 }}>
                  {n.card.one_line_summary}
                  {!n.card.is_about_this_company && ' (Not mainly about this company.)'}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="text-muted" style={{ fontSize: 11, marginTop: 8, opacity: 0.8 }}>
        These are the headlines saved now, not a snapshot from when the plan was made. AI labels are written when
        labelled, so they are only meant to be judged on recent news.
      </div>
    </div>
  );
}

/** The gates a plan passes or fails, in the order they apply, so "why no trade?" has a visible answer. */
function DecisionChecklist({ plan, minConfidence }: { plan: TradePlan; minConfidence: number | undefined }) {
  const rows: { ok: boolean | null; label: string; detail: string }[] = [];
  if (minConfidence != null && plan.confidence_score != null) {
    rows.push({
      ok: plan.confidence_score >= minConfidence,
      label: 'Confidence bar',
      detail: `${plan.confidence_score}% against a bar of ${minConfidence}%${
        plan.confidence_points != null && plan.confidence_points_max != null
          ? ` (${plan.confidence_points}/${plan.confidence_points_max} evidence points)`
          : ''
      }.`,
    });
  }
  if (plan.ai_trade_verdict) {
    rows.push({
      ok: plan.ai_trade_verdict !== 'pass',
      label: 'AI second opinion',
      detail: `${plan.ai_trade_verdict === 'pass' ? 'Would not take' : 'Would take'} this trade${
        plan.ai_decision_model ? ` (${plan.ai_decision_model})` : ''
      }. Cost to confidence: ${plan.ai_overlay_score ?? 0} pts.`,
    });
  }
  if (plan.committee_rating) {
    rows.push({ ok: null, label: 'AI Committee', detail: `Rated ${plan.committee_rating}${plan.committee_note ? `: ${plan.committee_note}` : '.'}` });
  }
  if (plan.auto_execute_note) rows.push({ ok: null, label: 'Auto-execute', detail: plan.auto_execute_note });
  if (rows.length === 0) return null;
  return (
    <div className="plan-panel">
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Decision checklist</div>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', fontSize: 13, lineHeight: 1.6 }}>
        {rows.map((r) => (
          <li key={r.label}>
            <span className={r.ok === null ? 'text-muted' : r.ok ? 'text-green' : 'text-red'} style={{ fontWeight: 700 }}>
              {r.ok === null ? '•' : r.ok ? '✓' : '✗'} {r.label}
            </span>{' '}
            <span className="text-muted">{r.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Every scored part of a plan, with the stored reason for each; shared by the tradeable and the no-trade card. */
function buildScoreParts(plan: TradePlan): ScorePart[] {
  const val = (v: number | null | undefined) => v ?? 0;
  return [
    { label: 'Technical', value: val(plan.technical_score), unsigned: true, help: 'Trend, momentum, structure and volume from the price chart.' },
    { label: 'Fundamentals', note: plan.extra_notes?.fundamentals, value: val(plan.fundamental_score), help: 'Revenue growth, margins and balance-sheet health, signed for the trade direction.' },
    { label: 'News', note: plan.extra_notes?.news, value: val(plan.news_score), help: 'Tone of recent headlines. Bullish news supports a long and argues against a short.' },
    { label: 'Confluence', note: plan.extra_notes?.confluence, value: val(plan.market_confirmation_score), help: 'Weekly-timeframe and broad market (SPY) agreement with the trade.' },
    { label: 'VIX', note: plan.extra_notes?.vix, value: val(plan.vix_regime_score), penaltyOnly: true, help: 'VIX regime. Only ever a penalty when elevated (25 or more), never a bonus.' },
    { label: 'Options', note: plan.extra_notes?.options, value: val(plan.options_score), help: 'Options put/call volume skew agreement (no chain data for most crypto and thin names).' },
    {
      label: 'Insider',
      value: val(plan.insider_score),
      help: 'SEC Form 4, last 90 days, up to 2 points either way. Net open-market buying supports a long ($100k net is 1 point, $1M is 2). Selling argues against a long: sales by choice count in full, sales under a 10b5-1 plan at a quarter of their value.',
    },
    ...EXTRA_PARTS.map(([key, label, help]) => ({
      label,
      help,
      value: val(plan.extra_scores?.[key]),
      note: plan.extra_notes?.[key],
      penaltyOnly: key === 'fed_window' || key === 'post_mentions' || key === 'short_volume',
    })),
    { label: 'Earnings Track', note: plan.extra_notes?.earnings_track, value: val(plan.earnings_surprise_score), help: 'Consistent beat or miss streak on reported consensus EPS over the last quarters. Real history, not a forecast of the next print.' },
    { label: 'Expected Move', note: plan.extra_notes?.expected_move, value: val(plan.expected_move_score), penaltyOnly: true, help: "Options market pricing a move larger than this stock's own recent range. A magnitude read, not a direction call: it penalizes a long and a short the same." },
    { label: 'Macro Event', note: plan.extra_notes?.macro_event, value: val(plan.macro_event_score), penaltyOnly: true, help: 'A scheduled FOMC decision, CPI release or jobs report today or tomorrow. Market-wide event risk, hand-maintained from federalreserve.gov and bls.gov.' },
    { label: 'AI Overlay', note: plan.extra_notes?.ai_overlay, value: val(plan.ai_overlay_score), penaltyOnly: true, help: 'The AI second opinion, scored. Only ever a penalty (0 to -3) and never a bonus, so the AI cannot talk the engine into a trade the rules rejected.' },
  ];
}

export function TradePlansPage() {
  // URL-backed, not a hardcoded default — arriving via "Generate Trade Plan"
  // from a symbol's Analysis page carries the symbol along (?symbol=X);
  // landing here directly requires an explicit choice, same as Analysis.
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol');
  const selectedPlanId = params.get('plan') ? Number(params.get('plan')) : null;
  const [statusFilter, setStatusFilter] = useState('active');
  const [sleeve, selectSleeve] = useSelectedSleeve();
  const { mutate: generate, data: latest, isPending, error: generateError } = useGenerateTradePlan();
  const { mutate: clearPlans, isPending: clearing } = useClearTradePlans();
  const { data: settings } = useSettings();
  const { data: history, isLoading: historyLoading } = useTradePlans();
  // Opening a plan from any list brings its card into view.
  useEffect(() => {
    if (selectedPlanId !== null) window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [selectedPlanId]);
  // The plan on show: the one clicked in the history, else the one just generated (re-read from the list so a
  // committee verdict that arrives later shows up), else the freshly returned copy.
  const shownId = selectedPlanId ?? latest?.id ?? null;
  const shown = (shownId !== null ? history?.find((p) => p.id === shownId) : undefined) ?? (selectedPlanId === null ? latest : undefined);

  const filteredHistory = useMemo(() => {
    if (!history) return [];
    const inSleeve = history.filter((p) => (p.sleeve_key || 'core') === sleeve);
    if (statusFilter === 'all') return inSleeve;
    if (statusFilter === 'active') return inSleeve.filter((p) => p.status === 'pending' || p.status === 'executed');
    return inSleeve.filter((p) => p.status === statusFilter);
  }, [history, statusFilter, sleeve]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ fontSize: 22 }}>Trade Plans</h1>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <CompanyDropdown value={symbol ?? ''} onChange={(s) => setParams({ symbol: s })} />
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              if (!symbol) return;
              const next = new URLSearchParams(params);
              next.delete('plan');
              setParams(next, { replace: true });
              generate({ symbol, sleeve });
            }}
            disabled={isPending || !symbol}
            title={symbol ? undefined : 'Choose a company first'}
          >
            {isPending ? 'Generating…' : 'Generate Trade Plan'}
          </button>
        </div>
      </div>
      {isPending && settings?.committee_gate_enabled && (
        <div className="text-muted" style={{ fontSize: 13 }}>
          The rules are scoring it first. If the plan passes, the AI Committee then reads it with all the data gathered, which
          can take a few minutes. Keep this page open.
        </div>
      )}

      <SleeveSwitcher selected={sleeve} onSelect={selectSleeve} />

      {generateError && <ErrorBanner message={(generateError as ApiError).message} />}
      {shown && (
        <>
          {selectedPlanId === null && <PipelineSteps />}
          {selectedPlanId !== null && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span className="text-muted" style={{ fontSize: 13 }}>
                Showing plan #{shown.id} from {shown.created_at ? formatLocalTime(shown.created_at) : 'earlier'}.
              </span>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ padding: '2px 10px', fontSize: 12 }}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.delete('plan');
                  setParams(next, { replace: true });
                }}
              >
                Close
              </button>
            </div>
          )}
          <TradePlanCard key={shown.id} plan={shown} />
        </>
      )}

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <h3 style={{ marginRight: 'auto' }}>History</h3>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={clearing || !history || history.length === 0}
            title="Deletes every stored trade plan. Open positions are kept."
            onClick={() => {
              if (window.confirm('Delete all stored trade plans? Open and closed positions are kept.')) {
                const next = new URLSearchParams(params);
                next.delete('plan');
                setParams(next, { replace: true });
                clearPlans();
              }
            }}
          >
            {clearing ? 'Deleting…' : 'Delete all plans'}
          </button>
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
                <th />
              </tr>
            </thead>
            <tbody>
              {filteredHistory.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    next.set('plan', String(p.id));
                    setParams(next, { replace: true });
                  }}
                  style={{ cursor: 'pointer', background: p.id === selectedPlanId ? 'var(--card-alt)' : undefined }}
                  title="Click to read this plan"
                  data-testid={`plan-row-${p.id}`}
                >
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
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button
                      type="button"
                      className={p.id === selectedPlanId ? 'btn btn-primary' : 'btn btn-secondary'}
                      style={{ padding: '4px 12px', fontSize: 12 }}
                      aria-label={`Read plan ${p.id}, ${p.symbol}`}
                      data-testid={`read-plan-${p.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        const next = new URLSearchParams(params);
                        next.set('plan', String(p.id));
                        setParams(next, { replace: true });
                      }}
                    >
                      {p.id === selectedPlanId ? 'Showing' : 'Read plan'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <MissedTradesCard />
      <ReplayCard />

      <StrategyHistoryCard />
    </div>
  );
}

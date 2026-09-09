import { useMemo, useState } from 'react';
import { useAnalysis, useGenerateTradePlan, useOpenPosition, useTradePlans } from '../api/hooks';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { DirectionBadge, TradePlanStatusBadge } from '../components/Badge';
import { PipelineSteps } from '../components/PipelineSteps';
import { TickerLink } from '../components/TickerLink';
import { RatioGauge } from '../components/RatioGauge';
import { IconBadge } from '../components/IconBadge';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber } from '../components/common';
import type { ApiError } from '../api/client';
import type { TradePlan } from '../api/types';

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
        {plan.ai_opinion_stance && <span className={`badge ${stanceBadgeClass}`}>{plan.ai_opinion_stance}</span>}
        {plan.ai_opinion_score != null && (
          <span className="text-muted tabular-nums" style={{ fontSize: 12 }}>
            {plan.ai_opinion_score}%
          </span>
        )}
      </div>
      <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
        {plan.ai_opinion_text}
      </div>
      {plan.ai_news_assessment && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 2 }}>AI's read of the news</div>
          <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            {plan.ai_news_assessment}
          </div>
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 11, marginTop: 6, opacity: 0.7 }}>
        Independent read from the AI provider (Settings → AI Trading Overlay) — separate from, and free to disagree
        with, the rule-based decision above.
      </div>
    </div>
  );
}

function TradePlanCard({ plan }: { plan: TradePlan }) {
  const { mutate: open, isPending, isSuccess, error: openError } = useOpenPosition();
  const { data: analysis } = useAnalysis(plan.direction ? plan.symbol : null);

  if (plan.direction === null) {
    return (
      <div className="card" style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <IconBadge variant="info" size={44} />
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <TickerLink symbol={plan.symbol} iconSize={24} fontWeight={700} style={{ fontSize: 16 }} />
            <span className="badge badge-neutral">No Trade Plan</span>
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

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <TickerLink symbol={plan.symbol} iconSize={32} fontWeight={700} style={{ fontSize: 16 }} />
          <DirectionBadge direction={plan.direction} />
        </div>
        <span className="text-muted" style={{ fontSize: 12 }}>
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
              </div>
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
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Ready to Execute?</div>
              <div className="text-muted" style={{ fontSize: 13, maxWidth: 480 }}>
                This trade plan was built from real-time market data. Review it, then execute it as a simulated
                paper position — no real money or broker order is involved.
              </div>
            </div>
            <button className="btn btn-primary" disabled={isPending} onClick={() => plan.id && open(plan.id)}>
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
  const [symbol, setSymbol] = useState('NVDA');
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
          <CompanyDropdown value={symbol} onChange={setSymbol} />
          <button type="button" className="btn btn-primary" onClick={() => generate(symbol)} disabled={isPending}>
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
                  <td>
                    <TradePlanStatusBadge status={p.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

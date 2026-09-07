import { useState } from 'react';
import { useGenerateTradePlan, useOpenPosition, useTradePlans } from '../api/hooks';
import { DirectionBadge } from '../components/Badge';
import { PipelineSteps } from '../components/PipelineSteps';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber } from '../components/common';
import type { ApiError } from '../api/client';
import type { TradePlan } from '../api/types';

function TradePlanCard({ plan }: { plan: TradePlan }) {
  const { mutate: open, isPending, isSuccess } = useOpenPosition();

  if (plan.direction === null) {
    return (
      <div className="card">
        <div style={{ fontWeight: 700 }}>{plan.symbol}</div>
        <div className="text-muted">{plan.reason ?? 'No trade plan generated'}</div>
      </div>
    );
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontWeight: 700, fontSize: 16 }}>{plan.symbol}</span>
          <DirectionBadge direction={plan.direction} />
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Confidence {plan.confidence_score}%
        </div>
      </div>

      <div className="grid stat-grid" style={{ marginBottom: 12 }}>
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
            R:R
          </div>
          <div className="tabular-nums">{formatNumber(plan.rr1)}:1</div>
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
      </div>

      <div className="text-muted" style={{ fontSize: 13, marginBottom: 12 }}>
        {plan.ai_take_text} <span style={{ opacity: 0.6 }}>({plan.ai_provider === 'none' ? 'rule-based' : plan.ai_provider})</span>
      </div>

      <button
        className="btn btn-primary"
        disabled={plan.status !== 'pending' || isPending || isSuccess}
        onClick={() => plan.id && open(plan.id)}
      >
        {plan.status === 'executed' || isSuccess ? 'Executed' : isPending ? 'Executing…' : 'Execute Trade Plan'}
      </button>
    </div>
  );
}

export function TradePlansPage() {
  const [symbol, setSymbol] = useState('AAPL');
  const { mutate: generate, data: latest, isPending, error: generateError } = useGenerateTradePlan();
  const { data: history, isLoading: historyLoading } = useTradePlans();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ fontSize: 22 }}>Trade Plans</h1>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            generate(symbol);
          }}
          style={{ display: 'flex', gap: 8 }}
        >
          <input type="text" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: 160 }} />
          <button type="submit" className="btn btn-primary" disabled={isPending}>
            {isPending ? 'Generating…' : 'Generate Trade Plan'}
          </button>
        </form>
      </div>

      {generateError && <ErrorBanner message={(generateError as ApiError).message} />}
      {latest && (
        <>
          <PipelineSteps />
          <TradePlanCard plan={latest} />
        </>
      )}

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>History</h3>
        {historyLoading && <LoadingSpinner />}
        {history && history.length === 0 && <EmptyState>No trade plans yet.</EmptyState>}
        {history && history.length > 0 && (
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
              {history.map((p) => (
                <tr key={p.id}>
                  <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                  <td>
                    <DirectionBadge direction={p.direction} />
                  </td>
                  <td className="tabular-nums">{formatMoney(p.entry)}</td>
                  <td className="tabular-nums">{p.rr1 ? `${formatNumber(p.rr1)}:1` : '—'}</td>
                  <td className="tabular-nums">{p.confidence_score ?? '—'}</td>
                  <td className="text-muted">{p.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

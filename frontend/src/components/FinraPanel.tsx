import { useFinraSymbol, useRefreshFinra } from '../api/hooks';
import type { ApiError } from '../api/client';
import { ErrorBanner, formatNumber } from './common';

/** FINRA daily short volume for one symbol. The signal is recorded only (never scored); the button downloads recent days. */
export function FinraPanel({ symbol }: { symbol: string }) {
  const { data, isLoading, error } = useFinraSymbol(symbol);
  const refresh = useRefreshFinra();
  const pct = (r: number | null) => (r == null ? '—' : `${(r * 100).toFixed(0)}%`);
  return (
    <div className="card" data-testid="finra-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h3>Short volume (FINRA)</h3>
        <button type="button" className="btn btn-secondary" disabled={refresh.isPending} onClick={() => refresh.mutate([symbol])}>
          {refresh.isPending ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      <div className="text-muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>
        Share of each day's traded volume that was sold short. Recorded for later testing; it does not change any score.
      </div>
      {refresh.error && <ErrorBanner message={(refresh.error as ApiError).message} />}
      {refresh.data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Checked {refresh.data.days_checked} days, {refresh.data.facts_created} new rows saved.
        </div>
      )}
      {isLoading && <div className="text-muted">Loading…</div>}
      {error && <ErrorBanner message={(error as ApiError).message} />}
      {data && data.stored_days === 0 && <div className="text-muted">No short-volume days saved for {symbol} yet. Press Refresh.</div>}
      {data && data.stored_days > 0 && (
        <>
          <div style={{ fontSize: 13, marginBottom: 8 }}>
            Recent {pct(data.recent_ratio)} ({data.recent_days} days) vs baseline {pct(data.baseline_ratio)} ({data.baseline_days} days)
            {data.signal && <span className="text-muted"> · {data.signal.reason}</span>}
          </div>
          <table>
            <thead>
              <tr><th>Date</th><th>Short</th><th>Total</th><th>Ratio</th></tr>
            </thead>
            <tbody>
              {data.days.slice(0, 10).map((d) => (
                <tr key={d.trade_date}>
                  <td>{d.trade_date}</td>
                  <td className="tabular-nums">{formatNumber(d.short_volume, 0)}</td>
                  <td className="tabular-nums">{formatNumber(d.total_volume, 0)}</td>
                  <td className="tabular-nums">{pct(d.ratio)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

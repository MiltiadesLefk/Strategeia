import { useHoldings } from '../api/hooks';
import { StatCard } from './StatCard';
import { TickerLink } from './TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatPct } from './common';
import type { ApiError } from '../api/client';

/** The portfolio view: what is held, how far each weight is from the target, sector mix, return against SPY.
 *  Read-only. The SPY comparison is from the first entry date; random and equal-weight baskets are not built. */
export function HoldingsCard({ sleeve }: { sleeve: string }) {
  const { data, isLoading, error, refetch } = useHoldings(sleeve);
  if (isLoading) return <LoadingSpinner label="Loading holdings…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />;
  if (!data) return null;
  if (data.positions.length === 0) return <EmptyState>No holdings yet. Hold buys appear here with their weights.</EmptyState>;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3 style={{ margin: 0 }}>Holdings</h3>
      <div className="grid stat-grid">
        <StatCard label="Return" value={formatPct(data.total_return_pct)} positive={data.total_return_pct >= 0} />
        <StatCard
          label="Against SPY"
          value={data.vs_spy_pct === null ? '—' : formatPct(data.vs_spy_pct)}
          positive={data.vs_spy_pct === null ? null : data.vs_spy_pct >= 0}
          note={data.spy_return_pct === null ? 'SPY not available' : `SPY ${formatPct(data.spy_return_pct)} since the first entry`}
        />
        <StatCard
          label="Equal-weight basket"
          value={data.equal_weight_return_pct === null ? '—' : formatPct(data.equal_weight_return_pct)}
          note="same stocks, equal amounts"
        />
        <StatCard label="Cash" value={`${data.cash_pct.toFixed(1)}%`} note={formatMoney(data.cash)} />
        <StatCard label="Worst drawdown" value={`${data.max_drawdown_pct.toFixed(1)}%`} note="from the equity curve" />
      </div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Sector</th>
            <th>Weight</th>
            <th>vs target {data.target_weight_pct}%</th>
            <th>P&amp;L</th>
            <th>Stop</th>
          </tr>
        </thead>
        <tbody>
          {data.positions.map((p) => (
            <tr key={p.symbol}>
              <td>
                <TickerLink symbol={p.symbol} />
              </td>
              <td>{p.sector}</td>
              <td>{p.weight_pct.toFixed(1)}%</td>
              <td>{formatPct(p.drift_pct)}</td>
              <td>
                {formatMoney(p.pnl)} ({formatPct(p.pnl_pct)})
              </td>
              <td>{p.stop_loss ? formatMoney(p.stop_loss) : p.mode === 'hold' ? 'alert only' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-muted" style={{ fontSize: 13 }}>
        Sectors: {data.sectors.map((s) => `${s.sector} ${s.weight_pct.toFixed(1)}%`).join(', ')}.
        {data.stale_symbols.length > 0 && ` No live price for ${data.stale_symbols.join(', ')}; shown at entry.`}
      </div>
    </div>
  );
}

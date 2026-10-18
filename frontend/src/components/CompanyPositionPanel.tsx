import { usePositions } from '../api/hooks';
import { EmptyState, LoadingSpinner, formatMoney } from './common';

/** What the paper portfolio holds (or held) in one stock. */
export function CompanyPositionPanel({ symbol }: { symbol: string }) {
  const { data, isLoading } = usePositions();
  if (isLoading) return <LoadingSpinner />;
  const rows = (data ?? []).filter((p) => p.symbol === symbol);
  if (rows.length === 0) return <EmptyState>No paper position in {symbol}.</EmptyState>;
  return (
    <div className="card">
      <table>
        <thead>
          <tr><th>Status</th><th>Opened</th><th>Shares</th><th>Entry</th><th>Stop</th><th>Closed at</th><th>P&amp;L</th></tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id}>
              <td>{p.status}{p.close_reason ? ` · ${p.close_reason}` : ''}</td>
              <td>{new Date(p.opened_at).toLocaleDateString()}</td>
              <td className="tabular-nums">{p.shares}</td>
              <td className="tabular-nums">{formatMoney(p.entry_price)}</td>
              <td className="tabular-nums">{p.stop_loss ? formatMoney(p.stop_loss) : '—'}</td>
              <td className="tabular-nums">{p.close_price != null ? formatMoney(p.close_price) : '—'}</td>
              <td className="tabular-nums">{p.realized_pnl != null ? formatMoney(p.realized_pnl) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

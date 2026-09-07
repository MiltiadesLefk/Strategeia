import { useEquityCurve, useClosePosition, usePortfolioStats, usePositions } from '../api/hooks';
import { StatCard } from '../components/StatCard';
import { DirectionBadge, PositionStatusBadge } from '../components/Badge';
import { EquityCurveChart } from '../components/chart/EquityCurveChart';
import { EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct } from '../components/common';

export function PortfolioPage() {
  const { data: stats, isLoading: statsLoading } = usePortfolioStats();
  const { data: equity } = useEquityCurve();
  const { data: positions, isLoading: positionsLoading } = usePositions();
  const { mutate: closePosition, isPending: closing } = useClosePosition();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 22 }}>Portfolio</h1>

      {statsLoading && <LoadingSpinner label="Loading portfolio…" />}
      {stats && (
        <div className="grid stat-grid">
          <StatCard label="Portfolio Value" value={formatMoney(stats.portfolio_value)} />
          <StatCard label="Total Return" value={formatPct(stats.total_return)} positive={stats.total_return > 0 ? true : stats.total_return < 0 ? false : null} />
          <StatCard label="Win Rate" value={`${formatNumber(stats.win_rate, 0)}%`} />
          <StatCard label="Avg R:R" value={stats.avg_rr !== null ? `${formatNumber(stats.avg_rr)}:1` : '—'} />
        </div>
      )}

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>Equity Curve</h3>
        {!equity || equity.length === 0 ? <EmptyState>No equity history yet — open a paper position to start tracking.</EmptyState> : <EquityCurveChart points={equity} />}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>Positions</h3>
        {positionsLoading && <LoadingSpinner />}
        {positions && positions.length === 0 && <EmptyState>No positions yet — execute a trade plan to open one.</EmptyState>}
        {positions && positions.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Shares</th>
                <th>Status</th>
                <th>Close Price</th>
                <th>P&L</th>
                <th>R</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id}>
                  <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                  <td>
                    <DirectionBadge direction={p.direction} />
                  </td>
                  <td className="tabular-nums">{formatMoney(p.entry_price)}</td>
                  <td className="tabular-nums">{p.shares}</td>
                  <td>
                    <PositionStatusBadge status={p.status} />
                  </td>
                  <td className="tabular-nums">{formatMoney(p.close_price)}</td>
                  <td className={`tabular-nums ${p.realized_pnl && p.realized_pnl > 0 ? 'text-green' : p.realized_pnl && p.realized_pnl < 0 ? 'text-red' : ''}`}>
                    {p.realized_pnl !== null ? formatMoney(p.realized_pnl) : '—'}
                  </td>
                  <td className="tabular-nums">{p.realized_r !== null ? formatNumber(p.realized_r) : '—'}</td>
                  <td>
                    {p.status === 'open' && (
                      <button className="btn btn-secondary" disabled={closing} onClick={() => closePosition(p.id)}>
                        Close
                      </button>
                    )}
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

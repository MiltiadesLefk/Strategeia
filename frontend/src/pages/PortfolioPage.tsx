import { useAnalysis, useEquityCurve, useClosePosition, useResetPortfolio, usePortfolioStats, usePositions } from '../api/hooks';
import { StatCard } from '../components/StatCard';
import { DirectionBadge } from '../components/Badge';
import { TickerLink } from '../components/TickerLink';
import { EquityCurveChart } from '../components/chart/EquityCurveChart';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct } from '../components/common';
import type { ApiError } from '../api/client';
import type { Position } from '../api/types';

function ActivePositionCard({ position }: { position: Position }) {
  const { data: analysis, isLoading } = useAnalysis(position.symbol);
  const { mutate: closePosition, isPending: closing } = useClosePosition();

  const levels: PriceLevel[] = [
    { price: position.entry_price, color: '#2563eb', title: 'Entry' },
    { price: position.stop_loss, color: '#ef4444', title: 'SL' },
    { price: position.tp1, color: '#10b981', title: 'TP1' },
    { price: position.tp2, color: '#10b981', title: 'TP2' },
  ];

  const currentPrice = analysis?.price;
  const unrealizedPct =
    currentPrice !== undefined
      ? ((currentPrice - position.entry_price) / position.entry_price) * 100 * (position.direction === 'long' ? 1 : -1)
      : null;

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <TickerLink symbol={position.symbol} iconSize={30} fontWeight={700} style={{ fontSize: 15 }} />
          <DirectionBadge direction={position.direction} />
          <span className="text-muted" style={{ fontSize: 12 }}>
            {position.shares} shares @ {formatMoney(position.entry_price)}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {unrealizedPct !== null && (
            <span className={`tabular-nums ${unrealizedPct >= 0 ? 'text-green' : 'text-red'}`} style={{ fontWeight: 700, fontSize: 14 }}>
              {unrealizedPct >= 0 ? '+' : ''}
              {unrealizedPct.toFixed(2)}% unrealized
            </span>
          )}
          <button className="btn btn-secondary" disabled={closing} onClick={() => closePosition(position.id)}>
            Close
          </button>
        </div>
      </div>
      {isLoading || !analysis ? (
        <LoadingSpinner label={`Loading ${position.symbol} chart…`} />
      ) : (
        <CandlestickChart candles={analysis.candles} levels={levels} height={260} />
      )}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 12, marginTop: 12 }}>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Entry
          </div>
          <div className="tabular-nums">{formatMoney(position.entry_price)}</div>
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Stop Loss
          </div>
          <div className="tabular-nums text-red">{formatMoney(position.stop_loss)}</div>
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            TP1 / TP2
          </div>
          <div className="tabular-nums text-green">
            {formatMoney(position.tp1)} / {formatMoney(position.tp2)}
          </div>
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Opened
          </div>
          <div className="tabular-nums">{new Date(position.opened_at).toLocaleDateString()}</div>
        </div>
      </div>
    </div>
  );
}

export function PortfolioPage() {
  const { data: stats, isLoading: statsLoading } = usePortfolioStats();
  const { data: equity } = useEquityCurve();
  const { data: positions, isLoading: positionsLoading } = usePositions();
  const { mutate: resetPortfolio, isPending: resetting, error: resetError } = useResetPortfolio();

  function handleReset() {
    if (window.confirm('Reset the paper account? This closes out all positions and equity history and starts fresh at the configured starting cash. This cannot be undone.')) {
      resetPortfolio();
    }
  }

  const openPositions = positions?.filter((p) => p.status === 'open') ?? [];
  const closedPositions = positions?.filter((p) => p.status === 'closed') ?? [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ fontSize: 22 }}>Portfolio</h1>
        <button className="btn btn-secondary" onClick={handleReset} disabled={resetting}>
          {resetting ? 'Resetting…' : 'Reset Paper Account'}
        </button>
      </div>
      {resetError && <ErrorBanner message={(resetError as ApiError).message} />}

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

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Active Positions</h3>
        {positionsLoading && <LoadingSpinner />}
        {positions && openPositions.length === 0 && (
          <div className="card">
            <EmptyState>No open positions — execute a trade plan to open one.</EmptyState>
          </div>
        )}
        {openPositions.map((p) => (
          <ActivePositionCard key={p.id} position={p} />
        ))}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>Closed Positions</h3>
        {closedPositions.length === 0 ? (
          <EmptyState>No closed trades yet.</EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Shares</th>
                <th>Close Price</th>
                <th>Reason</th>
                <th>P&L</th>
                <th>R</th>
              </tr>
            </thead>
            <tbody>
              {closedPositions.map((p) => (
                <tr key={p.id}>
                  <td style={{ fontWeight: 600 }}>
                    <TickerLink symbol={p.symbol} iconSize={24} />
                  </td>
                  <td>
                    <DirectionBadge direction={p.direction} />
                  </td>
                  <td className="tabular-nums">{formatMoney(p.entry_price)}</td>
                  <td className="tabular-nums">{p.shares}</td>
                  <td className="tabular-nums">{formatMoney(p.close_price)}</td>
                  <td className="text-muted">{p.close_reason ?? '—'}</td>
                  <td className={`tabular-nums ${p.realized_pnl && p.realized_pnl > 0 ? 'text-green' : p.realized_pnl && p.realized_pnl < 0 ? 'text-red' : ''}`}>
                    {p.realized_pnl !== null ? formatMoney(p.realized_pnl) : '—'}
                  </td>
                  <td className="tabular-nums">{p.realized_r !== null ? formatNumber(p.realized_r) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

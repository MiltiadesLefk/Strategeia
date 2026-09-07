import { Link } from 'react-router-dom';
import { useDashboardSummary } from '../api/hooks';
import { StatCard } from '../components/StatCard';
import { SignalBadge } from '../components/Badge';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct } from '../components/common';
import type { ApiError } from '../api/client';

export function DashboardPage() {
  const { data, isLoading, error } = useDashboardSummary();

  if (isLoading) return <LoadingSpinner label="Loading dashboard…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} />;
  if (!data) return null;

  const { stats, markets_scanned, potential_setups, top_setups, latest_trade_plan } = data;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h1 style={{ fontSize: 22 }}>{greeting}</h1>
        <div className="text-muted" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
          Your trading bot is ready — {markets_scanned} markets scanned, {potential_setups} potential setup{potential_setups === 1 ? '' : 's'} today
        </div>
      </div>

      <div className="grid stat-grid">
        <StatCard label="Total Return" value={formatPct(stats.total_return)} positive={stats.total_return > 0 ? true : stats.total_return < 0 ? false : null} />
        <StatCard label="Win Rate" value={`${formatNumber(stats.win_rate, 0)}%`} />
        <StatCard label="Total Trades" value={String(stats.total_trades)} />
        <StatCard label="Avg R:R" value={stats.avg_rr !== null ? `${formatNumber(stats.avg_rr)}:1` : '—'} />
        <StatCard label="Markets Scanned" value={String(markets_scanned)} />
        <StatCard label="Potential Setups" value={String(potential_setups)} />
        <StatCard label="Active Positions" value={String(stats.active_positions)} />
        <StatCard label="Portfolio Value" value={formatMoney(stats.portfolio_value)} />
      </div>

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h3>Top Setups Today</h3>
          <Link to="/scan" className="text-muted" style={{ fontSize: 13 }}>
            View All →
          </Link>
        </div>
        {top_setups.length === 0 ? (
          <EmptyState>No setups flagged in the current scan.</EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Price</th>
                <th>24h</th>
                <th>Signal</th>
              </tr>
            </thead>
            <tbody>
              {top_setups.map((r) => (
                <tr key={r.symbol}>
                  <td style={{ fontWeight: 600 }}>
                    <Link to={`/analysis?symbol=${r.symbol}`}>{r.symbol}</Link>
                  </td>
                  <td className="tabular-nums">{formatMoney(r.price)}</td>
                  <td className={`tabular-nums ${r.change_pct_24h >= 0 ? 'text-green' : 'text-red'}`}>{formatPct(r.change_pct_24h)}</td>
                  <td>
                    <SignalBadge signal={r.signal} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h3>Latest Trade Plan</h3>
          <Link to="/trade-plans" className="text-muted" style={{ fontSize: 13 }}>
            View All →
          </Link>
        </div>
        {!latest_trade_plan ? (
          <EmptyState>No trade plans generated yet — head to Trade Plans to create one.</EmptyState>
        ) : (
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                Symbol
              </div>
              <div style={{ fontWeight: 700 }}>{latest_trade_plan.symbol}</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                Entry
              </div>
              <div className="tabular-nums">{formatMoney(latest_trade_plan.entry)}</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                Stop
              </div>
              <div className="tabular-nums text-red">{formatMoney(latest_trade_plan.stop)}</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                Target 1
              </div>
              <div className="tabular-nums text-green">{formatMoney(latest_trade_plan.tp1)}</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                R:R
              </div>
              <div className="tabular-nums">{formatNumber(latest_trade_plan.rr1)}:1</div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                Confidence
              </div>
              <div className="tabular-nums">{latest_trade_plan.confidence_score}%</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

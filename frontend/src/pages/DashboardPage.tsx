import { useState } from 'react';
import { Link } from 'react-router-dom';
import { SleeveSwitcher, useSelectedSleeve } from '../components/SleeveSwitcher';
import { useDashboardSummary, useEquityCurve, useAnalysis } from '../api/hooks';
import { Sparkline } from '../components/Sparkline';
import { EquityCurveChart } from '../components/chart/EquityCurveChart';
import './dashboard.css';
import { SignalBadge, TrendBadge } from '../components/Badge';
import { CompanyIcon } from '../components/CompanyIcon';
import { TickerLink, tickerHref } from '../components/TickerLink';
import { RangeTabs } from '../components/RangeTabs';
import { DataFreshness } from '../components/DataFreshness';
import { Flash } from '../components/Flash';
import { CandlestickChart } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct, sampleSizeNote } from '../components/common';
import { supportResistanceLevels, isPotentialBreakout } from '../lib/priceLevels';
import type { ApiError } from '../api/client';

/* The old LiveClock lived here: a local wall clock next to a permanently
   green "Live Market Data" badge. The badge was unconditional markup, and
   the data behind it is the last daily close (cached up to 15 minutes), not
   a live tick. Replaced by MarketStatus, which reads real session state and
   says when the data actually arrived — see components/MarketStatus.tsx. */

function TopPickChart({ symbol }: { symbol: string }) {
  const [range, setRange] = useState('1mo');
  const { data, isLoading } = useAnalysis(symbol, range);

  const levels = data ? supportResistanceLevels({ support: data.support.slice(0, 1), resistance: data.resistance.slice(0, 1) }) : [];
  const breakout = !!data && isPotentialBreakout(data);

  return (
    <div className="card">
      <div className="panel-header">
        <span>Top pick chart</span>
        <RangeTabs value={range} onChange={setRange} />
      </div>
      <div className="dash-pick">
        <Link to={tickerHref(symbol)} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text)' }}>
          <CompanyIcon symbol={symbol} size={26} />
          <span style={{ fontWeight: 600 }}>{symbol}</span>
          {data && (
            <span className="mono text-muted">
              <Flash value={data.price} scope={symbol}>
                {formatMoney(data.price)}
              </Flash>
            </span>
          )}
        </Link>
        {breakout && <span className="chip chip-amber">Potential breakout</span>}
      </div>
      {isLoading || !data ? (
        <LoadingSpinner label={`Loading ${symbol}…`} />
      ) : (
        <CandlestickChart candles={data.candles} ema20Series={data.ema20_series} levels={levels} height={300} />
      )}
    </div>
  );
}

export function DashboardPage() {
  const [sleeve, selectSleeve] = useSelectedSleeve();
  const { data, isLoading, error, dataUpdatedAt, isFetching } = useDashboardSummary(sleeve);
  const { data: equity } = useEquityCurve(sleeve);

  if (isLoading) return <LoadingSpinner label="Loading dashboard…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} />;
  if (!data) return null;

  const { stats, markets_scanned, potential_setups, top_setups } = data;
  const equityTrend = equity?.map((p) => p.equity_value);
  const topPick = top_setups[0];
  const ret = stats.total_return;
  const retClass = ret > 0 ? 'delta-up' : ret < 0 ? 'delta-down' : 'chip';

  return (
    <div className="dash">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <div className="text-muted" style={{ fontSize: 13, marginTop: 2 }}>
            {markets_scanned} market{markets_scanned === 1 ? '' : 's'} scanned · {potential_setups} potential setup
            {potential_setups === 1 ? '' : 's'}
          </div>
        </div>
        <DataFreshness updatedAt={dataUpdatedAt} isFetching={isFetching} />
      </div>

      <SleeveSwitcher selected={sleeve} onSelect={selectSleeve} />

      <div className="dash-tiles">
        <div className="stat-tile">
          <div className="stat-tile-label">Portfolio value</div>
          <div className="stat-tile-value">{formatMoney(stats.portfolio_value)}</div>
          <div className="dash-tile-foot">
            <span className="stat-tile-sub" style={{ margin: 0 }}>
              from {formatMoney(stats.starting_cash)}
            </span>
            {equityTrend && equityTrend.length >= 2 && <Sparkline values={equityTrend} width={48} height={16} />}
          </div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Total return</div>
          <div className="stat-tile-value" style={{ color: ret > 0 ? 'var(--green)' : ret < 0 ? 'var(--red)' : undefined }}>
            {formatPct(ret)}
          </div>
          <div className="dash-tile-foot">
            <span className={retClass}>{formatMoney(stats.portfolio_value - stats.starting_cash)}</span>
          </div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Win rate</div>
          <div className="stat-tile-value">{formatNumber(stats.win_rate, 0)}%</div>
          <div className="stat-tile-sub">{sampleSizeNote(stats.total_trades)}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Total trades</div>
          <div className="stat-tile-value">{stats.total_trades}</div>
          <div className="stat-tile-sub">closed</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Active positions</div>
          <div className="stat-tile-value">{stats.active_positions}</div>
          <div className="stat-tile-sub">open holds</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Cash</div>
          <div className="stat-tile-value">{formatMoney(stats.current_cash)}</div>
          <div className="stat-tile-sub">uninvested</div>
        </div>
      </div>

      <div className="dash-main">
        <div className="dash-col">
          <div className="card">
            <div className="panel-header">
              <span>Portfolio equity curve</span>
              <span className="chip">Simulated</span>
            </div>
            {equity && equity.length >= 2 ? (
              <div className="dash-curve">
                <EquityCurveChart points={equity} />
              </div>
            ) : (
              <EmptyState>The equity curve builds up as the portfolio is marked to market.</EmptyState>
            )}
          </div>
          {topPick ? (
            <TopPickChart symbol={topPick.symbol} />
          ) : (
            <div className="card">
              <EmptyState>No top-ranked setup to chart yet.</EmptyState>
            </div>
          )}
        </div>

        <div className="dash-col">
          <div className="card card-flush">
            <div className="panel-header" style={{ padding: '10px 12px 8px', marginBottom: 0 }}>
              <span>Top setups today</span>
              <Link to="/scan" className="text-muted" style={{ fontSize: 12 }}>
                View all →
              </Link>
            </div>
            {top_setups.length === 0 ? (
              <EmptyState>No setups flagged in the current scan.</EmptyState>
            ) : (
              <table className="dash-table">
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
                        <TickerLink symbol={r.symbol} iconSize={22} />
                      </td>
                      <td className="mono">
                        <Flash value={r.price}>{formatMoney(r.price)}</Flash>
                      </td>
                      <td>
                        <span className={r.change_pct_24h >= 0 ? 'delta-up' : 'delta-down'}>{formatPct(r.change_pct_24h)}</span>
                      </td>
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
            <div className="panel-header">
              <span>Top pick</span>
              {topPick && <TrendBadge trend={topPick.trend} />}
            </div>
            {!topPick ? (
              <EmptyState>No scan data yet to summarize.</EmptyState>
            ) : (
              <div className="text-muted" style={{ fontSize: 13 }}>
                <Link to={tickerHref(topPick.symbol)}>{topPick.symbol}</Link> is showing {topPick.momentum.toLowerCase()} momentum on a{' '}
                {topPick.trend.toLowerCase()} trend. Scanner score <span className="mono">{topPick.score}/6</span>.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

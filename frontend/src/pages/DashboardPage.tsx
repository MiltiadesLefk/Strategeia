import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useDashboardSummary, useEquityCurve, useAnalysis } from '../api/hooks';
import { StatCard } from '../components/StatCard';
import { SignalBadge, TrendBadge } from '../components/Badge';
import { CompanyIcon } from '../components/CompanyIcon';
import { TickerLink, tickerHref } from '../components/TickerLink';
import { IconBadge } from '../components/IconBadge';
import { RangeTabs } from '../components/RangeTabs';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct } from '../components/common';
import type { ApiError } from '../api/client';

function LiveClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  const date = now.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  const time = now.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <span className="badge" style={{ background: 'var(--green-bg)', color: 'var(--green)' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
        Live Market Data
      </span>
      <span className="text-muted" style={{ fontSize: 13 }}>
        {date} · {time}
      </span>
    </div>
  );
}

function TopPickChart({ symbol }: { symbol: string }) {
  const [range, setRange] = useState('1mo');
  const { data, isLoading } = useAnalysis(symbol, range);

  const levels: PriceLevel[] = data
    ? [
        ...data.support.slice(0, 1).map((price) => ({ price, color: '#10b981', title: 'Support' })),
        ...data.resistance.slice(0, 1).map((price) => ({ price, color: '#ef4444', title: 'Resistance' })),
      ]
    : [];

  // Same 2% proximity threshold the backend's scanner uses to score "near a
  // key level" — narrated here as a breakout callout, mirrors AnalysisPage.
  const nearestResistance = data?.resistance[0];
  const nearestSupport = data?.support[0];
  const isPotentialBreakout =
    !!data &&
    ((data.trend === 'Bullish' && nearestResistance !== undefined && Math.abs(nearestResistance - data.price) / data.price <= 0.02) ||
      (data.trend === 'Bearish' && nearestSupport !== undefined && Math.abs(nearestSupport - data.price) / data.price <= 0.02));

  return (
    <div className="card" style={{ height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 10 }}>
        <Link to={tickerHref(symbol)} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text)' }}>
          <CompanyIcon symbol={symbol} size={28} />
          <div>
            <div style={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
              {symbol}
              {isPotentialBreakout && <span className="badge badge-amber">Potential Breakout</span>}
            </div>
            {data && (
              <div className="tabular-nums text-muted" style={{ fontSize: 12 }}>
                {formatMoney(data.price)}
              </div>
            )}
          </div>
        </Link>
        <RangeTabs value={range} onChange={setRange} />
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
  const { data, isLoading, error } = useDashboardSummary();
  const { data: equity } = useEquityCurve();

  if (isLoading) return <LoadingSpinner label="Loading dashboard…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} />;
  if (!data) return null;

  const { stats, markets_scanned, potential_setups, top_setups, latest_trade_plan, top_pick_trade_plan } = data;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const equityTrend = equity?.map((p) => p.equity_value);
  const topPick = top_setups[0];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h1 style={{ fontSize: 22 }}>{greeting}</h1>
          <div className="text-muted" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
            Your trading bot is ready — {markets_scanned} markets scanned, {potential_setups} potential setup{potential_setups === 1 ? '' : 's'} today
          </div>
        </div>
        <LiveClock />
      </div>

      <div className="grid stat-grid">
        <StatCard label="Total Return" value={formatPct(stats.total_return)} positive={stats.total_return > 0 ? true : stats.total_return < 0 ? false : null} trend={equityTrend} />
        <StatCard label="Win Rate" value={`${formatNumber(stats.win_rate, 0)}%`} />
        <StatCard label="Total Trades" value={String(stats.total_trades)} />
        <StatCard label="Avg R:R" value={stats.avg_rr !== null ? `${formatNumber(stats.avg_rr)}:1` : '—'} />
        <StatCard label="Markets Scanned" value={String(markets_scanned)} />
        <StatCard label="Potential Setups" value={String(potential_setups)} />
        <StatCard label="Active Positions" value={String(stats.active_positions)} />
        <StatCard label="Portfolio Value" value={formatMoney(stats.portfolio_value)} trend={equityTrend} />
      </div>

      <div className="split-row">
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
                      <TickerLink symbol={r.symbol} iconSize={26} />
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

        {topPick ? (
          <TopPickChart symbol={topPick.symbol} />
        ) : (
          <div className="card" style={{ height: '100%' }}>
            <EmptyState>No top-ranked setup to chart yet.</EmptyState>
          </div>
        )}
      </div>

      <div className="split-row">
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3>Latest AI Trade Plan</h3>
            <Link to="/trade-plans" className="text-muted" style={{ fontSize: 13 }}>
              View All →
            </Link>
          </div>
          {!latest_trade_plan ? (
            <EmptyState>No trade plans generated yet — head to Trade Plans to create one.</EmptyState>
          ) : latest_trade_plan.direction === null ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <TickerLink symbol={latest_trade_plan.symbol} iconSize={28} fontWeight={700} />
                <span className="badge badge-neutral">No Trade</span>
              </div>
              <div className="text-muted" style={{ fontSize: 13 }}>
                {latest_trade_plan.reason ?? 'Evaluated but did not clear the bar for a trade plan.'}
              </div>
            </>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                <TickerLink symbol={latest_trade_plan.symbol} iconSize={28} fontWeight={700} />
                <span className={`badge ${latest_trade_plan.direction === 'long' ? 'badge-green' : 'badge-red'}`}>
                  {latest_trade_plan.direction === 'long' ? 'Long' : 'Short'}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
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
            </>
          )}
        </div>

        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <h3>AI Insights</h3>
          </div>
          {!topPick && !top_pick_trade_plan ? (
            <EmptyState>No scan or trade-plan data yet to summarize.</EmptyState>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Every block below narrates topPick specifically — never a
                  different, unrelated symbol's stale plan — so this card
                  reads as one coherent take, not two mismatched ones. */}
              {topPick && (
                <div style={{ display: 'flex', gap: 12 }}>
                  <IconBadge variant={topPick.trend === 'Bullish' ? 'up' : topPick.trend === 'Bearish' ? 'down' : 'info'} size={34} />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                      Technical <TrendBadge trend={topPick.trend} />
                    </div>
                    <div className="text-muted" style={{ fontSize: 13, marginTop: 2 }}>
                      <Link to={tickerHref(topPick.symbol)}>{topPick.symbol}</Link> is showing {topPick.momentum.toLowerCase()} momentum on a{' '}
                      {topPick.trend.toLowerCase()} trend — scanner score {topPick.score}/6.
                    </div>
                  </div>
                </div>
              )}
              {top_pick_trade_plan && top_pick_trade_plan.direction === null && (
                <div style={{ display: 'flex', gap: 12 }}>
                  <IconBadge variant="info" size={34} />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>No Trade</div>
                    <div className="text-muted" style={{ fontSize: 13, marginTop: 2 }}>
                      {top_pick_trade_plan.reason ?? `${top_pick_trade_plan.symbol} was evaluated but didn't clear the bar for a trade plan.`}
                    </div>
                  </div>
                </div>
              )}
              {top_pick_trade_plan && top_pick_trade_plan.direction !== null && (
                <>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <IconBadge variant="info" size={34} />
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>
                        {top_pick_trade_plan.ai_provider && top_pick_trade_plan.ai_provider !== 'none'
                          ? `AI Take · ${top_pick_trade_plan.ai_provider}`
                          : 'Rule-based take'}
                      </div>
                      <div className="text-muted" style={{ fontSize: 13, marginTop: 2 }}>
                        {top_pick_trade_plan.ai_take_text}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <IconBadge variant={(top_pick_trade_plan.rr1 ?? 0) >= 2 ? 'up' : 'down'} size={34} />
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>Risk</div>
                      <div className="text-muted" style={{ fontSize: 13, marginTop: 2 }}>
                        {formatNumber(top_pick_trade_plan.rr1)}:1 risk:reward on{' '}
                        <Link to={tickerHref(top_pick_trade_plan.symbol)}>{top_pick_trade_plan.symbol}</Link>,{' '}
                        {top_pick_trade_plan.confidence_score}% confidence.
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

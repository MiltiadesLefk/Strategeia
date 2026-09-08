import { useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useResearch } from '../api/hooks';
import { SymbolPicker } from '../components/SymbolPicker';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { CompanyIcon } from '../components/CompanyIcon';
import { Tabs } from '../components/Tabs';
import { RevenueChart } from '../components/chart/RevenueChart';
import { ErrorBanner, LoadingSpinner, EmptyState, formatMoney, formatPct, formatRelativeTime, isSafeHttpUrl } from '../components/common';
import type { ApiError } from '../api/client';
import type { ResearchResponse } from '../api/types';

const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'financials', label: 'Financials' },
  { value: 'earnings', label: 'Earnings' },
  { value: 'news', label: 'News' },
  { value: 'catalysts', label: 'Catalysts' },
  { value: 'analysis', label: 'Analysis' },
];

// High-volatility-around-earnings threshold — earnings within 2 weeks
// warrants a callout, further out is just informational.
const HIGH_VOLATILITY_DAYS = 14;

function Range52WBar({ low, high, price }: { low: number | null; high: number | null; price: number }) {
  const hasRange = low !== null && high !== null && high > low;
  const pct = hasRange ? Math.min(100, Math.max(0, ((price - low!) / (high! - low!)) * 100)) : null;
  return (
    <div>
      <div className="tabular-nums">
        {formatMoney(low)} – {formatMoney(high)}
      </div>
      {pct !== null && (
        <div style={{ position: 'relative', height: 5, borderRadius: 999, background: 'var(--border)', marginTop: 8 }}>
          <div
            style={{
              position: 'absolute',
              left: `${pct}%`,
              top: -3,
              width: 11,
              height: 11,
              borderRadius: '50%',
              background: 'var(--indigo-light)',
              border: '2px solid var(--card)',
              transform: 'translateX(-50%)',
            }}
          />
        </div>
      )}
    </div>
  );
}

function CatalystsList({ catalysts }: { catalysts: string[] }) {
  if (catalysts.length === 0) return <EmptyState>No notable catalysts detected.</EmptyState>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {catalysts.map((c) => (
        <div key={c} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <span style={{ color: 'var(--green)', fontWeight: 700, flexShrink: 0, lineHeight: '20px' }}>✓</span>
          <span style={{ fontSize: 13, lineHeight: '20px' }}>{c}</span>
        </div>
      ))}
    </div>
  );
}

function NewsList({ symbol, news }: { symbol: string; news: ResearchResponse['news'] }) {
  if (news.length === 0) return <EmptyState>No recent news found.</EmptyState>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {news.map((n) => {
        const body = (
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <CompanyIcon symbol={symbol} size={36} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)' }}>{n.headline}</div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                {n.source} · {formatRelativeTime(n.published_at)}
              </div>
            </div>
          </div>
        );
        return isSafeHttpUrl(n.url) ? (
          <a key={n.url || n.headline} href={n.url} target="_blank" rel="noreferrer">
            {body}
          </a>
        ) : (
          <div key={n.headline}>{body}</div>
        );
      })}
    </div>
  );
}

function UpcomingEarningsCard({ data }: { data: ResearchResponse }) {
  if (!data.earnings_date) {
    return (
      <div className="card">
        <h3 style={{ marginBottom: 12 }}>Upcoming Earnings</h3>
        <EmptyState>No confirmed upcoming earnings date.</EmptyState>
      </div>
    );
  }
  const hasEstimates = data.earnings_eps_estimate !== null || data.earnings_revenue_estimate !== null;
  return (
    <div className="card">
      <h3 style={{ marginBottom: 12 }}>Upcoming Earnings</h3>
      <div style={{ fontWeight: 700, fontSize: 15 }}>{data.earnings_fiscal_label ?? 'Next Earnings'}</div>
      <div className="text-muted" style={{ fontSize: 13, marginBottom: 12 }}>
        {new Date(data.earnings_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
      </div>
      {hasEstimates && (
        <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <div className="text-muted" style={{ fontSize: 11 }}>
              Est. EPS
            </div>
            <div className="tabular-nums" style={{ fontWeight: 600 }}>
              {data.earnings_eps_estimate !== null ? `$${data.earnings_eps_estimate.toFixed(2)}` : '—'}
            </div>
          </div>
          <div>
            <div className="text-muted" style={{ fontSize: 11 }}>
              Est. Revenue
            </div>
            <div className="tabular-nums" style={{ fontWeight: 600 }}>
              {formatMoney(data.earnings_revenue_estimate, { compact: true })}
            </div>
          </div>
        </div>
      )}
      {data.earnings_days_until !== null && (
        <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontWeight: 600, color: 'var(--indigo-light)', fontSize: 13 }}>
            Earnings in {data.earnings_days_until} day{data.earnings_days_until === 1 ? '' : 's'}
          </div>
          {data.earnings_days_until <= HIGH_VOLATILITY_DAYS && (
            <div className="text-muted" style={{ fontSize: 12, marginTop: 2 }}>
              High volatility expected.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ResearchPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol') || 'AAPL';
  const [tab, setTab] = useState('overview');
  const { data, isLoading, error } = useResearch(symbol);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ fontSize: 22 }}>Research</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <CompanyDropdown value={symbol} onChange={(s) => setParams({ symbol: s })} />
          <SymbolPicker value={symbol} onChange={(s) => setParams({ symbol: s })} />
        </div>
      </div>

      {isLoading && <LoadingSpinner label={`Loading ${symbol}…`} />}
      {error && <ErrorBanner message={(error as ApiError).message} />}

      {data && (
        <>
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <CompanyIcon symbol={data.symbol} size={36} />
                <div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{data.name}</div>
                  <div className="text-muted">{data.symbol}</div>
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="tabular-nums" style={{ fontSize: 20, fontWeight: 700 }}>
                  {formatMoney(data.price)}
                </div>
                {data.change !== null && data.change_pct !== null && (
                  <div className={`tabular-nums ${data.change >= 0 ? 'text-green' : 'text-red'}`} style={{ fontSize: 13, fontWeight: 600 }}>
                    {data.change >= 0 ? '+' : ''}
                    {data.change.toFixed(2)} ({formatPct(data.change_pct)})
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                <span className="badge badge-green">● AI Analysis</span>
                <span className="text-muted" style={{ fontSize: 11 }}>
                  Updated {formatRelativeTime(data.generated_at)}
                </span>
              </div>
            </div>
            <div style={{ marginTop: 16 }}>
              <Tabs tabs={TABS} value={tab} onChange={setTab} />
            </div>
          </div>

          {tab === 'overview' && (
            <>
              <div className="grid stat-grid">
                <div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    Market Cap
                  </div>
                  <div className="tabular-nums" style={{ fontWeight: 600 }}>
                    {formatMoney(data.market_cap, { compact: true })}
                  </div>
                </div>
                <div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    P/E Ratio
                  </div>
                  <div className="tabular-nums" style={{ fontWeight: 600 }}>
                    {data.pe_ratio?.toFixed(1) ?? '—'}
                  </div>
                </div>
                <div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    Revenue (TTM)
                  </div>
                  <div className="tabular-nums" style={{ fontWeight: 600 }}>
                    {formatMoney(data.revenue_ttm, { compact: true })}
                  </div>
                  {data.revenue_yoy_pct !== null && (
                    <div className={`tabular-nums ${data.revenue_yoy_pct >= 0 ? 'text-green' : 'text-red'}`} style={{ fontSize: 11 }}>
                      {formatPct(data.revenue_yoy_pct)} YoY
                    </div>
                  )}
                </div>
                <div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    EPS (TTM)
                  </div>
                  <div className="tabular-nums" style={{ fontWeight: 600 }}>
                    {data.eps_ttm?.toFixed(2) ?? '—'}
                  </div>
                </div>
                <div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    52 Week Range
                  </div>
                  <Range52WBar low={data.week52_low} high={data.week52_high} price={data.price} />
                </div>
              </div>

              <div className="split-row">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                  <div className="card">
                    <h3 style={{ marginBottom: 12 }}>Key Financials</h3>
                    {data.financials.length === 0 ? <EmptyState>No financial history available.</EmptyState> : <RevenueChart years={data.financials} />}
                  </div>
                  <div className="card">
                    <h3 style={{ marginBottom: 12 }}>Recent News</h3>
                    <NewsList symbol={data.symbol} news={data.news} />
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                  <UpcomingEarningsCard data={data} />
                  <div className="card">
                    <h3 style={{ marginBottom: 12 }}>Key Catalysts</h3>
                    <CatalystsList catalysts={data.catalysts} />
                  </div>
                  <div className="card" style={{ background: 'var(--canvas)' }}>
                    <div className="text-muted" style={{ fontSize: 12, marginBottom: 6 }}>
                      AI Summary {data.ai_provider !== 'none' ? `· ${data.ai_provider}` : '· rule-based'}
                    </div>
                    <div>{data.ai_summary}</div>
                  </div>
                </div>
              </div>
            </>
          )}

          {tab === 'financials' && (
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Key Financials</h3>
              {data.financials.length === 0 ? (
                <EmptyState>No financial history available.</EmptyState>
              ) : (
                <>
                  <RevenueChart years={data.financials} />
                  <table style={{ marginTop: 20 }}>
                    <thead>
                      <tr>
                        <th>Year</th>
                        <th>Revenue</th>
                        <th>Net Income</th>
                        <th>Net Margin</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...data.financials].reverse().map((y) => (
                        <tr key={y.year}>
                          <td style={{ fontWeight: 600 }}>{y.year}</td>
                          <td className="tabular-nums">{formatMoney(y.revenue, { compact: true })}</td>
                          <td className="tabular-nums">{formatMoney(y.net_income, { compact: true })}</td>
                          <td className="tabular-nums">{y.revenue ? formatPct((y.net_income / y.revenue) * 100) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          )}

          {tab === 'earnings' && (
            <div className="split-row">
              <UpcomingEarningsCard data={data} />
              <div className="card">
                <h3 style={{ marginBottom: 8 }}>About These Estimates</h3>
                <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
                  Earnings date and estimates come directly from the configured market-data provider — nothing here is
                  modeled or predicted by the AI layer. If no estimate is shown, the provider didn't publish one for
                  this symbol's next report.
                </div>
              </div>
            </div>
          )}

          {tab === 'news' && (
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Recent News</h3>
              <NewsList symbol={data.symbol} news={data.news} />
            </div>
          )}

          {tab === 'catalysts' && (
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Key Catalysts</h3>
              <CatalystsList catalysts={data.catalysts} />
            </div>
          )}

          {tab === 'analysis' && (
            <div className="card" style={{ background: 'var(--canvas)' }}>
              <div className="text-muted" style={{ fontSize: 12, marginBottom: 6 }}>
                AI Summary {data.ai_provider !== 'none' ? `· ${data.ai_provider}` : '· rule-based'}
              </div>
              <div style={{ marginBottom: 16 }}>{data.ai_summary}</div>
              {data.ai_error && (
                <div className="text-muted" style={{ fontSize: 12, marginBottom: 16 }}>
                  ({data.ai_error})
                </div>
              )}
              <Link to={`/analysis?symbol=${data.symbol}`} className="btn btn-secondary">
                Open Full Chart Analysis →
              </Link>
            </div>
          )}
        </>
      )}
    </div>
  );
}

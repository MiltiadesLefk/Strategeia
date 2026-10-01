import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAnalysis, useArchive, useResearch, useUniverse } from '../api/hooks';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { RangeTabs } from '../components/RangeTabs';
import { Tabs } from '../components/Tabs';
import { TrendBadge } from '../components/Badge';
import { CompanyIcon } from '../components/CompanyIcon';
import { CandlestickChart } from '../components/chart/CandlestickChart';
import { RevenueChart } from '../components/chart/RevenueChart';
import { AiNoteCard, ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct, formatRelativeTime, isSafeHttpUrl } from '../components/common';
import { supportResistanceLevels, isPotentialBreakout } from '../lib/priceLevels';
import type { ApiError } from '../api/client';
import type { ResearchResponse } from '../api/types';

const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'financials', label: 'Financials' },
  { value: 'earnings', label: 'Earnings' },
  { value: 'news', label: 'News' },
  { value: 'catalysts', label: 'Catalysts' },
];

// High-volatility-around-earnings threshold — earnings within 2 weeks
// warrants a callout, further out is just informational.
const HIGH_VOLATILITY_DAYS = 14;

// Chart height for this page's main, "one ticker full analysis" chart —
// deliberately much taller than the compact previews used elsewhere
// (Dashboard's top-pick card, a Trade Plan card's mini chart) since this is
// the one place a user comes specifically to read the chart closely.
const MAIN_CHART_HEIGHT = 560;

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

// The dated archive's own account of itself: how much of this symbol's news
// and fundamentals we have saved, and since when. Mounted only after the
// research data has loaded, because loading it is what saves the latest items.
function ArchivePanel({ symbol }: { symbol: string }) {
  const { data, isLoading, error } = useArchive(symbol);
  if (isLoading) return null;
  // The panel is a footnote; if it can't load, the page is better without it
  // than with an error banner about something optional.
  if (error || !data) return null;
  const since = data.first_archived_at
    ? new Date(data.first_archived_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    : null;
  return (
    <div className="card" data-testid="archive-panel">
      <h3 style={{ marginBottom: 8 }}>Saved history</h3>
      {since ? (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <span className="tabular-nums" style={{ fontWeight: 600 }}>
            {data.news_count} news {data.news_count === 1 ? 'item' : 'items'}
          </span>{' '}
          and{' '}
          <span className="tabular-nums" style={{ fontWeight: 600 }}>
            {data.fundamentals_count} fundamentals {data.fundamentals_count === 1 ? 'snapshot' : 'snapshots'}
          </span>{' '}
          saved for {symbol} since {since}.
        </div>
      ) : (
        <div className="text-muted" style={{ fontSize: 13 }}>
          Nothing saved for {symbol} yet. News and fundamentals are saved the first time they are loaded.
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 12, lineHeight: 1.6, marginTop: 8 }}>
        Free data sources only show today&apos;s news and fundamentals, so the app saves what it loads, with the time it
        became public. A backtest can only use news from the day this archive began ({data.totals.symbols}{' '}
        {data.totals.symbols === 1 ? 'symbol' : 'symbols'} so far); earlier days have no recorded news.
      </div>
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

function ChartSection({ symbol, companyName, range, setRange }: { symbol: string; companyName: string | undefined; range: string; setRange: (r: string) => void }) {
  const { data, isLoading, error, refetch } = useAnalysis(symbol, range);
  const levels = data ? supportResistanceLevels(data) : [];
  const breakout = !!data && isPotentialBreakout(data);

  return (
    <>
      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <CompanyIcon symbol={symbol} size={36} />
          <div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>
              {symbol}
              {companyName && <span className="text-muted" style={{ fontWeight: 400, fontSize: 14 }}> · {companyName}</span>}
            </div>
            <div className="tabular-nums" style={{ fontSize: 15 }}>
              {data ? formatMoney(data.price) : '—'}
            </div>
          </div>
        </div>
        {data && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {breakout && <span className="badge badge-amber">Potential Breakout</span>}
            <TrendBadge trend={data.trend} />
          </div>
        )}
      </div>

      {isLoading && <LoadingSpinner label={`Loading ${symbol}…`} />}
      {error && <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />}

      {data && (
        <>
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
              <RangeTabs value={range} onChange={setRange} />
            </div>
            <CandlestickChart
              candles={data.candles}
              ema20Series={data.ema20_series}
              ema50Series={data.ema50_series}
              bollingerUpperSeries={data.bollinger_upper_series}
              bollingerLowerSeries={data.bollinger_lower_series}
              vwapSeries={data.vwap_series}
              levels={levels}
              height={MAIN_CHART_HEIGHT}
            />
          </div>

          <div className="grid stat-grid">
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                Trend
              </div>
              <div style={{ fontWeight: 700, color: data.trend === 'Bullish' ? 'var(--green)' : data.trend === 'Bearish' ? 'var(--red)' : undefined }}>
                {data.trend}
              </div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                Momentum
              </div>
              <div style={{ fontWeight: 700 }}>{data.momentum}</div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                RSI(14)
              </div>
              <div className="tabular-nums" style={{ fontWeight: 700 }}>
                {formatNumber(data.rsi14, 0)}
              </div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                EMA20 / EMA50
              </div>
              <div className="tabular-nums" style={{ fontWeight: 700 }}>
                {formatMoney(data.ema20)} / {formatMoney(data.ema50)}
              </div>
            </div>
            {/* ATR as a share of price is the comparable form: "this name
                moves 2.4% on an average day" is what tells you whether a
                given stop distance is structure or noise. It's the same
                number the trade planner floors its stops against. */}
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                ATR(14)
              </div>
              <div className="tabular-nums" style={{ fontWeight: 700 }}>
                {data.atr14 != null ? formatMoney(data.atr14) : '—'}
                {data.atr_pct != null && (
                  <span className="text-muted" style={{ fontSize: 12, fontWeight: 500 }}>
                    {' '}
                    · {formatNumber(data.atr_pct, 1)}%/day
                  </span>
                )}
              </div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                MACD
              </div>
              <div
                className="tabular-nums"
                style={{
                  fontWeight: 700,
                  color:
                    data.macd != null && data.macd_signal != null
                      ? data.macd > data.macd_signal
                        ? 'var(--green)'
                        : 'var(--red)'
                      : undefined,
                }}
              >
                {data.macd != null ? formatNumber(data.macd) : '—'}
                {data.macd_signal != null && (
                  <span className="text-muted" style={{ fontSize: 12, fontWeight: 500 }}>
                    {' '}
                    sig {formatNumber(data.macd_signal)}
                  </span>
                )}
              </div>
            </div>
          </div>

          <AiNoteCard label="AI Insight" provider={data.ai_provider} text={data.insight_text} error={data.ai_error} />
        </>
      )}
    </>
  );
}

function ResearchSection({ symbol, tab, setTab }: { symbol: string; tab: string; setTab: (t: string) => void }) {
  const { data, isLoading, error, refetch } = useResearch(symbol);
  const tabsRef = useRef<HTMLDivElement>(null);

  // Switching to a shorter tab (Earnings/News/Catalysts vs. the taller
  // Overview/Financials) shrinks the page below wherever the user had
  // scrolled to, so the browser clamps the scroll position — which reads as
  // "the whole page jumped to the top," past the pinned chart above. Anchor
  // deliberately to the tab bar instead, so a tab click always lands
  // predictably on that tab's content rather than an accidental clamp.
  function handleTabChange(next: string) {
    setTab(next);
    tabsRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  return (
    <>
      <div className="card" ref={tabsRef}>
        <Tabs tabs={TABS} value={tab} onChange={handleTabChange} />
      </div>

      {isLoading && <LoadingSpinner label="Loading fundamentals…" />}
      {error && <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />}

      {data && (
        <>
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
                  <AiNoteCard label="AI Summary" provider={data.ai_provider} text={data.ai_summary} error={data.ai_error} />
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

          {tab === 'news' && <ArchivePanel symbol={data.symbol} />}

          {tab === 'catalysts' && (
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Key Catalysts</h3>
              <CatalystsList catalysts={data.catalysts} />
            </div>
          )}
        </>
      )}
    </>
  );
}

export function AnalysisPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol');
  const [range, setRange] = useState('3mo');
  const [tab, setTab] = useState('overview');
  const { data: universe } = useUniverse();
  const companyName = universe?.find((e) => e.symbol === symbol)?.name;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ fontSize: 22 }}>Analysis</h1>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <CompanyDropdown value={symbol ?? ''} onChange={(s) => setParams({ symbol: s })} />
          {symbol && (
            <Link to={`/trade-plans?symbol=${symbol}`} className="btn btn-secondary">
              Generate Trade Plan →
            </Link>
          )}
        </div>
      </div>

      {!symbol && <EmptyState>Choose a company above to see its full chart, fundamentals, and news.</EmptyState>}

      {symbol && (
        <>
          <ChartSection symbol={symbol} companyName={companyName} range={range} setRange={setRange} />
          <ResearchSection symbol={symbol} tab={tab} setTab={setTab} />
        </>
      )}
    </div>
  );
}

import { useSearchParams } from 'react-router-dom';
import { useResearch } from '../api/hooks';
import { SymbolPicker } from '../components/SymbolPicker';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { CompanyIcon } from '../components/CompanyIcon';
import { RevenueChart } from '../components/chart/RevenueChart';
import { ErrorBanner, LoadingSpinner, EmptyState, formatMoney, isSafeHttpUrl } from '../components/common';
import type { ApiError } from '../api/client';

export function ResearchPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol') || 'AAPL';
  const { data, isLoading, error } = useResearch(symbol);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
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
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <CompanyIcon symbol={data.symbol} size={36} />
                <div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{data.name}</div>
                  <div className="text-muted">{data.symbol}</div>
                </div>
              </div>
              <div className="tabular-nums" style={{ fontSize: 20, fontWeight: 700 }}>
                {formatMoney(data.price)}
              </div>
            </div>
            <div className="grid stat-grid" style={{ marginTop: 16 }}>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Market Cap
                </div>
                <div className="tabular-nums">{formatMoney(data.market_cap, { compact: true })}</div>
              </div>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  P/E Ratio
                </div>
                <div className="tabular-nums">{data.pe_ratio?.toFixed(1) ?? '—'}</div>
              </div>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Revenue (TTM)
                </div>
                <div className="tabular-nums">{formatMoney(data.revenue_ttm, { compact: true })}</div>
              </div>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  EPS (TTM)
                </div>
                <div className="tabular-nums">{data.eps_ttm?.toFixed(2) ?? '—'}</div>
              </div>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  52-Week Range
                </div>
                <div className="tabular-nums">
                  {formatMoney(data.week52_low)} – {formatMoney(data.week52_high)}
                </div>
              </div>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Earnings
                </div>
                <div className="tabular-nums">{data.earnings_date ?? '—'}</div>
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 20 }}>
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Key Financials</h3>
              {data.financials.length === 0 ? <EmptyState>No financial history available.</EmptyState> : <RevenueChart years={data.financials} />}
            </div>
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Key Catalysts</h3>
              {data.catalysts.length === 0 ? (
                <EmptyState>No notable catalysts detected.</EmptyState>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {data.catalysts.map((c) => (
                    <li key={c} style={{ marginBottom: 8 }}>
                      {c}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="card">
            <h3 style={{ marginBottom: 12 }}>Recent News</h3>
            {data.news.length === 0 ? (
              <EmptyState>No recent news found.</EmptyState>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {data.news.map((n) => {
                  const body = (
                    <>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>{n.headline}</div>
                      <div className="text-muted" style={{ fontSize: 12 }}>
                        {n.source} · {new Date(n.published_at).toLocaleString()}
                      </div>
                    </>
                  );
                  return isSafeHttpUrl(n.url) ? (
                    <a key={n.url || n.headline} href={n.url} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>
                      {body}
                    </a>
                  ) : (
                    <div key={n.headline}>{body}</div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="card" style={{ background: 'var(--canvas)' }}>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 6 }}>
              AI Summary {data.ai_provider !== 'none' ? `· ${data.ai_provider}` : '· rule-based'}
            </div>
            <div>{data.ai_summary}</div>
          </div>
        </>
      )}
    </div>
  );
}

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useScan } from '../api/hooks';
import { SignalBadge, TrendBadge } from '../components/Badge';
import { Sparkline } from '../components/Sparkline';
import { ErrorBanner, LoadingSpinner, formatMoney, formatPct } from '../components/common';
import type { ApiError } from '../api/client';

export function MarketScanPage() {
  const [symbolsFilter, setSymbolsFilter] = useState<string | undefined>(undefined);
  const [draft, setDraft] = useState('');
  const { data, isLoading, error, refetch, isFetching } = useScan(symbolsFilter);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: 22, marginBottom: 4 }}>Market Scanner</h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }} className="text-muted">
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: isFetching ? 'var(--amber)' : 'var(--green)',
                display: 'inline-block',
              }}
            />
            {isFetching ? 'Scanning markets…' : `${data?.results.length ?? 0} symbols scanned`}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            type="text"
            placeholder="Custom symbols, e.g. AAPL,NVDA,TSLA"
            value={draft}
            onChange={(e) => setDraft(e.target.value.toUpperCase())}
            style={{ width: 260 }}
          />
          <button className="btn btn-secondary" onClick={() => setSymbolsFilter(draft || undefined)}>
            Filter
          </button>
          <button className="btn btn-primary" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? 'Scanning…' : 'Rescan'}
          </button>
        </div>
      </div>

      {isLoading && <LoadingSpinner label="Scanning markets…" />}
      {error && <ErrorBanner message={(error as ApiError).message} />}

      {data && (
        <div className="card">
          {data.errors.length > 0 && (
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
              {data.errors.length} symbol(s) failed to load: {data.errors.slice(0, 3).join('; ')}
              {data.errors.length > 3 ? '…' : ''}
            </div>
          )}
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Price</th>
                <th>24h</th>
                <th>Trend</th>
                <th>Momentum</th>
                <th>Score</th>
                <th>AI Signal</th>
                <th>Chart</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.results
                .slice()
                .sort((a, b) => b.score - a.score)
                .map((r) => (
                  <tr key={r.symbol}>
                    <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                    <td className="tabular-nums">{formatMoney(r.price)}</td>
                    <td className={`tabular-nums ${r.change_pct_24h >= 0 ? 'text-green' : 'text-red'}`}>{formatPct(r.change_pct_24h)}</td>
                    <td>
                      <TrendBadge trend={r.trend} />
                    </td>
                    <td className="text-muted">{r.momentum}</td>
                    <td className="tabular-nums">{r.score}</td>
                    <td>
                      <SignalBadge signal={r.signal} />
                    </td>
                    <td>
                      <Sparkline values={r.sparkline} />
                    </td>
                    <td>
                      <Link to={`/analysis?symbol=${r.symbol}`} className="text-muted" style={{ fontSize: 13 }}>
                        Analyze →
                      </Link>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

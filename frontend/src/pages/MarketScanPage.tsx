import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useRunAutoScanNow, useScan } from '../api/hooks';
import { SignalBadge, TrendBadge } from '../components/Badge';
import { TickerLink } from '../components/TickerLink';
import { Sparkline } from '../components/Sparkline';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatPct } from '../components/common';
import type { ApiError } from '../api/client';
import type { ScanResult } from '../api/types';

const WATCHLIST_KEY = 'strategeia.watchlist';
const TABS = [
  { id: 'top', label: 'Top Setups' },
  { id: 'all', label: 'All Stocks' },
  { id: 'watchlist', label: 'Watchlist' },
] as const;
type Tab = (typeof TABS)[number]['id'];

function loadWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function StarButton({ active, onClick }: { active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-label={active ? 'Remove from watchlist' : 'Add to watchlist'}
      style={{ background: 'none', border: 'none', padding: 4, display: 'flex', color: active ? 'var(--amber)' : 'var(--text-muted)' }}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill={active ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="m12 3 2.6 5.9 6.4.6-4.8 4.3 1.4 6.3L12 16.9 6.4 20.1l1.4-6.3-4.8-4.3 6.4-.6L12 3Z"
        />
      </svg>
    </button>
  );
}

export function MarketScanPage() {
  const [symbolsFilter, setSymbolsFilter] = useState<string | undefined>(undefined);
  const [draft, setDraft] = useState('');
  const [tab, setTab] = useState<Tab>('top');
  const [watchlist, setWatchlist] = useState<string[]>(() => loadWatchlist());
  const { data, isLoading, error, refetch, isFetching } = useScan(symbolsFilter);
  const { mutate: runAutoScanNow, isPending: autoTrading, data: autoTradeResult, error: autoTradeError } = useRunAutoScanNow();

  useEffect(() => {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchlist));
  }, [watchlist]);

  const toggleWatch = (symbol: string) => {
    setWatchlist((prev) => (prev.includes(symbol) ? prev.filter((s) => s !== symbol) : [...prev, symbol]));
  };

  const rows: ScanResult[] = useMemo(() => {
    if (!data) return [];
    const sorted = data.results.slice().sort((a, b) => b.score - a.score);
    if (tab === 'top') return sorted.filter((r) => r.signal !== 'no_signal');
    if (tab === 'watchlist') return sorted.filter((r) => watchlist.includes(r.symbol));
    return sorted;
  }, [data, tab, watchlist]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="text-muted">
              <circle cx="11" cy="11" r="7" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <h1 style={{ fontSize: 22 }}>Market Scanner</h1>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginTop: 4 }} className="text-muted">
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
          <button
            className="btn btn-secondary"
            onClick={() => runAutoScanNow()}
            disabled={autoTrading}
            title="Scan, then generate (and auto-execute, if enabled in Settings) a trade plan for every potential setup found"
          >
            {autoTrading ? 'Scanning & Trading…' : 'Scan & Auto-Trade Now'}
          </button>
        </div>
      </div>

      {autoTradeError && <ErrorBanner message={(autoTradeError as ApiError).message} />}
      {autoTradeResult && (
        <div className="text-muted" style={{ fontSize: 13 }}>
          {autoTradeResult.generated.length === 0
            ? 'Auto-trade found no new potential setups (or the open-position cap was already reached).'
            : `Auto-trade generated plans for: ${autoTradeResult.generated.join(', ')}. See Trade Plans for details.`}
        </div>
      )}

      <div style={{ display: 'inline-flex', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 4, gap: 2, width: 'fit-content' }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className="btn"
            style={{
              padding: '7px 16px',
              background: tab === t.id ? 'var(--card-alt)' : 'transparent',
              color: tab === t.id ? 'var(--text)' : 'var(--text-muted)',
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            {t.label}
            {t.id === 'watchlist' && watchlist.length > 0 ? ` (${watchlist.length})` : ''}
          </button>
        ))}
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
          {rows.length === 0 ? (
            <EmptyState>
              {tab === 'watchlist' ? 'No symbols pinned yet — click the star on any row to add one.' : 'No symbols match this view.'}
            </EmptyState>
          ) : (
            <table>
              <thead>
                <tr>
                  <th />
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
                {rows.map((r) => (
                  <tr key={r.symbol}>
                    <td style={{ width: 28 }}>
                      <StarButton active={watchlist.includes(r.symbol)} onClick={() => toggleWatch(r.symbol)} />
                    </td>
                    <td style={{ fontWeight: 600 }}>
                      <TickerLink symbol={r.symbol} iconSize={26} />
                    </td>
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
          )}
        </div>
      )}
    </div>
  );
}

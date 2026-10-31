import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useLatestRatings, useRunAutoScanNow, useScan, useUniverse } from '../api/hooks';
import { SignalBadge, TrendBadge } from '../components/Badge';
import { TickerLink } from '../components/TickerLink';
import { Sparkline } from '../components/Sparkline';
import { DataFreshness } from '../components/DataFreshness';
import { Flash } from '../components/Flash';
import { PresetMatchChips, ScreenPresetBar, useActivePreset } from '../components/ScreenPresetBar';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatPct } from '../components/common';
import type { ApiError } from '../api/client';
import { Tabs } from '../components/Tabs';
import type { ScanResult } from '../api/types';
import './scan.css';

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
  const [tab, setTab] = useState<Tab>('top');
  const ratingBySymbol = useLatestRatings().data ?? {};
  const [screenPreset, setScreenPreset] = useState<string | null>(null);
  const [watchlist, setWatchlist] = useState<string[]>(() => loadWatchlist());
  const { data: universe } = useUniverse();
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useScan(symbolsFilter);
  const { run: presetRun, matchBySymbol: presetMatches } = useActivePreset(screenPreset);
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
    // An active screen replaces the tab filter: it picks from every scanned symbol, not just one tab's.
    if (screenPreset) return presetRun.data ? sorted.filter((r) => presetMatches.has(r.symbol)) : [];
    if (tab === 'top') return sorted.filter((r) => r.signal !== 'no_signal');
    if (tab === 'watchlist') return sorted.filter((r) => watchlist.includes(r.symbol));
    return sorted;
  }, [data, tab, watchlist, screenPreset, presetRun.data, presetMatches]);

  const total = data?.results.length ?? 0;
  const setups = data?.results.filter((r) => r.signal === 'potential_setup').length ?? 0;
  const watching = data?.results.filter((r) => r.signal === 'watching').length ?? 0;
  const up = data?.results.filter((r) => r.change_pct_24h >= 0).length ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="page-header">
        <div>
          <h1 className="page-title">Market Scanner</h1>
          <div className="label-mono" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
            <span
              style={{ width: 7, height: 7, borderRadius: '50%', background: isFetching ? 'var(--amber)' : 'var(--green)', display: 'inline-block' }}
            />
            {isFetching ? 'Scanning…' : `${total} symbols scanned`}
            <span>·</span>
            <DataFreshness updatedAt={dataUpdatedAt} />
          </div>
        </div>
        <div className="scan-controls">
          <select
            value={symbolsFilter ?? ''}
            onChange={(e) => setSymbolsFilter(e.target.value || undefined)}
            title="Filter the scan to one symbol, or show all"
          >
            <option value="">All symbols</option>
            {universe?.map((entry) => (
              <option key={entry.symbol} value={entry.symbol}>
                {entry.symbol} — {entry.name}
              </option>
            ))}
          </select>
          <button className="btn btn-secondary" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? 'Scanning…' : 'Rescan'}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => runAutoScanNow()}
            disabled={autoTrading}
            title="Run the hold scan now: every watchlist stock goes through the quality gate and, if auto-execute is on in Settings, passing stocks are bought as simulated hold positions"
          >
            {autoTrading ? 'Running hold scan…' : 'Run hold scan now'}
          </button>
        </div>
      </div>

      {autoTradeError && <ErrorBanner message={(autoTradeError as ApiError).message} />}
      {autoTradeResult && (
        <div className="text-muted" style={{ fontSize: 13 }}>
          {autoTradeResult.generated.length === 0
            ? 'Hold scan finished: nothing bought this run.'
            : `Hold scan bought: ${autoTradeResult.generated.join(', ')}.`}
        </div>
      )}

      {data && (
        <div className="scan-tiles">
          <div className="stat-tile">
            <div className="stat-tile-label">Symbols scanned</div>
            <div className="stat-tile-value">{total}</div>
          </div>
          <div className="stat-tile">
            <div className="stat-tile-label">Potential setups</div>
            <div className="stat-tile-value">{setups}</div>
            <div className="scan-bar"><i style={{ width: `${total ? (setups / total) * 100 : 0}%` }} /></div>
          </div>
          <div className="stat-tile">
            <div className="stat-tile-label">Watching</div>
            <div className="stat-tile-value">{watching}</div>
          </div>
          <div className="stat-tile">
            <div className="stat-tile-label">Up on the day</div>
            <div className="stat-tile-value">{up}</div>
            <div className="scan-bar"><i style={{ width: `${total ? (up / total) * 100 : 0}%`, background: 'var(--green)' }} /></div>
          </div>
        </div>
      )}

      <Tabs
        tabs={TABS.map((t) => ({ value: t.id, label: `${t.label}${t.id === 'watchlist' && watchlist.length > 0 ? ` (${watchlist.length})` : ''}` }))}
        value={tab}
        onChange={(v) => setTab(v as Tab)}
      />

      <ScreenPresetBar
        selected={screenPreset}
        onSelect={setScreenPreset}
        run={presetRun.data}
        isLoading={presetRun.isFetching}
        error={presetRun.error}
      />

      {isLoading && <LoadingSpinner label="Scanning markets…" />}
      {error && <ErrorBanner message={(error as ApiError).message} />}

      {data && (
        <div className="card card-flush">
          {data.errors.length > 0 && (
            <div className="text-muted" style={{ fontSize: 12, padding: 12 }}>
              {data.errors.length} symbol(s) failed to load: {data.errors.slice(0, 3).join('; ')}
              {data.errors.length > 3 ? '…' : ''}
            </div>
          )}
          {rows.length === 0 ? (
            <EmptyState>
              {screenPreset
                ? presetRun.isFetching
                  ? 'Screening…'
                  : `No scanned symbol matches this screen among the ${presetRun.data?.checked ?? 0} checked.`
                : tab === 'watchlist' ? 'No symbols pinned yet — click the star on any row to add one.' : 'No symbols match this view.'}
            </EmptyState>
          ) : (
            <div className="scan-table"><table>
              <thead>
                <tr>
                  <th />
                  <th>Symbol</th>
                  <th>Price</th>
                  <th>1D</th>
                  <th>Trend</th>
                  <th>Momentum</th>
                  <th>Score /6</th>
                  <th>Rating %</th>
                  <th>Signal</th>
                  <th>Chart</th>
                  {screenPreset && <th>Matched criteria</th>}
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
                    <td className="tabular-nums">
                      <Flash value={r.price} scope={symbolsFilter ?? 'all'}>
                        {formatMoney(r.price)}
                      </Flash>
                    </td>
                    <td className={`tabular-nums ${r.change_pct_24h >= 0 ? 'text-green' : 'text-red'}`}>{formatPct(r.change_pct_24h)}</td>
                    <td>
                      <TrendBadge trend={r.trend} />
                    </td>
                    <td className="text-muted">{r.momentum}</td>
                    <td className="mono">{r.score}</td>
                    <td className="tabular-nums">{ratingBySymbol[r.symbol] ? `${ratingBySymbol[r.symbol].percent}%` : '—'}</td>
                    <td>
                      <SignalBadge signal={r.signal} />
                    </td>
                    <td>
                      <Sparkline values={r.sparkline} />
                    </td>
                    {screenPreset && <td>{presetMatches.get(r.symbol) && <PresetMatchChips match={presetMatches.get(r.symbol)!} />}</td>}
                    <td>
                      <Link to={`/stock/${r.symbol}`} className="text-muted" style={{ fontSize: 13 }}>
                        Analyze →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
      )}
    </div>
  );
}

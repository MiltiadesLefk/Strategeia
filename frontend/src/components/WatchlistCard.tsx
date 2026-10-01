import { useMemo, useState } from 'react';
import { useResetWatchlist, useSaveWatchlist, useValidateWatchlistSymbol, useWatchlist } from '../api/hooks';
import type { WatchlistEntry } from '../api/types';
import { ErrorBanner, LoadingSpinner, formatRelativeTime } from './common';

const SHOW_FILTER_ABOVE = 15;

const LAYER_LABEL = {
  dev_filter: 'STRATEGEIA_DEV_TICKERS (server override)',
  custom: 'Your saved list',
  bundled: 'Bundled list',
} as const;

function SectorText({ entry }: { entry: WatchlistEntry }) {
  if (entry.sector_known) return <span className="text-muted">{entry.sector}</span>;
  return (
    <span
      className="text-muted"
      style={{ fontStyle: 'italic' }}
      title="No sector is known for this symbol, so the per-sector position cap does not count it."
    >
      sector unknown
    </span>
  );
}

/** Settings card for the list of symbols every scan, the dashboard and the
 *  company pickers work from. Edits are a local draft until Save: the list is
 *  stored on the server's volume, so it survives updates and rebuilds. */
export function WatchlistCard() {
  const { data, isLoading, error, refetch } = useWatchlist();
  const save = useSaveWatchlist();
  const reset = useResetWatchlist();
  const validate = useValidateWatchlistSymbol();

  // null = not editing: the list shown is whatever the server has saved.
  const [draft, setDraft] = useState<WatchlistEntry[] | null>(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [addError, setAddError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [justSaved, setJustSaved] = useState(false);

  const saved = useMemo(() => (data?.has_custom ? data.editable_entries : null), [data]);
  const list = draft ?? saved;
  const dirty =
    draft !== null &&
    (saved === null || draft.length !== saved.length || draft.some((e, i) => e.symbol !== saved[i].symbol));

  if (isLoading) {
    return (
      <div className="card">
        <h3>Watchlist</h3>
        <LoadingSpinner />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <h3>Watchlist</h3>
        <ErrorBanner message={(error as Error | null)?.message ?? 'Could not load the watchlist.'} onRetry={() => refetch()} />
      </div>
    );
  }

  const scanSize = data.scan_universe_size;
  const startSize = Math.min(scanSize, data.max_symbols, data.bundled_size);
  const edit = (next: WatchlistEntry[]) => {
    setDraft(next);
    setJustSaved(false);
    save.reset();
  };

  function add() {
    const symbol = newSymbol.trim().toUpperCase();
    if (!symbol || !list) return;
    setAddError(null);
    if (list.some((e) => e.symbol === symbol)) {
      setAddError(`${symbol} is already in the list.`);
      return;
    }
    if (list.length >= data!.max_symbols) {
      setAddError(`The watchlist holds at most ${data!.max_symbols} symbols.`);
      return;
    }
    validate.mutate(symbol, {
      onSuccess: (check) => {
        if (!check.valid) {
          setAddError(check.message ?? `${check.symbol} could not be verified.`);
          return;
        }
        if (list.some((e) => e.symbol === check.symbol)) {
          setAddError(`${check.symbol} is already in the list.`);
          return;
        }
        edit([
          ...list,
          { symbol: check.symbol, name: check.name ?? check.symbol, sector: check.sector ?? 'Unknown', sector_known: check.sector_known },
        ]);
        setNewSymbol('');
      },
      onError: (e) => setAddError((e as Error).message),
    });
  }

  function move(index: number, by: -1 | 1) {
    if (!list) return;
    const target = index + by;
    if (target < 0 || target >= list.length) return;
    const next = [...list];
    [next[index], next[target]] = [next[target], next[index]];
    edit(next);
  }

  function saveNow() {
    if (!list) return;
    save.mutate(
      list.map((e) => e.symbol),
      {
        onSuccess: () => {
          setDraft(null);
          setJustSaved(true);
          window.setTimeout(() => setJustSaved(false), 2500);
        },
      },
    );
  }

  function resetToBundled() {
    if (!window.confirm('Delete your saved watchlist and go back to the bundled list?')) return;
    reset.mutate(undefined, {
      onSuccess: () => {
        setDraft(null);
        setJustSaved(false);
      },
    });
  }

  const shown = list
    ? list
        .map((entry, index) => ({ entry, index }))
        .filter(({ entry }) => {
          const q = filter.trim().toLowerCase();
          return !q || entry.symbol.toLowerCase().includes(q) || entry.name.toLowerCase().includes(q);
        })
    : [];
  const scanned = list ? Math.min(scanSize, list.length) : 0;
  const bundledPreview = !list ? data.editable_entries.slice(0, scanSize) : [];

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        Watchlist
        <span className={`badge ${data.active_layer === 'custom' ? 'badge-green' : data.active_layer === 'dev_filter' ? 'badge-amber' : 'badge-neutral'}`}>
          {LAYER_LABEL[data.active_layer]}
        </span>
      </h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 680 }}>
        The symbols every market scan, the dashboard's top setups, auto-scan and the company pickers work from.
        Your list is saved on the server's own storage (<code>runtime/universe.json</code>), so it survives updates and
        rebuilds; without one, the bundled list ({data.bundled_size} symbols) is used. Order matters: scans take the
        first {scanSize} symbols from the top (the Scan Universe Size setting, below), and the dashboard's top setups
        come from that scan.
      </div>

      {data.active_layer === 'dev_filter' && data.dev_filter && (
        <div className="badge-amber" style={{ borderRadius: 8, padding: '10px 14px', fontSize: 13, fontWeight: 500 }}>
          <code>STRATEGEIA_DEV_TICKERS</code> is set on the server, so this list is ignored until it's removed. Right now
          only {data.dev_filter.join(', ')} {data.dev_filter.length === 1 ? 'is' : 'are'} used everywhere. You can still
          edit and save a list here; it takes over once the variable is removed.
        </div>
      )}
      {data.custom_error && (
        <div className="badge-red" style={{ borderRadius: 8, padding: '10px 14px', fontSize: 13, fontWeight: 500 }}>
          {data.custom_error} Saving a new list replaces the unreadable file.
        </div>
      )}

      {list === null ? (
        <>
          <div style={{ fontSize: 13 }}>
            You have no saved list, so the bundled list of {data.bundled_size} symbols is in use
            {data.active_layer === 'dev_filter' ? ' once the server override is removed' : ''}. Scans cover its first{' '}
            {Math.min(scanSize, data.bundled_size)}: {bundledPreview.slice(0, 12).map((e) => e.symbol).join(', ')}
            {bundledPreview.length > 12 ? ', …' : ''}.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-secondary" onClick={() => edit(data.editable_entries.slice(0, startSize))}>
              Start my own list from the first {startSize} bundled symbols
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => edit([])}>
              Start with an empty list
            </button>
          </div>
        </>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px', maxWidth: 280 }}>
              <input
                type="text"
                value={newSymbol}
                placeholder="Add a symbol, e.g. TSLA or ETH-USD"
                aria-label="Symbol to add"
                onChange={(e) => {
                  setNewSymbol(e.target.value.toUpperCase());
                  setAddError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') add();
                }}
              />
            </div>
            <button type="button" className="btn btn-secondary" onClick={add} disabled={validate.isPending || !newSymbol.trim()}>
              {validate.isPending ? 'Checking…' : 'Add'}
            </button>
            {list.length > SHOW_FILTER_ABOVE && (
              <input
                type="text"
                value={filter}
                placeholder="Filter the list"
                aria-label="Filter the list"
                onChange={(e) => setFilter(e.target.value)}
                style={{ flex: '0 1 180px' }}
              />
            )}
          </div>
          {addError && (
            <div className="text-red" style={{ fontSize: 12 }}>
              {addError}
            </div>
          )}
          <div className="text-muted" style={{ fontSize: 12 }}>
            A symbol is checked against the market-data providers before it is added, so a typo is caught here.
            {' '}
            {list.length} of {data.max_symbols} symbols
            {list.length > 0 && (
              <>
                {' '}
                — scanning the first {scanned} of {list.length}
                {scanned < list.length ? ` (the rest are not scanned at Scan Universe Size ${scanSize})` : ''}.
              </>
            )}
          </div>

          <div style={{ maxHeight: 360, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
            {shown.length === 0 && (
              <div className="text-muted" style={{ padding: 12, fontSize: 13 }}>
                {list.length === 0 ? 'The list is empty. Add at least one symbol to save it.' : 'No symbol matches the filter.'}
              </div>
            )}
            {shown.map(({ entry, index }) => (
              <div key={entry.symbol}>
                {index === scanSize && (
                  <div className="text-muted" style={{ fontSize: 11, padding: '4px 10px', background: 'var(--card-alt)', borderTop: '1px dashed var(--border)' }}>
                    Not scanned: the symbols below are past Scan Universe Size ({scanSize})
                  </div>
                )}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '5px 10px',
                    fontSize: 13,
                    borderTop: index === 0 ? undefined : '1px solid var(--border)',
                    opacity: index >= scanSize ? 0.6 : 1,
                  }}
                >
                  <span className="tabular-nums text-muted" style={{ width: 28, textAlign: 'right' }}>
                    {index + 1}
                  </span>
                  <strong style={{ width: 70 }}>{entry.symbol}</strong>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{entry.name}</span>
                    <span style={{ display: 'block', fontSize: 11 }}>
                      <SectorText entry={entry} />
                    </span>
                  </span>
                  <button type="button" className="btn btn-secondary" style={{ padding: '2px 8px' }} aria-label={`Move ${entry.symbol} up`} disabled={index === 0} onClick={() => move(index, -1)}>
                    ▲
                  </button>
                  <button type="button" className="btn btn-secondary" style={{ padding: '2px 8px' }} aria-label={`Move ${entry.symbol} down`} disabled={index === list.length - 1} onClick={() => move(index, 1)}>
                    ▼
                  </button>
                  <button type="button" className="btn btn-secondary" style={{ padding: '2px 8px' }} aria-label={`Remove ${entry.symbol}`} onClick={() => edit(list.filter((e) => e.symbol !== entry.symbol))}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={saveNow}
              disabled={save.isPending || !dirty || list.length < data.min_symbols}
              style={justSaved ? { background: 'var(--green)', borderColor: 'var(--green)' } : undefined}
            >
              {save.isPending ? 'Saving…' : justSaved ? '✓ Saved' : 'Save watchlist'}
            </button>
            {draft !== null && (
              <button type="button" className="btn btn-secondary" onClick={() => { setDraft(null); setAddError(null); save.reset(); }}>
                Discard changes
              </button>
            )}
            {dirty && <span className="text-muted" style={{ fontSize: 12 }}>Unsaved changes</span>}
          </div>
        </>
      )}
      {save.isError && <ErrorBanner message={(save.error as Error).message} />}
      {reset.isError && <ErrorBanner message={(reset.error as Error).message} />}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        <button type="button" className="btn btn-secondary" onClick={resetToBundled} disabled={reset.isPending || !data.has_custom}>
          {reset.isPending ? 'Resetting…' : 'Reset to the bundled list'}
        </button>
        <span className="text-muted" style={{ fontSize: 12 }}>
          {data.has_custom
            ? `Saved ${formatRelativeTime(data.custom_updated_at)}. Changes apply immediately, no restart needed.`
            : 'Nothing saved yet.'}
        </span>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Symbols that are not in the bundled list show no sector, because the data sources give no reliable one. The
        per-sector position cap simply doesn't count them, rather than grouping all of them together.
      </div>
    </div>
  );
}

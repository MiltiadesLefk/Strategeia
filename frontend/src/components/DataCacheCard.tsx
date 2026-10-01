import { useCacheStatus, useClearCache } from '../api/hooks';
import { ErrorBanner, LoadingSpinner, formatRelativeTime } from './common';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function entries(n: number): string {
  return `${n} ${n === 1 ? 'entry' : 'entries'}`;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13, padding: '3px 0' }}>
      <span className="text-muted">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

/** What the data cache and the price-history store hold. Read-only numbers
 *  plus one button that empties the provider cache (never the history). */
export function DataCacheCard() {
  const { data, isLoading, error, refetch } = useCacheStatus();
  const clear = useClearCache();
  const counters = data?.counters;
  const lookups = counters ? counters.memory_hits + counters.disk_hits + counters.coalesced_hits + counters.misses : 0;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <h3>Data cache</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        Market data you have already fetched is kept so repeat page views and scans don't go back to the data providers.
        It is also saved to a file, so a restart or redeploy starts with it instead of empty.
      </div>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={(error as Error).message} onRetry={() => refetch()} />}
      {data && counters && (
        <>
          <div>
            <Row label="In memory" value={entries(data.memory_entries)} />
            <Row
              label="Hit rate since start"
              value={data.hit_rate === null ? 'no lookups yet' : `${(data.hit_rate * 100).toFixed(0)}% of ${lookups}`}
            />
            <Row label="From disk after a restart" value={String(counters.disk_hits)} />
            <Row label="Old data shown during a provider outage" value={String(counters.stale_served + counters.stale_served_from_disk)} />
            {data.persistent ? (
              <>
                <Row
                  label={`Saved to ${data.persistent.file_name}`}
                  value={`${entries(data.persistent.entries)}, ${formatBytes(data.persistent.file_bytes)} (limit ${formatBytes(data.persistent.max_bytes)})`}
                />
                <Row label="Newest saved" value={formatRelativeTime(data.persistent.newest_stored_at)} />
                {data.persistent.errors > 0 && (
                  <Row label="Disk problems (data calls still worked)" value={String(data.persistent.errors)} />
                )}
              </>
            ) : (
              <Row label="Saved to disk" value="off (PERSIST_CACHE_DB=false)" />
            )}
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>Price history (for research)</div>
            {data.history.symbols === 0 ? (
              <div className="text-muted" style={{ fontSize: 12 }}>
                Nothing stored yet. Load years of daily bars with <code>scripts/preload_history.py</code>.
              </div>
            ) : (
              <>
                <Row label="Symbols" value={String(data.history.symbols)} />
                <Row label="Daily bars" value={data.history.bars.toLocaleString()} />
                <Row label="Range" value={`${data.history.first_date ?? '—'} to ${data.history.last_date ?? '—'}`} />
                <Row label="File size" value={formatBytes(data.history.file_bytes)} />
              </>
            )}
          </div>
        </>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-secondary" disabled={clear.isPending} onClick={() => clear.mutate()}>
          {clear.isPending ? 'Clearing…' : 'Clear cache'}
        </button>
        {clear.isSuccess && <span className="text-green" style={{ fontSize: 12 }}>{clear.data.message}</span>}
        {clear.isError && <span className="text-red" style={{ fontSize: 12 }}>{(clear.error as Error).message}</span>}
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Clearing empties the cache, in memory and on disk; the next requests refetch from the data providers. The stored
        price history is not touched.
      </div>
    </div>
  );
}

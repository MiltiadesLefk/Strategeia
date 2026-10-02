// Library tab: everything the app recorded about one ticker, newest first (news, fundamentals,
// filings, insider and Congress trades, watcher events, trade lessons). Read-only; the text comes
// from outside sources and is shown as quoted data. Nothing here changes a trade plan.
import { useState } from 'react';
import { useLibrary } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime, isSafeHttpUrl } from '../common';

export function LibraryPanel({ symbol }: { symbol: string }) {
  const [kind, setKind] = useState('');
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const { data, isLoading, error, refetch } = useLibrary(symbol, kind, q);

  if (isLoading && !data) return <LoadingSpinner label="Loading library…" />;
  if (error && !data) return <ErrorBanner message={(error as ApiError).message ?? 'Could not load the library'} onRetry={() => refetch()} />;
  if (!data) return null;

  const kinds = Object.entries(data.counts);
  return (
    <div className="card" data-library-part="list">
      <h3 style={{ marginBottom: 4 }}>Library for {data.symbol}</h3>
      <p className="text-muted" style={{ fontSize: 13, marginBottom: 12 }}>{data.note}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQ(text);
        }}
        style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}
      >
        <input type="search" value={text} placeholder="Search the text" onChange={(e) => setText(e.target.value)} style={{ minWidth: 200 }} />
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">All kinds</option>
          {kinds.map(([k, n]) => (
            <option key={k} value={k}>{k.replace(/_/g, ' ')} ({n})</option>
          ))}
        </select>
        <button type="submit" className="btn">Search</button>
      </form>
      <p className="text-muted" style={{ fontSize: 13, marginBottom: 8 }}>{data.total} matching {data.total === 1 ? 'entry' : 'entries'}{data.total > data.entries.length ? `, newest ${data.entries.length} shown` : ''}</p>
      {data.entries.length === 0 ? (
        <EmptyState>Nothing recorded for {data.symbol} yet.</EmptyState>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Known</th>
                <th>Kind</th>
                <th>What</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((e, i) => (
                <tr key={`${e.kind}-${e.known_at}-${i}`}>
                  <td className="tabular-nums" title={e.known_at}>{formatRelativeTime(e.known_at)}</td>
                  <td>{e.kind_label}</td>
                  <td>
                    {e.url && isSafeHttpUrl(e.url) ? (
                      <a href={e.url} target="_blank" rel="noopener noreferrer">{e.title}</a>
                    ) : (
                      e.title
                    )}
                    {e.summary && <div className="text-muted" style={{ fontSize: 13 }}>{e.summary}</div>}
                  </td>
                  <td className="text-muted">{e.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

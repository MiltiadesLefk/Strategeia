import { useEffect, useState } from 'react';
import { useRatingRun, useRatingRuns, useStartRatingRun } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { RatingRow } from '../api/types';
import { TickerLink } from '../components/TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner } from '../components/common';

function when(iso: string): string {
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString();
}

function RatingDetailRow({ row }: { row: RatingRow }) {
  const parts = Object.entries(row.detail.parts ?? {}).filter(([, v]) => v !== 0);
  const why = row.detail.why ?? {};
  return (
    <tr>
      <td colSpan={6} style={{ background: 'var(--bg-subtle, transparent)' }}>
        {parts.length === 0 ? <span className="text-muted">No part scored.</span> : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {parts.map(([name, points]) => (
              <li key={name}>
                <strong>{name} {points > 0 ? `+${points}` : points}</strong>
                {why[name] ? <span className="text-muted"> — {why[name]}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}

export function RatingsPage() {
  const runs = useRatingRuns();
  const start = useStartRatingRun();
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    if (!selected && runs.data?.length) setSelected(runs.data[0].run_id);
  }, [runs.data, selected]);
  const run = useRatingRun(selected);
  const going = !!runs.data?.some((r) => r.running);

  async function rateNow() {
    const result = await start.mutateAsync();
    setSelected(result.run_id);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="page-header">
        <div>
          <h1>Ratings</h1>
          <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
            The 32-point evidence rating of every watchlist stock: each morning scan that ran, and runs you start here. Rate now only rates; it buys nothing.
          </div>
        </div>
        <button className="btn btn-primary" onClick={rateNow} disabled={start.isPending || going}>
          {going ? 'Rating…' : 'Rate now'}
        </button>
      </div>
      {start.error && <ErrorBanner message={(start.error as ApiError).message ?? 'Could not start the run'} />}
      {runs.isLoading ? <LoadingSpinner /> : runs.error ? (
        <ErrorBanner message="Could not load the rating history" onRetry={() => runs.refetch()} />
      ) : !runs.data?.length ? (
        <EmptyState>No ratings yet. Press Rate now, or turn on the daily scan (Settings) and they appear after its next run.</EmptyState>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) 1fr', gap: 16, alignItems: 'start' }}>
          <div className="card" style={{ padding: 8 }}>
            {runs.data.map((r) => (
              <button
                key={r.run_id}
                onClick={() => { setSelected(r.run_id); setOpen(null); }}
                className="btn btn-secondary"
                style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 6, opacity: r.run_id === selected ? 1 : 0.7 }}
              >
                <div>{when(r.run_at)}{r.running ? ' · running' : ''}</div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  {r.source === 'scan' ? 'Morning scan' : 'Manual'} · {r.rated} rated · {r.passed} passed · {r.bought} bought
                </div>
              </button>
            ))}
          </div>
          <div className="card">
            {run.isLoading ? <LoadingSpinner /> : (
              <div className="score-table-wrap">
                <table className="score-table">
                  <thead>
                    <tr><th>Stock</th><th style={{ textAlign: 'right' }}>Points</th><th style={{ textAlign: 'right' }}>%</th><th>Result</th><th>Dip</th><th>Why</th></tr>
                  </thead>
                  <tbody>
                    {(run.data?.ratings ?? []).flatMap((r) => [
                      <tr key={r.symbol} onClick={() => setOpen(open === r.symbol ? null : r.symbol)} style={{ cursor: 'pointer' }}>
                        <td><TickerLink symbol={r.symbol} /></td>
                        <td style={{ textAlign: 'right' }}>{r.points}/{r.max_points}</td>
                        <td style={{ textAlign: 'right' }}>{r.percent}%</td>
                        <td>{r.bought ? 'Bought' : r.passed ? 'Passed' : 'Below bar'}</td>
                        <td>{r.dip ? 'dip' : ''}</td>
                        <td className="text-muted">{open === r.symbol ? 'hide' : 'show parts'}</td>
                      </tr>,
                      ...(open === r.symbol ? [<RatingDetailRow key={`${r.symbol}-d`} row={r} />] : []),
                    ])}
                  </tbody>
                </table>
                {run.data?.running && <div className="text-muted" style={{ padding: 8 }}>Still rating… rows appear as they finish.</div>}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

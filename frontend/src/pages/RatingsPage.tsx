import { useEffect, useState } from 'react';
import { useRatingRun, useRatingRuns, useStartRatingRun } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { RatingRow } from '../api/types';
import { RatingTable } from './RatingTable';
import { TickerLink } from './TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner } from './common';

function when(iso: string): string {
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString();
}

function RatingDetailRow({ row }: { row: RatingRow }) {
  return (
    <tr>
      <td colSpan={6}>
        <RatingTable row={row} />
      </td>
    </tr>
  );
}

export function RatingsPanel() {
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
        <div className="text-muted" style={{ fontSize: 13 }}>
          The 32-point evidence rating of every watchlist stock: each morning scan that ran, and runs you start here. Rate now only rates; it buys nothing.
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
                        <td className="text-muted">{open === r.symbol ? 'hide' : 'show table'}</td>
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

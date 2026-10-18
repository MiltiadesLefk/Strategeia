import { useState } from 'react';
import type { ApiError } from '../api/client';
import { useRateStock, useStockRatings } from '../api/hooks';
import { RatingTable } from './RatingTable';
import { EmptyState, ErrorBanner, LoadingSpinner } from './common';

const when = (iso: string) => new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString();

/** One stock's evidence rating, explained part by part, with a button to re-rate it now. */
export function CompanyRatingPanel({ symbol }: { symbol: string }) {
  const history = useStockRatings(symbol);
  const rate = useRateStock(symbol);
  const [row, ...older] = history.data ?? [];
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ marginBottom: 4 }}>
            {row ? `${row.percent}/100 · ${row.bought ? 'Bought' : row.passed ? 'Passed' : 'Below bar'}${row.dip ? ' · dip' : ''}` : 'Not rated yet'}
          </h3>
          {row && <div className="text-muted" style={{ fontSize: 12 }}>Rated {when(row.run_at)}</div>}
        </div>
        <button className="btn btn-primary" onClick={() => rate.mutate()} disabled={rate.isPending}>
          {rate.isPending ? 'Rating…' : 'Re-rate now'}
        </button>
      </div>
      {rate.error && <ErrorBanner message={(rate.error as ApiError).message ?? 'Could not rate this stock'} />}
      {history.isLoading ? <LoadingSpinner /> : !row ? (
        <EmptyState>{symbol} has not been rated yet. Press Re-rate now. It only rates; it buys nothing.</EmptyState>
      ) : (
        <>
          <RatingTable row={row} />
          {older.length > 0 && (
            <div className="card">
              <h3 style={{ marginBottom: 8 }}>Earlier ratings</h3>
              <table>
                <thead><tr><th>When</th><th style={{ textAlign: 'right' }}>Score /100</th><th>Result</th><th /></tr></thead>
                <tbody>
                  {older.flatMap((r) => [
                    <tr key={r.run_at} onClick={() => setOpen(open === r.run_at ? null : r.run_at)} style={{ cursor: 'pointer' }}>
                      <td>{when(r.run_at)}</td>
                      <td style={{ textAlign: 'right' }}>{r.percent}</td>
                      <td>{r.bought ? 'Bought' : r.passed ? 'Passed' : 'Below bar'}</td>
                      <td className="text-muted">{open === r.run_at ? 'hide table' : 'show table'}</td>
                    </tr>,
                    ...(open === r.run_at ? [<tr key={`${r.run_at}-t`}><td colSpan={4}><RatingTable row={r} /></td></tr>] : []),
                  ])}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

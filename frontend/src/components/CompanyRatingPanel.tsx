import { useRatingRun, useRatingRuns } from '../api/hooks';
import { EmptyState, LoadingSpinner } from './common';

/** The newest evidence rating of one stock, with the parts that made it. */
export function CompanyRatingPanel({ symbol }: { symbol: string }) {
  const runs = useRatingRuns();
  const latest = runs.data?.find((r) => !r.running) ?? runs.data?.[0];
  const run = useRatingRun(latest?.run_id ?? null);
  if (runs.isLoading || run.isLoading) return <LoadingSpinner />;
  const row = run.data?.ratings.find((r) => r.symbol === symbol);
  if (!row) return <EmptyState>{symbol} has not been rated yet. Rate now on Market Scan → Ratings rates the whole watchlist.</EmptyState>;
  const parts = Object.entries(row.detail.parts ?? {}).filter(([, v]) => v !== 0);
  const why = row.detail.why ?? {};
  return (
    <div className="card">
      <h3 style={{ marginBottom: 4 }}>
        {row.points}/{row.max_points} · {row.percent}% · {row.bought ? 'Bought' : row.passed ? 'Passed' : 'Below bar'}
        {row.dip ? ' · dip' : ''}
      </h3>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
        Rated {new Date(row.run_at.endsWith('Z') ? row.run_at : `${row.run_at}Z`).toLocaleString()}
      </div>
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
    </div>
  );
}

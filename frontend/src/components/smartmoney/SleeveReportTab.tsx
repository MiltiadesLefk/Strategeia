import { useSleeveReport } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { SleeveComparison, SleeveGroupStats, SleeveReportRow } from '../../api/types';
import { ErrorBanner, LoadingSpinner } from '../common';

const VERDICT_LABEL: Record<SleeveReportRow['verdict'], string> = {
  not_enough_data: 'Not enough data',
  beats_both: 'Interval above zero against both',
  beats_one: 'Interval above zero against one',
  beats_neither: 'Interval above zero against neither',
};

function pts(v: number | null): string {
  return v == null ? '–' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
}

function Comparison({ c }: { c: SleeveComparison }) {
  return (
    <div style={{ fontSize: 13 }}>
      <strong>{c.label}</strong>
      {c.available ? (
        <div className="tabular-nums">
          mean excess {pts(c.mean_excess_pct)} points per trade (95% interval {pts(c.low)} to {pts(c.high)}), on {c.compared} of {c.of} trades
          {c.reference_mean_pct != null && <> · reference averaged {pts(c.reference_mean_pct)}%</>}
        </div>
      ) : (
        <div className="text-muted">Not available: {c.reason}</div>
      )}
      {c.available && c.reason && <div className="text-muted" style={{ fontSize: 12 }}>{c.reason}</div>}
    </div>
  );
}

function GroupRow({ g }: { g: SleeveGroupStats }) {
  return (
    <tr>
      <td>{g.label}</td>
      <td className="tabular-nums">{g.n}</td>
      <td className="tabular-nums">
        {g.win_rate != null ? `${g.win_rate.toFixed(0)}%` : '–'}
        {g.win_rate_low != null && g.win_rate_high != null && (
          <span className="text-muted"> ({g.win_rate_low.toFixed(0)}–{g.win_rate_high.toFixed(0)}%)</span>
        )}
      </td>
      <td className="tabular-nums">
        {g.avg_r != null ? `${g.avg_r.toFixed(2)}R` : '–'}
        {g.avg_r_low != null && g.avg_r_high != null && (
          <span className="text-muted"> ({g.avg_r_low.toFixed(2)} to {g.avg_r_high.toFixed(2)})</span>
        )}
      </td>
      <td className="text-muted">{g.small_sample ? 'too early to read' : ''}</td>
    </tr>
  );
}

function SleeveCard({ s }: { s: SleeveReportRow }) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }} data-testid="sleeve-report-card">
      <div className="split-row" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 16 }}>{s.name}</strong>
          <span className="text-muted" style={{ fontSize: 12 }}>{s.key !== s.name && s.key}{!s.exists && ' · not created yet'}</span>
        </div>
        <div className="tabular-nums" style={{ fontWeight: 700 }}>
          n = {s.closed_trades} <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>closed (needs {s.min_closed_trades})</span>
        </div>
      </div>
      <div>
        <span className={s.verdict === 'not_enough_data' ? 'badge badge-neutral' : 'badge badge-green'}>{VERDICT_LABEL[s.verdict]}</span>
        {s.candidate_to_disable && (
          <span className="badge badge-amber" style={{ marginLeft: 8 }} title="Information only: nothing is disabled automatically">
            candidate to disable (your call)
          </span>
        )}
      </div>
      <div style={{ fontSize: 13 }}>{s.headline}</div>
      {s.reason && <div className="text-muted" style={{ fontSize: 12 }}>{s.reason}</div>}
      {s.vs_spy && <Comparison c={s.vs_spy} />}
      {s.vs_random && <Comparison c={s.vs_random} />}
      {s.closed_trades > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="table" style={{ fontSize: 12 }}>
            <thead>
              <tr>
                <th>Group</th>
                <th>n</th>
                <th>Win rate (95%)</th>
                <th>Avg R (95%)</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <GroupRow g={s.trade_stats} />
              {s.groups.map((g) => (
                <GroupRow key={g.label} g={g} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function SleeveReportTab() {
  const q = useSleeveReport();
  return (
    <>
      <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)', fontSize: 13 }}>
        <strong>Sleeve report.</strong> What each paper account&apos;s closed trades did, reported next to SPY over the same dates and
        next to random picks from the same universe held over the same days. It is read-only and informational: it does not show the
        signal works, and nothing here disables a sleeve. Below 100 closed trades a sleeve is not compared.
      </div>
      {q.isLoading && <LoadingSpinner />}
      {q.error && <ErrorBanner message={(q.error as ApiError).message} onRetry={() => q.refetch()} />}
      {q.data?.sleeves.map((s) => (
        <SleeveCard key={s.key} s={s} />
      ))}
      {q.data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          <ul style={{ margin: '0 0 0 18px' }}>
            {q.data.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
            <li>
              Random picks: {q.data.random_draws} per trade from {q.data.universe_size} symbols, fixed seed {q.data.random_seed}.
            </li>
          </ul>
        </div>
      )}
    </>
  );
}

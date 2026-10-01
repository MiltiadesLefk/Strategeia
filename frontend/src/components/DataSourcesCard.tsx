import { useDataSources, useProbeDataSource } from '../api/hooks';
import type { DataSourceHealth } from '../api/types';
import { ErrorBanner, LoadingSpinner, formatRelativeTime } from './common';

const STATUS_COLOUR: Record<DataSourceHealth['status'], string> = {
  healthy: 'var(--green, #16a34a)',
  degraded: 'var(--amber, #d97706)',
  failing: 'var(--red, #dc2626)',
  unused: 'var(--text-muted, #94a3b8)',
};

const STATUS_HELP: Record<DataSourceHealth['status'], string> = {
  healthy: 'Healthy: recent calls succeeded.',
  degraded: 'Degraded: some recent calls failed, or old cached data had to be shown.',
  failing: 'Failing: most recent calls failed, or several failed in a row.',
  unused: 'Unused: not called since the server started.',
};

function ms(value: number | null): string {
  if (value === null) return '—';
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

function SourceRows({
  rows,
  probing,
  onProbe,
}: {
  rows: DataSourceHealth[];
  probing: string | null;
  onProbe: (name: string) => void;
}) {
  return (
    <>
      {rows.map((row) => (
        <tr key={row.name}>
          <td>
            <span
              title={STATUS_HELP[row.status]}
              aria-label={row.status}
              style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: STATUS_COLOUR[row.status], marginRight: 8 }}
            />
            <strong>{row.position !== null ? `${row.position}. ` : ''}{row.label}</strong>
            <div className="text-muted" style={{ fontSize: 12 }}>{row.purpose}</div>
            {row.last_success_at && (
              <div className="text-muted" style={{ fontSize: 12 }}>last success {formatRelativeTime(row.last_success_at)}</div>
            )}
            {row.last_error && (
              <div className="text-red" style={{ fontSize: 12 }} title={row.last_error}>
                last error: {row.last_error.length > 90 ? `${row.last_error.slice(0, 90)}…` : row.last_error} ({formatRelativeTime(row.last_error_at)})
              </div>
            )}
            {row.stale_served > 0 && (
              <div className="text-muted" style={{ fontSize: 12 }}>old cached data shown {row.stale_served}x</div>
            )}
          </td>
          <td className="tabular-nums">{row.calls === 0 ? '—' : row.calls}</td>
          <td className="tabular-nums">{row.success_rate === null ? '—' : `${Math.round(row.success_rate * 100)}%`}</td>
          <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>{ms(row.latency_p50_ms)} / {ms(row.latency_p95_ms)}</td>
          <td>
            {row.can_probe ? (
              <button type="button" className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: 12 }} disabled={probing === row.name} onClick={() => onProbe(row.name)}>
                {probing === row.name ? '…' : 'Probe'}
              </button>
            ) : (
              <span className="text-muted" style={{ fontSize: 12 }}>no probe</span>
            )}
          </td>
        </tr>
      ))}
    </>
  );
}

/** Which data sources the app uses, how each is behaving, and a button to test one.
 *  Numbers are counted from real calls since the server started; nothing is estimated. */
export function DataSourcesCard() {
  const { data, isLoading, error, refetch } = useDataSources();
  const probe = useProbeDataSource();
  const probing = probe.isPending ? probe.variables ?? null : null;
  const anyCalls = !!data && [...data.chain, ...data.others].some((r) => r.calls > 0);

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <h3>Data sources</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 720 }}>
        Market data is asked of the sources below in order; if one fails, the next one is tried. Answers are cached, so
        most page views never reach a source at all (that is why call counts stay small).
        If every source fails, saved older data may be shown and is labelled as such; nothing is made up.
      </div>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={(error as Error).message} onRetry={() => refetch()} />}
      {data && (
        <>
          {!anyCalls && (
            <div className="text-muted" style={{ fontSize: 13 }}>
              No data calls have been made since the server started, so there are no numbers yet. Open a page that loads
              market data, or press Probe.
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table className="table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Calls</th>
                  <th title={`Over the last ${data.window_size} calls`}>Success</th>
                  <th title="Median / slowest 5% of live requests (cache hits excluded)">p50 / p95</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td colSpan={5} className="text-muted" style={{ fontSize: 12, paddingTop: 8 }}>Price and company data, in fallback order</td>
                </tr>
                <SourceRows rows={data.chain} probing={probing} onProbe={(n) => probe.mutate(n)} />
                <tr>
                  <td colSpan={5} className="text-muted" style={{ fontSize: 12, paddingTop: 12 }}>Other sources</td>
                </tr>
                <SourceRows rows={data.others} probing={probing} onProbe={(n) => probe.mutate(n)} />
              </tbody>
            </table>
          </div>
        </>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', minHeight: 20 }}>
        {probe.isSuccess && (
          <span className={probe.data.ok ? 'text-green' : 'text-red'} style={{ fontSize: 12 }}>
            {probe.data.name}: {probe.data.ok ? `ok in ${ms(probe.data.latency_ms)}. ${probe.data.detail ?? ''}` : `failed after ${ms(probe.data.latency_ms)}. ${probe.data.error ?? ''}`}
          </span>
        )}
        {probe.isError && <span className="text-red" style={{ fontSize: 12 }}>{(probe.error as Error).message}</span>}
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Success and latency cover the last {data?.window_size ?? 100} calls to each source; call counts are since the server
        started. A bad symbol counts as a failed call for every source that was asked about it. Backtests and replays are not
        counted.
      </div>
    </div>
  );
}

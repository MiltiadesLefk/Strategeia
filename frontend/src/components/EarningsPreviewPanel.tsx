import { useEarningsPreview } from '../api/hooks';
import type { ApiError } from '../api/client';
import { AiNoteCard, EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatPct } from './common';

const VERDICT_TEXT: Record<string, string> = {
  rich: 'options are pricing more than this stock usually moves',
  cheap: 'options are pricing less than this stock usually moves',
  'in line': 'options are pricing about what this stock usually moves',
};

function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return '';
  return value > 0 ? 'text-green' : 'text-red';
}

/** Facts first (all computed from our own data), then the rule-based or AI paragraph.
 * Scenario rows are positions in the stock's own past reaction distribution, never price targets. */
export function EarningsPreviewPanel({ symbol }: { symbol: string }) {
  const { data, isLoading, error, refetch } = useEarningsPreview(symbol);

  if (isLoading) return <LoadingSpinner label="Building earnings preview…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />;
  if (!data) return null;

  const implied = data.implied_move;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
      <h3>Earnings preview</h3>
      <AiNoteCard
        label="Preview"
        provider={data.summary_provider}
        text={data.summary}
        error={data.summary_error}
      />

      <div className="split-row">
        <div className="card">
          <h3 style={{ marginBottom: 12 }}>Move size: history vs options</h3>
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Median past move ({data.reactions_sampled} reports)
              </div>
              <div className="tabular-nums" style={{ fontWeight: 600 }}>
                {data.historical_move_pct !== null ? formatPct(data.historical_move_pct) : '—'}
              </div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 11 }}>
                Options-implied{implied ? ` (to ${implied.expiration})` : ''}
              </div>
              <div className="tabular-nums" style={{ fontWeight: 600 }}>
                {implied ? formatPct(implied.implied_move_pct) : '—'}
              </div>
            </div>
          </div>
          {implied && implied.covers_earnings && implied.ratio !== null && implied.verdict && (
            <div className="text-muted" style={{ fontSize: 13, marginTop: 10 }}>
              {implied.ratio.toFixed(1)}x the historical median: {VERDICT_TEXT[implied.verdict] ?? implied.verdict}.
            </div>
          )}
          {implied && !implied.covers_earnings && (
            <div className="text-muted" style={{ fontSize: 13, marginTop: 10 }}>
              The nearest options expire before the report, so they do not price it yet and are not compared.
            </div>
          )}
          {!implied && (
            <div className="text-muted" style={{ fontSize: 13, marginTop: 10 }}>
              No options data for this symbol.
            </div>
          )}
        </div>

        <div className="card">
          <h3 style={{ marginBottom: 12 }}>Scenarios from its own history</h3>
          {data.scenarios.length === 0 ? (
            <EmptyState>{data.scenarios_note ?? 'Not enough past reports.'}</EmptyState>
          ) : (
            <>
              <table>
                <thead>
                  <tr>
                    <th>Scenario</th>
                    <th>Reaction</th>
                    <th>Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  {data.scenarios.map((s) => (
                    <tr key={s.name}>
                      <td>{s.name}</td>
                      <td className={`tabular-nums ${tone(s.move_pct)}`}>{signed(s.move_pct)}</td>
                      <td className="text-muted" style={{ fontSize: 12 }}>
                        {s.description} (percentile {s.percentile})
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-muted" style={{ fontSize: 11, marginTop: 8 }}>
                How the stock reacted to its last {data.reactions_sampled} reports. These are past reactions, not forecasts or price targets.
              </div>
            </>
          )}
        </div>
      </div>

      <div className="split-row">
        <div className="card">
          <h3 style={{ marginBottom: 12 }}>Surprise record</h3>
          {data.track_record ? (
            <div className="text-muted" style={{ fontSize: 13, marginBottom: 8 }}>
              Beat EPS consensus in {data.track_record.beats} of {data.track_record.quarters} quarters, missed in{' '}
              {data.track_record.misses}.
            </div>
          ) : (
            <div className="text-muted" style={{ fontSize: 13, marginBottom: 8 }}>
              No reported-quarter history available.
            </div>
          )}
          {data.surprise_table.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Report</th>
                  <th>EPS est.</th>
                  <th>EPS actual</th>
                  <th>Surprise</th>
                  <th>Stock reaction</th>
                </tr>
              </thead>
              <tbody>
                {data.surprise_table.map((row) => (
                  <tr key={row.report_date}>
                    <td>{row.report_date}</td>
                    <td className="tabular-nums">{row.eps_estimate !== null ? row.eps_estimate.toFixed(2) : '—'}</td>
                    <td className="tabular-nums">{row.eps_actual !== null ? row.eps_actual.toFixed(2) : '—'}</td>
                    <td className={`tabular-nums ${tone(row.surprise_pct)}`}>{signed(row.surprise_pct)}</td>
                    <td className={`tabular-nums ${tone(row.reaction_pct)}`}>{signed(row.reaction_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h3 style={{ marginBottom: 12 }}>What to watch</h3>
          {data.what_to_watch.length === 0 ? (
            <EmptyState>Nothing stands out from the data.</EmptyState>
          ) : (
            <ul style={{ margin: '0 0 0 18px', fontSize: 13, lineHeight: 1.6 }}>
              {data.what_to_watch.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          {data.estimate?.revenue_estimate != null && (
            <div className="text-muted" style={{ fontSize: 12, marginTop: 10 }}>
              Consensus revenue {formatMoney(data.estimate.revenue_estimate, { compact: true })}
              {data.estimate.fiscal_period_label ? ` for ${data.estimate.fiscal_period_label}` : ''}.
            </div>
          )}
        </div>
      </div>

      {data.data_gaps.length > 0 && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Missing from this preview: {data.data_gaps.join(', ')}.
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 12 }}>
        Every figure above comes from this app's own data and rules. The paragraph is {data.summary_provider === 'none' ? 'rule-based' : `written by ${data.summary_provider} from those figures`}.
      </div>
    </div>
  );
}

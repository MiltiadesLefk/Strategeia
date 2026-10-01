import { useCalibration } from '../api/hooks';
import type { CalibrationBand, CalibrationIc, CalibrationVerdict } from '../api/types';
import type { ApiError } from '../api/client';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatR } from './common';

const VERDICT_LABEL: Record<CalibrationVerdict, string> = {
  not_enough_data: 'Not enough data',
  no_variation: 'No variation',
  no_clear_relationship: 'No clear link',
  positive: 'Rises with results',
  negative: 'Falls with results',
};

function verdictBadgeClass(verdict: CalibrationVerdict): string {
  if (verdict === 'positive') return 'badge badge-green';
  if (verdict === 'negative') return 'badge badge-red';
  if (verdict === 'no_clear_relationship') return 'badge badge-neutral';
  return 'badge badge-amber';
}

function formatIc(value: number | null): string {
  return value === null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}`;
}

function formatP(value: number | null): string {
  if (value === null) return '—';
  return value < 0.001 ? '< 0.001' : value.toFixed(3);
}

function interval(low: number | null, high: number | null, format: (v: number) => string): string {
  return low === null || high === null ? 'no interval yet' : `${format(low)} to ${format(high)}`;
}

/** Win rate as a bar, with the Wilson interval drawn over it so a small sample
 *  visibly spans most of the track instead of looking like a precise number. */
function WinRateBar({ band }: { band: CalibrationBand }) {
  if (band.win_rate === null || band.win_rate_low === null || band.win_rate_high === null) {
    return <span className="text-muted">—</span>;
  }
  const color = band.small_sample ? 'var(--amber)' : 'var(--green)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 190 }}>
      <div
        style={{ position: 'relative', flex: 1, height: 10, background: '#1d212c', borderRadius: 5 }}
        title={`${band.win_rate.toFixed(0)}% (95% interval ${band.win_rate_low.toFixed(0)}% to ${band.win_rate_high.toFixed(0)}%)`}
      >
        <div style={{ width: `${band.win_rate}%`, height: '100%', background: color, opacity: 0.55, borderRadius: 5 }} />
        <div
          style={{
            position: 'absolute',
            top: 3,
            height: 4,
            left: `${band.win_rate_low}%`,
            width: `${Math.max(1, band.win_rate_high - band.win_rate_low)}%`,
            background: 'var(--text)',
            opacity: 0.85,
            borderRadius: 2,
          }}
        />
      </div>
      <div className="tabular-nums" style={{ width: 110, fontSize: 12 }}>
        <strong>{band.win_rate.toFixed(0)}%</strong>
        <span className="text-muted"> ({band.win_rate_low.toFixed(0)}–{band.win_rate_high.toFixed(0)})</span>
      </div>
    </div>
  );
}

function BandRow({ band, minTrades, summary }: { band: CalibrationBand; minTrades: number; summary?: boolean }) {
  return (
    <tr style={summary ? { fontWeight: 600 } : undefined}>
      <td>{band.label}</td>
      <td className="tabular-nums">
        {band.n}
        {band.n > 0 && band.n < minTrades && (
          <span className="badge badge-amber" style={{ marginLeft: 8, fontSize: 10, padding: '1px 6px' }} title={`Fewer than ${minTrades} trades: too few to read much into`}>
            few
          </span>
        )}
      </td>
      <td>
        <WinRateBar band={band} />
      </td>
      <td className={`tabular-nums ${band.avg_r !== null && band.avg_r > 0 ? 'text-green' : band.avg_r !== null && band.avg_r < 0 ? 'text-red' : ''}`}>
        {formatR(band.avg_r)}
        {band.avg_r_low !== null && band.avg_r_high !== null && (
          <div className="text-muted" style={{ fontSize: 10 }}>
            {formatR(band.avg_r_low)} to {formatR(band.avg_r_high)}
          </div>
        )}
      </td>
      <td className={`tabular-nums ${band.total_pnl > 0 ? 'text-green' : band.total_pnl < 0 ? 'text-red' : ''}`}>
        {band.n > 0 ? formatMoney(band.total_pnl) : '—'}
      </td>
    </tr>
  );
}

function IcRow({ result, showAdjusted }: { result: CalibrationIc; showAdjusted?: boolean }) {
  return (
    <tr>
      <td>{result.label}</td>
      <td className="tabular-nums">
        {result.n}
        {showAdjusted && result.n_nonzero !== null && result.n > 0 && (
          <div className="text-muted" style={{ fontSize: 10 }}>
            {result.n_nonzero} non-zero
          </div>
        )}
      </td>
      <td className="tabular-nums">{formatIc(result.ic)}</td>
      <td className="tabular-nums text-muted">{interval(result.ci_low, result.ci_high, (v) => formatIc(v))}</td>
      <td className="tabular-nums">
        {formatP(showAdjusted ? (result.p_value_adjusted ?? result.p_value) : result.p_value)}
      </td>
      <td>
        <span className={verdictBadgeClass(result.verdict)} title={result.note}>
          {VERDICT_LABEL[result.verdict]}
        </span>
      </td>
    </tr>
  );
}

/**
 * Does the confidence score predict results? Everything here is computed on the
 * server from closed paper trades; the card only lays it out. The loudest thing
 * on it, while there are few trades, is the banner saying not to read the numbers.
 */
export function CalibrationCard() {
  const { data, isLoading, error, refetch } = useCalibration();

  return (
    <div className="card" data-testid="calibration-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, marginBottom: 4 }}>
        <h3>Does confidence predict results?</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>
          Rule-based statistics on closed paper trades. No AI involved.
        </span>
      </div>

      {isLoading && <LoadingSpinner label="Loading calibration…" />}
      {error && <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, marginTop: 8 }}>
          <div
            className={data.reliable ? 'badge-neutral' : 'badge-amber'}
            style={{ borderRadius: 8, padding: '12px 16px', fontSize: 13, fontWeight: data.reliable ? 400 : 600 }}
            role={data.reliable ? undefined : 'alert'}
          >
            {data.headline}
          </div>

          {data.closed_trades === 0 ? (
            <EmptyState>The table appears once paper trades have closed.</EmptyState>
          ) : (
            <>
              <div>
                <h4 style={{ marginBottom: 8 }}>By confidence band (evidence points out of {data.points_max})</h4>
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Band</th>
                        <th>Trades</th>
                        <th>Win rate (95% interval)</th>
                        <th>Avg R (95% interval)</th>
                        <th>Total P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.bands.map((band) => (
                        <BandRow key={band.label} band={band} minTrades={data.min_trades_per_band} />
                      ))}
                      {data.overall && <BandRow band={data.overall} minTrades={data.min_trades_per_band} summary />}
                    </tbody>
                  </table>
                </div>
              </div>

              <div>
                <h4 style={{ marginBottom: 8 }}>Information coefficient</h4>
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Compared</th>
                        <th>Trades</th>
                        <th>IC</th>
                        <th>95% interval</th>
                        <th>p-value</th>
                        <th>Reading</th>
                      </tr>
                    </thead>
                    <tbody>
                      <IcRow result={data.ic} />
                      {data.ic_by_direction.map((r) => (
                        <IcRow key={r.key} result={r} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div>
                <h4 style={{ marginBottom: 8 }}>Which parts of the score relate to results?</h4>
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Component</th>
                        <th>Trades</th>
                        <th>IC</th>
                        <th>95% interval</th>
                        <th>p-value (adjusted)</th>
                        <th>Reading</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.components.map((c) => (
                        <IcRow key={c.key} result={c} showAdjusted />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="text-muted" style={{ fontSize: 12 }} data-testid="calibration-exclusions">
                {data.analyzed_trades} of {data.closed_trades} closed trades analysed.
                {data.excluded.total > 0
                  ? ` Left out: ${data.excluded.no_linked_plan} with no linked plan, ${data.excluded.missing_r} with no recorded R, ${data.excluded.outside_bands} outside every band.`
                  : ' None left out.'}
              </div>

              <div className="text-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                <strong>How to read this.</strong> Each plan carries a confidence score made of evidence points. The table groups
                closed trades by those points and shows how often each group won and how much it earned per unit of risk (R). The
                thin white mark on each win-rate bar is the 95% interval: the range the true win rate could plausibly sit in. With
                few trades it is wide, and bars in amber have fewer than {data.min_trades_per_band} trades behind them. The
                information coefficient (IC) asks one question: do higher scores tend to come with better results? It runs from
                -1 to +1; 0 means no link, and real trading signals are usually small (0.05 to 0.15 is already interesting). A
                result only counts as a finding with at least {data.min_trades_for_reading} trades, an interval that stays on one
                side of zero and a small p-value; the p-value is how often shuffling the results at random would look this
                strong. The components are tested together ({data.components_tested} testable here), so their p-values are adjusted:
                test enough things and one will look good by luck. This is a measurement of past paper trades, not a forecast.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

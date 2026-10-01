import { useState } from 'react';
import { useReplay } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { ReplayFlip, ReplayOverrides, ReplayStats } from '../api/types';
import { TickerLink } from './TickerLink';
import { ErrorBanner, formatR } from './common';

const KNOBS = [
  { key: 'min_confidence_for_trade', label: 'Minimum confidence to trade' },
  { key: 'ai_overlay_objection_action', label: 'What an AI objection does' },
  { key: 'ai_overlay_scores_confidence', label: 'AI objection costs confidence points' },
  { key: 'allowed_directions', label: 'Directions allowed' },
] as const;
type KnobKey = (typeof KNOBS)[number]['key'];

function range(low: number | null, high: number | null, format: (v: number) => string): string {
  return low === null || high === null ? '' : `${format(low)} to ${format(high)}`;
}

function StatsColumn({ title, s }: { title: string; s: ReplayStats }) {
  return (
    <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14, flex: '1 1 240px' }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>{title}</div>
      <table style={{ width: '100%', fontSize: 13 }} className="tabular-nums">
        <tbody>
          <tr><td className="text-muted">Trades taken</td><td style={{ textAlign: 'right' }}>{s.taken}</td></tr>
          <tr><td className="text-muted">With a result</td><td style={{ textAlign: 'right' }}>{s.resolved}</td></tr>
          <tr>
            <td className="text-muted">Win rate</td>
            <td style={{ textAlign: 'right' }}>
              {s.win_rate === null ? '—' : `${s.win_rate.toFixed(0)}%`}
              <div className="text-muted" style={{ fontSize: 11 }}>{range(s.win_rate_low, s.win_rate_high, (v) => `${v.toFixed(0)}%`)}</div>
            </td>
          </tr>
          <tr>
            <td className="text-muted">Average R</td>
            <td style={{ textAlign: 'right' }}>
              {formatR(s.avg_r)}
              <div className="text-muted" style={{ fontSize: 11 }}>{range(s.avg_r_low, s.avg_r_high, formatR)}</div>
            </td>
          </tr>
          <tr><td className="text-muted">Total R</td><td style={{ textAlign: 'right' }}>{formatR(s.total_r)}</td></tr>
        </tbody>
      </table>
      {s.small_sample && (
        <div className="text-muted" style={{ fontSize: 11, marginTop: 6 }}>Too few results to read much into.</div>
      )}
    </div>
  );
}

function FlipRow({ f }: { f: ReplayFlip }) {
  return (
    <tr>
      <td><TickerLink symbol={f.symbol} /></td>
      <td>{new Date(f.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</td>
      <td>{f.flip === 'now_taken' ? <span className="badge badge-green">Now taken</span> : <span className="badge badge-red">Now skipped</span>}</td>
      <td className={f.result_r === null ? '' : f.result_r > 0 ? 'text-green' : f.result_r < 0 ? 'text-red' : ''}>
        {f.result_state === 'resolved' ? formatR(f.result_r) : f.result_state === 'open' ? 'still open' : 'no result'}
      </td>
      <td className="text-muted">{f.why}</td>
      <td className="text-muted">{f.strategy_version === null ? '—' : `v${f.strategy_version}`}</td>
    </tr>
  );
}

/**
 * "What if?": pick a setting the stored decisions can be re-decided under, and see which past
 * decisions would have flipped and what they earned. Read-only: nothing is saved or switched on.
 * Only the settings that can be recomputed from stored rows are offered; sizing and exit
 * settings are not, and the card says so.
 */
export function ReplayCard() {
  const { mutate, isPending, data, error, reset } = useReplay();
  const [knob, setKnob] = useState<KnobKey>('min_confidence_for_trade');
  const [confidence, setConfidence] = useState(40);
  const [action, setAction] = useState<'cancel' | 'hold' | 'none'>('none');
  const [scores, setScores] = useState(false);
  const [directions, setDirections] = useState<'both' | 'long' | 'short'>('long');

  const run = () => {
    const overrides: ReplayOverrides =
      knob === 'min_confidence_for_trade'
        ? { min_confidence_for_trade: confidence }
        : knob === 'ai_overlay_objection_action'
          ? { ai_overlay_objection_action: action }
          : knob === 'ai_overlay_scores_confidence'
            ? { ai_overlay_scores_confidence: scores }
            : { allowed_directions: directions };
    mutate(overrides);
  };

  return (
    <div className="card" data-testid="replay-card">
      <h3>What if?</h3>
      <div className="text-muted" style={{ fontSize: 12, maxWidth: 680, marginTop: 2, marginBottom: 10 }}>
        Re-decide the plans already made under a different setting, before you switch it on. Only settings that decide
        take or skip can be replayed from stored rows; sizing, caps, slippage and exits cannot (use the Backtest Lab for
        those). Nothing is saved or changed here.
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={knob} onChange={(e) => { setKnob(e.target.value as KnobKey); reset(); }} aria-label="Setting to replay" style={{ width: "auto" }}>
          {KNOBS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
        {knob === 'min_confidence_for_trade' && (
          <input type="number" min={0} max={100} value={confidence} aria-label="Minimum confidence"
            onChange={(e) => setConfidence(Math.max(0, Math.min(100, Number(e.target.value) || 0)))} style={{ width: 80 }} />
        )}
        {knob === 'ai_overlay_objection_action' && (
          <select value={action} onChange={(e) => setAction(e.target.value as typeof action)} aria-label="Objection action" style={{ width: "auto" }}>
            <option value="cancel">Cancel the trade</option>
            <option value="hold">Hold for review</option>
            <option value="none">Do nothing</option>
          </select>
        )}
        {knob === 'ai_overlay_scores_confidence' && (
          <select value={scores ? 'yes' : 'no'} onChange={(e) => setScores(e.target.value === 'yes')} aria-label="Objection costs points" style={{ width: "auto" }}>
            <option value="yes">Costs points</option>
            <option value="no">Costs nothing</option>
          </select>
        )}
        {knob === 'allowed_directions' && (
          <select value={directions} onChange={(e) => setDirections(e.target.value as typeof directions)} aria-label="Directions" style={{ width: "auto" }}>
            <option value="both">Both</option>
            <option value="long">Long only</option>
            <option value="short">Short only</option>
          </select>
        )}
        <button type="button" className="btn btn-secondary" onClick={run} disabled={isPending}>
          {isPending ? 'Replaying…' : 'Replay'}
        </button>
      </div>

      {error && <ErrorBanner message={(error as ApiError).message} />}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 14 }} data-testid="replay-result">
          <div className="text-muted" style={{ fontSize: 12 }}>
            {data.decisions} stored decisions replayed{data.truncated ? ' (older ones were cut off)' : ''}.{' '}
            {data.fixed_count} cannot be moved by any of these settings.
            {data.versions.length > 0 && (
              <> Rules versions: {data.versions.map((v) => `${v.strategy_version === null ? 'unrecorded' : `v${v.strategy_version}`} (${v.decisions})`).join(', ')}.</>
            )}
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <StatsColumn title="What happened" s={data.before} />
            <StatsColumn title="With this setting" s={data.after} />
          </div>
          <div style={{ fontSize: 13 }}>
            <strong>{data.flipped_count}</strong> decision{data.flipped_count === 1 ? '' : 's'} would flip ({data.now_taken} now taken,{' '}
            {data.now_skipped} now skipped
            {data.flips_without_result > 0 ? `; ${data.flips_without_result} with no result yet, not counted in the rates` : ''}).
          </div>
          {data.flips.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: 13 }}>
                <thead>
                  <tr><th align="left">Symbol</th><th align="left">Date</th><th align="left">Change</th><th align="left">Result</th><th align="left">Why</th><th align="left">Rules</th></tr>
                </thead>
                <tbody>{data.flips.map((f) => <FlipRow key={f.plan_id} f={f} />)}</tbody>
              </table>
            </div>
          )}
          <details>
            <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>Limits of this replay</summary>
            <ul className="text-muted" style={{ fontSize: 12, margin: '6px 0 0', paddingLeft: 18 }}>
              {data.caveats.map((c) => <li key={c}>{c}</li>)}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}

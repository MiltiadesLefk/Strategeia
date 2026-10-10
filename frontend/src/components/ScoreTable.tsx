export interface ScorePart {
  label: string;
  value: number;
  /** What the part measures, in plain words. */
  help: string;
  /** The stored reason for this plan's score, when the backend kept one. */
  note?: string;
  /** Technical is a raw strength reading, not a signed vote for or against the trade. */
  unsigned?: boolean;
  /** Caution flags can only subtract; a 0 means "nothing flagged", not "neutral". */
  penaltyOnly?: boolean;
}

/** Zero has two meanings the engine used to leave unsaid: the part found nothing, or it had no data to look at. */
function zeroWhy(part: ScorePart): string {
  if (part.note) return part.note;
  return part.penaltyOnly
    ? 'Nothing flagged, so no penalty. (This plan did not store a reason.)'
    : 'Scored 0: the rule found nothing, or the data was missing. This plan did not store which.';
}

function rating(part: ScorePart, direction: 'long' | 'short' | null): { text: string; cls: string; why: string } {
  const side = direction === 'short' ? 'short' : 'long';
  if (part.unsigned) {
    return part.value > 0
      ? { text: 'Strong', cls: 'badge badge-green', why: `Technical setup earned ${part.value} points for the ${side}.` }
      : { text: 'Weak', cls: 'badge badge-neutral', why: 'The chart setup earned no points.' };
  }
  const base = part.note ? `${part.note} ` : '';
  if (part.value > 0) return { text: 'Supports', cls: 'badge badge-green', why: `${base}Adds ${part.value} to the ${side}.` };
  if (part.value < 0) return { text: 'Against', cls: 'badge badge-red', why: `${base}Costs ${-part.value} against the ${side}.` };
  return part.penaltyOnly
    ? { text: part.note?.startsWith('No data') ? 'No data' : 'Clear', cls: 'badge badge-neutral', why: zeroWhy(part) }
    : { text: part.note?.startsWith('No data') ? 'No data' : 'Neutral', cls: 'badge badge-neutral', why: zeroWhy(part) };
}

/** Every score part of a plan: its points, a rating, and what it measures, so a number is never a mystery. */
export interface CommitteeRow {
  rating: string | null;
  badgeClass: string;
  note: string | null;
  running: boolean;
  /** Whether the committee was switched on for this plan at all. */
  gateOn: boolean;
}

export function ScoreTable({
  parts,
  direction,
  committee,
}: {
  parts: ScorePart[];
  direction: 'long' | 'short' | null;
  committee?: CommitteeRow;
}) {
  // Strongest evidence first: against and supporting parts above the neutral ones.
  const sorted = [...parts].sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  return (
    <div className="score-table-wrap">
      <table className="score-table">
        <thead>
          <tr>
            <th>Part</th>
            <th style={{ textAlign: 'right' }}>Points</th>
            <th>Rating</th>
            <th>What it measures, and why it scored this way</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => {
            const r = rating(p, direction);
            return (
              <tr key={p.label} className={p.value === 0 ? 'score-row-quiet' : undefined}>
                <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{p.label}</td>
                <td
                  className={`tabular-nums ${p.value > 0 && !p.unsigned ? 'text-green' : p.value < 0 ? 'text-red' : ''}`}
                  style={{ textAlign: 'right', fontWeight: 600 }}
                >
                  {p.value > 0 && !p.unsigned ? '+' : ''}
                  {p.value}
                </td>
                <td>
                  <span className={r.cls}>{r.text}</span>
                </td>
                <td className="text-muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
                  <span style={{ color: 'var(--text)' }}>{r.why}</span> {p.help}
                </td>
              </tr>
            );
          })}
          {committee && (
            <tr>
              <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>AI Committee</td>
              <td className="text-muted" style={{ textAlign: 'right' }} title="The committee is an opinion, not a scored part">
                not scored
              </td>
              <td>
                {committee.rating ? (
                  <span className={committee.badgeClass}>{committee.rating}</span>
                ) : (
                  <span className="badge badge-neutral">{committee.running ? 'Reading' : 'No rating'}</span>
                )}
              </td>
              <td className="text-muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
                <span style={{ color: 'var(--text)' }}>
                  {committee.note ??
                    (committee.running
                      ? 'The committee is still reading this plan.'
                      : committee.gateOn
                        ? 'The committee did not read this plan.'
                        : 'The committee gate is off (Settings, AI tab), so it did not read this plan.')}
                </span>{' '}
                Analysts, bull and bear, and a risk debate end in a five-level rating. It adds no points: it can only stop a
                trade, never start or size one.
              </td>
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr>
            <td style={{ fontWeight: 700 }}>Total of the parts</td>
            <td className="tabular-nums" style={{ textAlign: 'right', fontWeight: 700 }}>
              {total > 0 ? '+' : ''}
              {total}
            </td>
            <td colSpan={2} className="text-muted" style={{ fontSize: 12 }}>
              Confidence is the share of achievable evidence points this setup earned, not this sum alone.
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

export interface ScorePart {
  label: string;
  value: number;
  /** What the part measures, in plain words. */
  help: string;
  /** The stored reason for this rating's score, when the backend kept one. */
  note?: string;
  /** Technical is a raw strength reading, not a signed vote for or against the stock. */
  unsigned?: boolean;
  /** Caution flags can only subtract; a 0 means "nothing flagged", not "neutral". */
  penaltyOnly?: boolean;
}

/** Zero has two meanings: the part found nothing, or it had no data to look at. */
function zeroWhy(part: ScorePart): string {
  if (part.note) return part.note;
  return part.penaltyOnly
    ? 'Nothing flagged, so no penalty.'
    : 'Scored 0: the rule found nothing, or the data was missing.';
}

function rating(part: ScorePart): { text: string; cls: string; why: string } {
  if (part.unsigned) {
    return part.value > 0
      ? { text: 'Strong', cls: 'badge badge-green', why: `Technical setup earned ${part.value} points.` }
      : { text: 'Weak', cls: 'badge badge-neutral', why: 'The chart setup earned no points.' };
  }
  const base = part.note ? `${part.note} · ` : '';
  if (part.value > 0) return { text: 'Supports', cls: 'badge badge-green', why: `${base}Adds ${part.value}.` };
  if (part.value < 0) return { text: 'Against', cls: 'badge badge-red', why: `${base}Costs ${-part.value}.` };
  const noData = part.note?.startsWith('No data');
  return { text: noData ? 'No data' : part.penaltyOnly ? 'Clear' : 'Neutral', cls: 'badge badge-neutral', why: zeroWhy(part) };
}

/** Every part of a rating: its points, a verdict, and what it measures, so a number is never a mystery. */
export function ScoreTable({ parts, summary }: { parts: ScorePart[]; summary?: string }) {
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
            const r = rating(p);
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
        </tbody>
        <tfoot>
          <tr>
            <td style={{ fontWeight: 700 }}>Total of the parts</td>
            <td className="tabular-nums" style={{ textAlign: 'right', fontWeight: 700 }}>
              {total > 0 ? '+' : ''}
              {total}
            </td>
            <td colSpan={2} className="text-muted" style={{ fontSize: 12 }}>
              {summary}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

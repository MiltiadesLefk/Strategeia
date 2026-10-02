import { useState, type ReactNode } from 'react';
import {
  useAddThesisItem,
  useAddThesisNote,
  useRecheckThesis,
  useRemoveThesisItem,
  useReviewThesis,
  useSetThesisPillarStatus,
  useSettingsStatus,
  useThesis,
} from '../api/hooks';
import type { ApiError } from '../api/client';
import type { Thesis, ThesisItemKind, ThesisPillar, ThesisPillarStatus } from '../api/types';
import { AiNoteCard, formatRelativeTime } from './common';

// The "Thesis" panel on an open position's card: why the trade was taken (pillars, risks,
// catalysts), whether it still holds, and a dated log. Pillars the rules can judge from price
// data (trend, stop, weekly, market) are re-checked automatically and read-only here; the rest
// record what the plan cited and can be marked by hand. Nothing in this panel changes the
// position itself.

const STATUS_LABEL: Record<ThesisPillarStatus, string> = { intact: 'Intact', at_risk: 'At risk', broken: 'Broken' };
const STATUS_BADGE: Record<ThesisPillarStatus, string> = { intact: 'badge-green', at_risk: 'badge-amber', broken: 'badge-red' };
const MECHANICAL_KEYS = ['trend', 'stop', 'weekly', 'market'];
const LOG_PREVIEW = 6;
const smallButton = { padding: '2px 8px', fontSize: 12 } as const;

function countdown(days: number | null): string {
  if (days === null) return 'no date';
  if (days < 0) return `passed ${-days} day${days === -1 ? '' : 's'} ago`;
  if (days === 0) return 'today';
  return `in ${days} day${days === 1 ? '' : 's'}`;
}

/** Small red badge for the card header; renders nothing unless the thesis is broken. */
export function ThesisBrokenBadge({ positionId }: { positionId: number }) {
  const { data } = useThesis(positionId);
  if (!data?.thesis?.thesis_broken) return null;
  return (
    <span className="badge badge-red" title="The core trend pillar of this position's thesis no longer holds" data-testid="thesis-broken-badge">
      Thesis broken
    </span>
  );
}

function summary(thesis: Thesis): string {
  const counts = { intact: 0, at_risk: 0, broken: 0 };
  thesis.pillars.forEach((p) => (counts[p.status] += 1));
  return [`${counts.intact} intact`, counts.at_risk ? `${counts.at_risk} at risk` : '', counts.broken ? `${counts.broken} broken` : '']
    .filter(Boolean)
    .join(' · ');
}

function PillarRow({ pillar, positionId }: { pillar: ThesisPillar; positionId: number }) {
  const setStatus = useSetThesisPillarStatus(positionId);
  const remove = useRemoveThesisItem(positionId);
  const mechanical = MECHANICAL_KEYS.includes(pillar.key);
  return (
    <li style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      {mechanical ? (
        <span className={`badge ${STATUS_BADGE[pillar.status]}`}>{STATUS_LABEL[pillar.status]}</span>
      ) : (
        <select
          value={pillar.status}
          aria-label={`Status of: ${pillar.text}`}
          onChange={(e) => setStatus.mutate({ id: pillar.id, status: e.target.value as ThesisPillarStatus })}
          style={{ width: 'auto', padding: '2px 6px', fontSize: 12 }}
          title="This pillar is not re-checked by rules; mark it yourself"
        >
          {(Object.keys(STATUS_LABEL) as ThesisPillarStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      )}
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 13 }}>
          {pillar.text}
          {pillar.core && (
            <span className="text-muted" style={{ fontSize: 11, marginLeft: 6 }}>
              (core)
            </span>
          )}
        </div>
        <div className="text-muted" style={{ fontSize: 11 }}>
          {pillar.detail
            ? `Last check: ${pillar.detail}`
            : mechanical
              ? 'Not re-checked yet'
              : pillar.source === 'user'
                ? 'Added by you'
                : 'From the trade plan; not re-checked by rules'}
        </div>
      </div>
      {!pillar.core && (
        <button className="btn btn-secondary" style={smallButton} onClick={() => remove.mutate(pillar.id)} aria-label={`Remove: ${pillar.text}`}>
          Remove
        </button>
      )}
    </li>
  );
}

function AddItemForm({ positionId }: { positionId: number }) {
  const add = useAddThesisItem(positionId);
  const [kind, setKind] = useState<ThesisItemKind>('pillar');
  const [text, setText] = useState('');
  const [date, setDate] = useState('');
  function submit() {
    if (!text.trim()) return;
    add.mutate(
      { kind, text: text.trim(), date: kind === 'catalyst' && date ? date : null },
      {
        onSuccess: () => {
          setText('');
          setDate('');
        },
      },
    );
  }
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <select value={kind} onChange={(e) => setKind(e.target.value as ThesisItemKind)} style={{ width: 'auto' }} aria-label="Kind of item to add">
        <option value="pillar">Pillar</option>
        <option value="risk">Risk</option>
        <option value="catalyst">Catalyst</option>
      </select>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Add your own pillar, risk or catalyst"
        style={{ flex: 1, minWidth: 200 }}
        maxLength={600}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
      />
      {kind === 'catalyst' && <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ width: 'auto' }} aria-label="Catalyst date" />}
      <button className="btn btn-secondary" onClick={submit} disabled={add.isPending || !text.trim()}>
        Add
      </button>
      {add.error && (
        <span className="text-red" style={{ fontSize: 12 }}>
          {(add.error as ApiError).message}
        </span>
      )}
    </div>
  );
}

function NoteForm({ positionId }: { positionId: number }) {
  const addNote = useAddThesisNote(positionId);
  const [text, setText] = useState('');
  function submit() {
    if (!text.trim()) return;
    addNote.mutate(text.trim(), { onSuccess: () => setText('') });
  }
  return (
    <div style={{ display: 'flex', gap: 8 }}>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Add a dated note to the log"
        style={{ flex: 1 }}
        maxLength={2000}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
      />
      <button className="btn btn-secondary" onClick={submit} disabled={addNote.isPending || !text.trim()}>
        Add note
      </button>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

const listStyle = { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 } as const;

function ThesisBody({ thesis, positionId }: { thesis: Thesis; positionId: number }) {
  const recheck = useRecheckThesis(positionId);
  const review = useReviewThesis(positionId);
  const remove = useRemoveThesisItem(positionId);
  const aiOnline = useSettingsStatus().data?.ai_online ?? false;
  const [showAllLog, setShowAllLog] = useState(false);
  const log = thesis.log.slice().reverse();
  const shownLog = showAllLog ? log : log.slice(0, LOG_PREVIEW);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {thesis.thesis_broken && (
        <div className="badge badge-red" style={{ padding: '8px 10px', whiteSpace: 'normal' }} role="alert">
          Thesis broken: the core trend pillar no longer holds for this {thesis.direction}. The exit rules still decide whether the position closes.
        </div>
      )}

      <Section title="Pillars">
        <ul style={listStyle}>
          {thesis.pillars.map((p) => (
            <PillarRow key={p.id} pillar={p} positionId={positionId} />
          ))}
        </ul>
      </Section>

      <Section title="Risks">
        {thesis.risks.length === 0 ? (
          <div className="text-muted" style={{ fontSize: 13 }}>
            None recorded.
          </div>
        ) : (
          <ul style={listStyle}>
            {thesis.risks.map((r) => (
              <li key={r.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, fontSize: 13 }}>{r.text}</div>
                <button className="btn btn-secondary" style={smallButton} onClick={() => remove.mutate(r.id)} aria-label={`Remove: ${r.text}`}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Catalysts">
        {thesis.catalysts.length === 0 ? (
          <div className="text-muted" style={{ fontSize: 13 }}>
            None recorded.
          </div>
        ) : (
          <ul style={listStyle}>
            {thesis.catalysts.map((c) => (
              <li key={c.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, fontSize: 13 }}>
                  {c.text}
                  <span className="text-muted tabular-nums" style={{ marginLeft: 8, fontSize: 12 }}>
                    {c.date ? `${c.date} · ${countdown(c.days_until)}` : 'no date'}
                  </span>
                </div>
                <button className="btn btn-secondary" style={smallButton} onClick={() => remove.mutate(c.id)} aria-label={`Remove: ${c.text}`}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <AddItemForm positionId={positionId} />

      <Section title="Log">
        <ul style={listStyle}>
          {shownLog.map((e, i) => (
            <li key={`${e.at}-${i}`} style={{ fontSize: 13, display: 'flex', gap: 8 }}>
              <span className="text-muted tabular-nums" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                {new Date(e.at).toLocaleDateString('en-CA')}
              </span>
              <span>
                {e.kind === 'note' && (
                  <span className="badge badge-neutral" style={{ marginRight: 6 }}>
                    your note
                  </span>
                )}
                {e.text}
              </span>
            </li>
          ))}
        </ul>
        {log.length > LOG_PREVIEW && (
          <button className="btn btn-secondary" style={{ ...smallButton, marginTop: 6 }} onClick={() => setShowAllLog(!showAllLog)}>
            {showAllLog ? 'Show fewer' : `Show all ${log.length}`}
          </button>
        )}
      </Section>

      <NoteForm positionId={positionId} />

      {thesis.review_text && (
        <AiNoteCard
          label="AI thesis review (a note, not a fact)"
          provider={[thesis.review_provider, thesis.review_model].filter(Boolean).join(' · ')}
          text={thesis.review_text}
          error={thesis.review_error ? `The last rewrite failed: ${thesis.review_error}` : null}
        />
      )}
      {!thesis.review_text && thesis.review_error && (
        <div className="text-red" style={{ fontSize: 12 }}>
          The AI review failed: {thesis.review_error}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" onClick={() => recheck.mutate()} disabled={recheck.isPending}>
          {recheck.isPending ? 'Checking…' : 'Re-check now'}
        </button>
        <button
          className="btn btn-secondary"
          onClick={() => review.mutate()}
          disabled={!aiOnline || review.isPending}
          title={aiOnline ? 'Ask your AI for a short read of where the thesis stands (uses one AI call)' : 'No AI provider is configured'}
        >
          {review.isPending ? 'Reviewing…' : thesis.review_text ? 'Rewrite AI review' : 'AI review'}
        </button>
        <span className="text-muted" style={{ fontSize: 11 }}>
          {thesis.last_checked_at ? `Rules last checked ${formatRelativeTime(thesis.last_checked_at)}` : 'Not re-checked by rules yet'}
        </span>
        {recheck.data && recheck.data.status !== 'checked' && (
          <span className="text-muted" style={{ fontSize: 12 }}>
            {recheck.data.note}
          </span>
        )}
        {(recheck.error || review.error) && (
          <span className="text-red" style={{ fontSize: 12 }}>
            {((recheck.error ?? review.error) as ApiError).message}
          </span>
        )}
      </div>
    </div>
  );
}

/** Collapsible thesis panel for one open position. Collapsed by default; fetches nothing extra
 *  beyond the (cached, read-only) thesis the card header already needs for its warning badge. */
export function ThesisPanel({ positionId }: { positionId: number }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useThesis(positionId);
  const recheck = useRecheckThesis(positionId);
  const thesis = data?.thesis ?? null;

  return (
    <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }} data-testid="thesis-panel">
      <button
        className="btn btn-secondary"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <span>{open ? '▾' : '▸'} Thesis</span>
        {thesis && <span className="text-muted" style={{ fontSize: 12 }}>{summary(thesis)}</span>}
        {thesis?.thesis_broken && <span className="badge badge-red">Broken</span>}
      </button>
      {open && (
        <div style={{ marginTop: 12 }}>
          {isLoading ? (
            <div className="text-muted">Loading thesis…</div>
          ) : thesis ? (
            <ThesisBody thesis={thesis} positionId={positionId} />
          ) : (
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="text-muted" style={{ fontSize: 13 }}>
                No thesis recorded for this position yet. It is created automatically within a few minutes of opening.
              </span>
              <button className="btn btn-secondary" onClick={() => recheck.mutate()} disabled={recheck.isPending}>
                {recheck.isPending ? 'Creating…' : 'Create from the trade plan now'}
              </button>
              {recheck.error && (
                <span className="text-red" style={{ fontSize: 12 }}>
                  {(recheck.error as ApiError).message}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

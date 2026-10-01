import { useWriteLesson } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { Position } from '../api/types';

// The "Lesson" column of the closed-positions table and the row that opens under a
// trade. A lesson is 2-4 sentences an AI wrote after the trade closed, from the trade's
// figures, SPY over the same period and the plan's recorded reasoning. It is a note, not
// a fact, and is labelled that way. When the AI could not write one nothing is shown in
// its place: there is no template, only the reason it failed.

const smallButton = { padding: '2px 8px', fontSize: 12 } as const;

function dateOnly(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString('en-CA') : '';
}

/** The table cell: opens/closes the lesson, or offers to write one. */
export function LessonCell({
  position,
  open,
  onToggle,
  aiOnline,
}: {
  position: Position;
  open: boolean;
  onToggle: () => void;
  aiOnline: boolean;
}) {
  const { mutate, isPending, error } = useWriteLesson();
  if (position.lesson_text) {
    return (
      <button className="btn btn-secondary" style={smallButton} onClick={onToggle} aria-expanded={open}>
        {open ? 'Hide lesson' : 'Lesson'}
      </button>
    );
  }
  if (!aiOnline) {
    return (
      <span className="text-muted" title="No AI provider is configured, so no lesson can be written.">
        —
      </span>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'flex-start' }}>
      <button className="btn btn-secondary" style={smallButton} onClick={() => mutate(position.id)} disabled={isPending}>
        {isPending ? 'Writing…' : 'Write lesson'}
      </button>
      {position.lesson_error && (
        <span className="text-red" style={{ fontSize: 11 }} title={position.lesson_error}>
          last try failed
        </span>
      )}
      {error && (
        <span className="text-red" style={{ fontSize: 11 }}>
          {(error as ApiError).message}
        </span>
      )}
    </div>
  );
}

/** The row that opens under a trade: the lesson text, who wrote it, and a rewrite action. */
export function LessonDetailRow({ position, colSpan, aiOnline }: { position: Position; colSpan: number; aiOnline: boolean }) {
  const { mutate, isPending, error } = useWriteLesson();
  const writtenBy = [position.lesson_provider, position.lesson_model].filter(Boolean).join(' · ');
  return (
    <tr>
      <td colSpan={colSpan} style={{ background: 'var(--card-alt)' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '4px 0' }}>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{position.lesson_text}</div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            AI-written note, not a fact{writtenBy ? ` · ${writtenBy}` : ''}
            {position.lesson_at && !position.lesson_error ? ` · ${dateOnly(position.lesson_at)}` : ''}
          </div>
          {position.lesson_error && (
            <div className="text-red" style={{ fontSize: 12 }}>
              The last attempt to rewrite it failed: {position.lesson_error}
            </div>
          )}
          {error && (
            <div className="text-red" style={{ fontSize: 12 }}>
              {(error as ApiError).message}
            </div>
          )}
          {aiOnline && (
            <div>
              <button className="btn btn-secondary" style={smallButton} onClick={() => mutate(position.id)} disabled={isPending}>
                {isPending ? 'Rewriting…' : 'Rewrite lesson'}
              </button>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

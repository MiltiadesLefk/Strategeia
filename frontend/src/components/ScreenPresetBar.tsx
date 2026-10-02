import { useMemo } from 'react';
import type { ApiError } from '../api/client';
import { useRunScreenPreset, useScreenPresets } from '../api/hooks';
import type { ScreenPresetMatch, ScreenPresetRun } from '../api/types';
import { ErrorBanner, LoadingSpinner } from './common';

// Preset chips above the Market Scan table. Choosing one runs it on the server over the first
// part of the watchlist (the response says how many were checked) and the table then shows only
// the matches, each annotated with the criteria it met. Presets are screens for candidates to
// look at, not signals; criteria our data cannot answer are listed as unavailable, never faked.

/** Run result for the active preset, shared with the table so it can filter and annotate rows. */
export function useActivePreset(name: string | null) {
  const run = useRunScreenPreset(name);
  const matchBySymbol = useMemo(
    () => new Map<string, ScreenPresetMatch>((run.data?.matches ?? []).map((m) => [m.symbol, m])),
    [run.data],
  );
  return { run, matchBySymbol };
}

/** The criteria a matched row met (green), could not be told (grey), or did not meet (hidden). */
export function PresetMatchChips({ match }: { match: ScreenPresetMatch }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {match.criteria
        .filter((c) => c.ok !== false)
        .map((c) => (
          <span key={c.id} className={`badge ${c.ok ? 'badge-green' : 'badge-neutral'}`} title={c.detail} style={{ whiteSpace: 'normal' }}>
            {c.ok ? c.label : `${c.label}: n/a`}
          </span>
        ))}
    </div>
  );
}

export function ScreenPresetBar({
  selected,
  onSelect,
  run,
  isLoading,
  error,
}: {
  selected: string | null;
  onSelect: (name: string | null) => void;
  run: ScreenPresetRun | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const { data: presets } = useScreenPresets();
  if (!presets) return null;
  const active = presets.find((p) => p.name === selected);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }} data-testid="screen-presets">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="text-muted" style={{ fontSize: 12 }}>
          Screens:
        </span>
        {presets.map((p) => (
          <button
            key={p.name}
            className={`btn ${selected === p.name ? 'btn-primary' : 'btn-secondary'}`}
            style={{ padding: '4px 12px', fontSize: 13 }}
            aria-pressed={selected === p.name}
            title={p.description}
            onClick={() => onSelect(selected === p.name ? null : p.name)}
          >
            {p.label}
          </button>
        ))}
        {selected && (
          <button className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: 13 }} onClick={() => onSelect(null)}>
            Clear
          </button>
        )}
      </div>

      {active && (
        <div className="text-muted" style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div>
            {active.description} A symbol matches when it meets{' '}
            {active.criteria
              .filter((c) => c.required)
              .map((c) => `"${c.label}"`)
              .join(', ')}{' '}
            and at least {active.min_matches} criteria in all.
          </div>
          {isLoading && <LoadingSpinner label={`Screening for ${active.label.toLowerCase()}…`} />}
          {run && (
            <div>
              {run.note} {run.matches.length} matched
              {run.unjudged.length > 0 ? `; ${run.unjudged.length} could not be judged because data was missing (${run.unjudged.slice(0, 5).join(', ')}${run.unjudged.length > 5 ? '…' : ''})` : ''}
              . Screens surface candidates to study; they are not trade signals.
            </div>
          )}
          <details>
            <summary style={{ cursor: 'pointer' }}>Criteria from the original screen we cannot check ({active.unavailable.length})</summary>
            <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
              {active.unavailable.map((u) => (
                <li key={u.label}>
                  {u.label}: {u.reason}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
      {error ? <ErrorBanner message={(error as ApiError).message} /> : null}
    </div>
  );
}

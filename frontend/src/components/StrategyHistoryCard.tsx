import { useState } from 'react';
import { useStrategyVersions } from '../api/hooks';
import { EmptyState, ErrorBanner, LoadingSpinner } from './common';

const COLLAPSED_COUNT = 4;

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** Which rules and settings each plan was made under. A version is created
 *  the first time a plan is generated under a new combination of decision-
 *  relevant settings and rule constants, so this list is only what plans
 *  actually used. */
export function StrategyHistoryCard() {
  const { data, isLoading, error, refetch } = useStrategyVersions();
  const [showAll, setShowAll] = useState(false);

  const versions = data?.versions ?? [];
  const shown = showAll ? versions : versions.slice(0, COLLAPSED_COUNT);

  return (
    <div className="card">
      <h3 style={{ marginBottom: 4 }}>Strategy history</h3>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 12, maxWidth: 640 }}>
        Each plan records the version of the rules and settings it was made under, so results can be compared across
        changes. A new version appears with the first plan generated after a decision-relevant setting (or a rule in the
        code) changes.
      </div>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={(error as Error).message} onRetry={() => refetch()} />}
      {data && versions.length === 0 && <EmptyState>No versions yet: the first plan you generate creates version 1.</EmptyState>}
      {data && data.versions.length > 0 && data.current_number === null && (
        <div className="text-muted" style={{ fontSize: 12, marginBottom: 10 }}>
          Your current settings have not produced a plan yet; they become version {versions[0].number + 1} with the next one.
        </div>
      )}
      {shown.map((v) => (
        <div key={v.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="badge badge-neutral">v{v.number}</span>
            {v.number === data?.current_number && <span className="badge badge-green">current</span>}
            <span className="text-muted" style={{ fontSize: 12 }}>
              {formatDate(v.created_at)} · {v.plans} plan{v.plans === 1 ? '' : 's'}
              {v.no_trades > 0 ? ` (${v.no_trades} no-trade)` : ''} · {v.closed_trades} closed trade
              {v.closed_trades === 1 ? '' : 's'}
            </span>
            {v.label && <span style={{ fontSize: 12 }}>{v.label}</span>}
          </div>
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            {v.changes.length === 0 ? (
              v.number === versions[versions.length - 1].number ? 'First recorded version.' : 'No differences recorded.'
            ) : (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {v.changes.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ))}
      {versions.length > COLLAPSED_COUNT && (
        <button type="button" className="btn btn-secondary" style={{ marginTop: 8 }} onClick={() => setShowAll((s) => !s)}>
          {showAll ? 'Show fewer' : `Show all ${versions.length} versions`}
        </button>
      )}
      {data && data.unversioned_plans > 0 && (
        <div className="text-muted" style={{ fontSize: 12, marginTop: 10 }}>
          {data.unversioned_plans} earlier plan{data.unversioned_plans === 1 ? '' : 's'} predate versioning and carry no version.
        </div>
      )}
    </div>
  );
}

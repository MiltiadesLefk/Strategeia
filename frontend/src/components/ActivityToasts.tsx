import { dismissActivity, useActivities } from '../lib/activity';

/** Bottom-right stack: what is running now, and how the last actions ended. */
export function ActivityToasts() {
  const activities = useActivities();
  if (activities.length === 0) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {activities.map((a) => (
        <div key={a.id} className={`toast toast-${a.status}`}>
          <span className="toast-icon" aria-hidden="true">
            {a.status === 'running' ? <span className="spinner-dot" /> : a.status === 'done' ? '✓' : '!'}
          </span>
          <div className="toast-body">
            <div className="toast-title">{a.status === 'running' ? `${a.label}…` : a.label}</div>
            {a.message && <div className="toast-message">{a.message}</div>}
          </div>
          {a.status !== 'running' && (
            <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => dismissActivity(a.id)}>
              ×
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

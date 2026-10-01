import { useEffect, useState } from 'react';
import { useRunWatcher, useSetWatcherEnabled, useSettings, useUpdateSettings, useWatcherEvents, useWatchers } from '../api/hooks';
import type { WatchersAction } from '../api/types';
import { EmptyState, ErrorBanner, LoadingSpinner, ToggleSwitch, formatRelativeTime, isSafeHttpUrl } from './common';

const ACTIONS: { value: WatchersAction; label: string; help: string }[] = [
  {
    value: 'record',
    label: 'Record only',
    help: 'Every event is saved with the time it became public. Nothing else happens.',
  },
  {
    value: 'alert',
    label: 'Record and alert',
    help: 'Also sends a Telegram message (needs Telegram set up above) with a link to the source.',
  },
  {
    value: 'alert_and_reevaluate',
    label: 'Record, alert and re-evaluate',
    help:
      'Also runs a full evaluation of the symbol, the same one the auto-scan runs. While the market is closed the ' +
      'evaluation is queued for shortly after the next open instead of using a stale price. An event never changes ' +
      'a trade by itself.',
  },
];

function interval(seconds: number): string {
  return seconds >= 3600 ? `${(seconds / 3600).toFixed(1).replace(/\.0$/, '')} h` : `${Math.round(seconds / 60)} min`;
}

/** The Watchers card on the Settings page: the master switch, what an event does,
 *  how often the scheduler checks, the installed watchers and the events they found.
 *  Saves on its own (it is not part of the Settings page's other forms). */
export function WatchersCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const watchers = useWatchers();
  const events = useWatcherEvents(10);
  const run = useRunWatcher();
  const setEnabled = useSetWatcherEnabled();

  const [enabled, setEnabledDraft] = useState(false);
  const [action, setAction] = useState<WatchersAction>('alert_and_reevaluate');
  const [minutes, setMinutes] = useState(5);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setEnabledDraft(settings.watchers_enabled);
    setAction(settings.watchers_action);
    setMinutes(settings.watchers_poll_minutes);
  }, [settings]);

  const minutesValid = Number.isInteger(minutes) && minutes >= 1 && minutes <= 60;
  const helpText = ACTIONS.find((a) => a.value === action)?.help;

  function save() {
    update.mutate(
      { watchers_enabled: enabled, watchers_action: action, watchers_poll_minutes: minutes },
      {
        onSuccess: () => {
          setSaved(true);
          window.setTimeout(() => setSaved(false), 2000);
        },
      },
    );
  }

  const list = watchers.data?.watchers ?? [];

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3>Watchers</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        A watcher checks one source in the background (a new filing, a headline) and reports events. Every event is
        saved with the time it became public, so it can be tested in a backtest later. This does not place trades.
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ToggleSwitch checked={enabled} onChange={setEnabledDraft} label="Watchers" />
        <span className={enabled ? 'badge badge-green' : 'badge badge-neutral'}>{enabled ? 'On' : 'Off'}</span>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Off by default. No watchers are installed yet, so switching this on does nothing until one is added.
      </div>

      <div>
        <label>When a watcher finds something</label>
        <select value={action} onChange={(e) => setAction(e.target.value as WatchersAction)}>
          {ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
        <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
          {helpText}
        </div>
      </div>

      <div>
        <label>Check for due watchers every (minutes)</label>
        <input
          type="number"
          value={minutes}
          min={1}
          max={60}
          step={1}
          onChange={(e) => setMinutes(Number(e.target.value))}
        />
        <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
          1 to 60. Each watcher also has its own, usually slower, interval. A change takes effect after the app restarts.
        </div>
        {!minutesValid && (
          <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
            Enter a whole number from 1 to 60.
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn btn-primary" disabled={update.isPending || !minutesValid} onClick={save}>
          {update.isPending ? 'Saving…' : saved ? 'Saved' : 'Save'}
        </button>
        {update.error && <span className="text-red">{(update.error as Error).message}</span>}
      </div>

      <h4 style={{ marginTop: 8 }}>Installed watchers</h4>
      {watchers.isLoading && <LoadingSpinner />}
      {watchers.error && <ErrorBanner message={(watchers.error as Error).message} onRetry={() => watchers.refetch()} />}
      {watchers.data && list.length === 0 && <EmptyState>No watchers are installed yet.</EmptyState>}
      {list.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Watcher</th>
                <th>On</th>
                <th>Every</th>
                <th>Last run</th>
                <th>Last success</th>
                <th>Failures</th>
                <th>Fired today</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((w) => (
                <tr key={w.name}>
                  <td title={w.description}>
                    {w.name}
                    {w.last_error && (
                      <div className="text-red" style={{ fontSize: 12 }}>
                        {w.last_error}
                      </div>
                    )}
                  </td>
                  <td>
                    <ToggleSwitch
                      checked={w.enabled}
                      onChange={(value) => setEnabled.mutate({ name: w.name, enabled: value })}
                      label={`${w.name} enabled`}
                    />
                  </td>
                  <td className="tabular-nums">{interval(w.poll_interval_seconds)}</td>
                  <td>{formatRelativeTime(w.last_run_at)}</td>
                  <td>{formatRelativeTime(w.last_success_at)}</td>
                  <td className="tabular-nums">{w.consecutive_failures}</td>
                  <td className="tabular-nums">
                    {w.fires_today} / {w.daily_fire_cap}
                  </td>
                  <td>
                    <button className="btn btn-secondary" disabled={run.isPending} onClick={() => run.mutate(w.name)}>
                      Run now
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {run.error && <ErrorBanner message={(run.error as Error).message} />}
      {run.data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {run.data.skipped_reason
            ? `Skipped: ${run.data.skipped_reason.replace(/_/g, ' ')}.`
            : run.data.error
              ? `Failed: ${run.data.error}`
              : `${run.data.new_events} new event(s), ${run.data.fired} fired, ${run.data.suppressed} held back, ${run.data.duplicates} already seen.`}
        </div>
      )}

      <h4 style={{ marginTop: 8 }}>Recent events</h4>
      {events.isLoading && <LoadingSpinner />}
      {events.data && events.data.length === 0 && <EmptyState>No events recorded yet.</EmptyState>}
      {events.data && events.data.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {events.data.map((e) => (
            <div key={e.id} style={{ fontSize: 13 }}>
              <span className="text-muted">{formatRelativeTime(e.known_at)}</span> <span className="badge badge-neutral">{e.watcher}</span>{' '}
              {e.symbol && <strong>{e.symbol} </strong>}
              {e.source_ref && isSafeHttpUrl(e.source_ref) ? (
                <a href={e.source_ref} target="_blank" rel="noopener noreferrer">
                  {e.headline}
                </a>
              ) : (
                e.headline
              )}
              {e.suppressed_reason && <span className="text-muted"> (held back: {e.suppressed_reason})</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

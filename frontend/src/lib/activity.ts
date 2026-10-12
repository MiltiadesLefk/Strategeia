import { useSyncExternalStore } from 'react';

/**
 * Feedback for every write the user triggers. api/client.ts reports each non-GET request here; the
 * ActivityToasts component shows it while it runs and says how it ended. Components keep their own
 * pending/error handling: this is the layer underneath that guarantees something is always visible.
 */
export type ActivityStatus = 'running' | 'done' | 'failed';

export interface Activity {
  id: number;
  label: string;
  status: ActivityStatus;
  message?: string;
}

/** A request that finishes faster than this never shows a "running" toast (no flicker), nor a "done" one. */
const SHOW_AFTER_MS = 250;
const DONE_VISIBLE_MS = 3000;
const FAILED_VISIBLE_MS = 9000;

// [pattern, what is running, what happened]. First match wins; the fallback names the path.
const LABELS: [RegExp, string, string][] = [
  [/^\/api\/trade-plans\/generate/, 'Generating trade plan', 'Trade plan ready'],
  [/^\/api\/scan\/auto-trade/, 'Scanning and evaluating symbols', 'Scan finished'],
  [/^\/api\/portfolio\/reset/, 'Resetting paper account', 'Paper account reset'],
  [/^\/api\/portfolio\/positions\/\d+\/close/, 'Closing position', 'Position closed'],
  [/^\/api\/portfolio\/positions\/\d+\/lesson/, 'Writing trade lesson', 'Lesson written'],
  [/^\/api\/portfolio\/positions\/\d+\/thesis\/(recheck|review)/, 'Checking thesis', 'Thesis checked'],
  [/^\/api\/portfolio\/positions/, 'Updating position', 'Position updated'],
  [/^\/api\/trade-plans/, 'Updating trade plans', 'Trade plans updated'],
  [/^\/api\/backtests\/validate/, 'Starting validation', 'Validation started'],
  [/^\/api\/backtests\/.*cancel/, 'Cancelling run', 'Run cancelled'],
  [/^\/api\/backtests/, 'Starting backtest', 'Backtest started'],
  [/^\/api\/committee\/runs/, 'Starting AI Committee run', 'Committee run started'],
  [/^\/api\/screener\/run/, 'Running screener', 'Screener finished'],
  [/^\/api\/screener\/saved/, 'Saving screen', 'Screen saved'],
  [/^\/api\/smart-money\/review\/\d+\/buy/, 'Buying', 'Buy submitted'],
  [/^\/api\/smart-money\/review\/\d+\/skip/, 'Skipping', 'Skipped'],
  [/^\/api\/smart-money\/\w+\/refresh/, 'Loading filings', 'Filings loaded'],
  [/^\/api\/signals\/finra\/refresh/, 'Loading FINRA short volume', 'FINRA data loaded'],
  [/^\/api\/missed-trades\/refresh/, 'Replaying declined trades', 'Replay finished'],
  [/^\/api\/replay/, 'Replaying plans', 'Replay finished'],
  [/^\/api\/news\/(collect|label)/, 'Collecting news', 'News updated'],
  [/^\/api\/forecast-lab\/models\/train/, 'Training model', 'Model trained'],
  [/^\/api\/forecast-lab/, 'Updating Forecast Lab', 'Forecast Lab updated'],
  [/^\/api\/settings\/test-connection/, 'Testing connection', 'Connection test finished'],
  [/^\/api\/settings/, 'Saving settings', 'Settings saved'],
  [/^\/api\/data-sources\/.*probe/, 'Probing data source', 'Probe finished'],
  [/^\/api\/notes\/.*send/, 'Sending note', 'Note sent'],
  [/^\/api\/notes\//, 'Preparing note', 'Note ready'],
  [/^\/api\/cache\/clear/, 'Clearing cache', 'Cache cleared'],
  [/^\/api\/sleeves/, 'Updating sleeve', 'Sleeve updated'],
  [/^\/api\/watchers/, 'Updating watcher', 'Watcher updated'],
  [/^\/api\/watchlist/, 'Updating watchlist', 'Watchlist updated'],
  [/^\/api\/alerts/, 'Updating alert', 'Alert updated'],
];

// Reads sent as POST, or calls that have their own screen, stay silent.
const SILENT = [/^\/api\/auth\//, /^\/api\/watchlist\/validate/];

function describe(path: string): { running: string; done: string } | null {
  const clean = path.split('?')[0];
  if (SILENT.some((re) => re.test(clean))) return null;
  for (const [re, running, done] of LABELS) if (re.test(clean)) return { running, done };
  const last = clean.split('/').filter(Boolean).pop() ?? 'request';
  const words = last.replace(/[-_]/g, ' ');
  const text = words.charAt(0).toUpperCase() + words.slice(1);
  return { running: text, done: `${text} done` };
}

let items: Activity[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function publish(next: Activity[]) {
  items = next;
  listeners.forEach((l) => l());
}

function remove(id: number) {
  publish(items.filter((a) => a.id !== id));
}

export function dismissActivity(id: number) {
  remove(id);
}

/** Wraps one write request. Returns the promise unchanged; only reports to the toast layer. */
export function trackWrite<T>(path: string, run: () => Promise<T>): Promise<T> {
  const info = describe(path);
  if (!info) return run();
  const id = nextId++;
  let shown = false;
  const timer = setTimeout(() => {
    shown = true;
    publish([...items, { id, label: info.running, status: 'running' }]);
  }, SHOW_AFTER_MS);
  return run().then(
    (value) => {
      clearTimeout(timer);
      if (shown) {
        publish(items.map((a) => (a.id === id ? { ...a, status: 'done', label: info.done } : a)));
        setTimeout(() => remove(id), DONE_VISIBLE_MS);
      }
      return value;
    },
    (error: unknown) => {
      clearTimeout(timer);
      const message = error instanceof Error ? error.message : 'Request failed';
      const failed: Activity = { id, label: `${info.running} failed`, status: 'failed', message };
      publish(shown ? items.map((a) => (a.id === id ? failed : a)) : [...items, failed]);
      setTimeout(() => remove(id), FAILED_VISIBLE_MS);
      throw error;
    },
  );
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useActivities(): Activity[] {
  return useSyncExternalStore(subscribe, () => items);
}

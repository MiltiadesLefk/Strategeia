import { useState } from 'react';
import { useCreatePriceAlert, useDeletePriceAlert, usePriceAlerts } from '../api/hooks';
import type { PriceAlert, PriceAlertCondition, PriceAlertUnit } from '../api/types';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from './common';
import { TickerLink } from './TickerLink';

const CONDITION_LABELS: Record<PriceAlertCondition, string> = {
  price_above: 'Price at or above',
  price_below: 'Price at or below',
  day_move_pct: 'Moves at least (% in a day, up or down)',
  near_stop: 'Gets close to my stop (open position)',
  near_tp1: 'Gets close to my first target (open position)',
};

const POSITION_CONDITIONS: PriceAlertCondition[] = ['near_stop', 'near_tp1'];

/** One line saying what an alert watches for, e.g. "price at or above 150.00". */
export function describeAlert(alert: Pick<PriceAlert, 'condition' | 'threshold' | 'unit'>): string {
  const unit = alert.unit === 'atr' ? ' ATR' : '%';
  switch (alert.condition) {
    case 'price_above':
      return `price at or above ${alert.threshold.toFixed(2)}`;
    case 'price_below':
      return `price at or below ${alert.threshold.toFixed(2)}`;
    case 'day_move_pct':
      return `moves ${alert.threshold}% or more in a day`;
    case 'near_stop':
      return `within ${alert.threshold}${unit} of my stop`;
    case 'near_tp1':
      return `within ${alert.threshold}${unit} of my first target`;
  }
}

function AlertForm({ symbol, onDone }: { symbol: string; onDone?: () => void }) {
  const create = useCreatePriceAlert();
  const [condition, setCondition] = useState<PriceAlertCondition>('price_above');
  const [threshold, setThreshold] = useState('');
  const [unit, setUnit] = useState<PriceAlertUnit>('pct');
  const [repeat, setRepeat] = useState(false);
  const [note, setNote] = useState('');

  const value = Number(threshold);
  const valid = threshold.trim() !== '' && Number.isFinite(value) && value > 0;
  const needsUnit = POSITION_CONDITIONS.includes(condition);

  function submit() {
    create.mutate(
      {
        symbol,
        condition,
        threshold: value,
        unit: needsUnit ? unit : null,
        repeat,
        note: note.trim() || null,
      },
      {
        onSuccess: () => {
          setThreshold('');
          setNote('');
          onDone?.();
        },
      },
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="alert-form">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <label>Alert me when {symbol}</label>
          <select value={condition} onChange={(e) => setCondition(e.target.value as PriceAlertCondition)} aria-label="Alert condition">
            {Object.entries(CONDITION_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>{needsUnit ? 'Within' : condition === 'day_move_pct' ? 'Percent' : 'Price'}</label>
          <input
            type="number"
            min={0}
            step="any"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            style={{ width: 110 }}
            aria-label="Alert threshold"
          />
        </div>
        {needsUnit && (
          <div>
            <label>Measured in</label>
            <select value={unit} onChange={(e) => setUnit(e.target.value as PriceAlertUnit)} aria-label="Alert unit">
              <option value="pct">% of the price</option>
              <option value="atr">ATRs (average daily range)</option>
            </select>
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          type="text"
          value={note}
          maxLength={200}
          placeholder="Note (optional)"
          onChange={(e) => setNote(e.target.value)}
          style={{ flex: '1 1 200px' }}
          aria-label="Alert note"
        />
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', margin: 0 }}>
          <input type="checkbox" checked={repeat} onChange={(e) => setRepeat(e.target.checked)} />
          Repeat (at most every 4 hours)
        </label>
        <button type="button" className="btn btn-secondary" onClick={submit} disabled={!valid || create.isPending}>
          {create.isPending ? 'Saving…' : 'Set alert'}
        </button>
      </div>
      {create.error && <ErrorBanner message={create.error.message} />}
      <div className="text-muted" style={{ fontSize: 12 }}>
        Alerts only send a Telegram message and record the event. They never trade or change a position.
      </div>
    </div>
  );
}

function statusBadge(alert: PriceAlert) {
  const cls = alert.status === 'active' ? 'badge badge-green' : alert.status === 'triggered' ? 'badge badge-amber' : 'badge badge-neutral';
  return <span className={cls}>{alert.status}</span>;
}

function AlertRows({ alerts, showSymbol }: { alerts: PriceAlert[]; showSymbol: boolean }) {
  const remove = useDeletePriceAlert();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {alerts.map((alert) => (
        <div
          key={alert.id}
          style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between' }}
          data-testid="alert-row"
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {showSymbol && <TickerLink symbol={alert.symbol} />}
            <span>{describeAlert(alert)}</span>
            {alert.repeat && <span className="badge badge-neutral">repeats</span>}
            {statusBadge(alert)}
            {alert.last_triggered_at && (
              <span className="text-muted" style={{ fontSize: 12 }}>
                fired {formatRelativeTime(alert.last_triggered_at)}
                {alert.last_value != null ? ` (${alert.last_value.toFixed(2)})` : ''}
              </span>
            )}
            {alert.note && (
              <span className="text-muted" style={{ fontSize: 12 }}>
                {alert.note}
              </span>
            )}
          </div>
          <button type="button" className="btn btn-secondary" style={{ padding: '2px 10px', fontSize: 12 }} disabled={remove.isPending} onClick={() => remove.mutate(alert.id)}>
            {alert.status === 'active' ? 'Cancel' : 'Remove'}
          </button>
        </div>
      ))}
      {remove.error && <ErrorBanner message={remove.error.message} />}
    </div>
  );
}

/** "Alert me" in the Analysis page header: opens a small form for the symbol on screen and lists its alerts. */
export function AlertMeButton({ symbol }: { symbol: string }) {
  const [open, setOpen] = useState(false);
  const { data } = usePriceAlerts();
  const mine = (data?.alerts ?? []).filter((a) => a.symbol === symbol && a.status === 'active');
  return (
    <>
      <button type="button" className="btn btn-secondary" onClick={() => setOpen((v) => !v)} aria-expanded={open} data-testid="alert-me">
        Alert me{mine.length > 0 ? ` (${mine.length})` : ''}
      </button>
      {open && (
        <div className="card" style={{ flexBasis: '100%', display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="alert-me-panel">
          <AlertForm symbol={symbol} />
          {mine.length > 0 && <AlertRows alerts={mine} showSymbol={false} />}
        </div>
      )}
    </>
  );
}

/** Every alert you set, with cancel/remove. Creating one starts on the Analysis page ("Alert me"). */
export function PriceAlertsCard() {
  const { data, isLoading, error } = usePriceAlerts();
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="price-alerts-card">
      <h3>Price alerts</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        Alerts you set from the Analysis page (&quot;Alert me&quot;). A check runs every five minutes while the market is open
        (crypto: always) and sends a Telegram message when a condition is met. Alerts only notify; they never trade or
        change a position.
        {data ? ` ${data.active_count} of ${data.max_active} active alerts used.` : ''}
      </div>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      {data && data.alerts.length === 0 && <EmptyState>No alerts yet.</EmptyState>}
      {data && data.alerts.length > 0 && <AlertRows alerts={data.alerts} showSymbol />}
    </div>
  );
}

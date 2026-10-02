import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCreateSleeve, useSleeves } from '../api/hooks';
import type { ApiError } from '../api/client';
import { ErrorBanner, formatMoney, formatNumber, formatPct, formatR } from './common';

/** The selected sleeve lives in the URL (?sleeve=key) so a link or a reload keeps it. Core is the default. */
export function useSelectedSleeve(): [string, (key: string) => void] {
  const [params, setParams] = useSearchParams();
  const selected = params.get('sleeve') || 'core';
  const select = (key: string) => {
    const next = new URLSearchParams(params);
    if (key === 'core') next.delete('sleeve');
    else next.set('sleeve', key);
    setParams(next, { replace: true });
  };
  return [selected, select];
}

export function SleeveChip({ color, name }: { color: string | null | undefined; name: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span aria-hidden style={{ width: 10, height: 10, borderRadius: 5, background: color ?? 'var(--text-muted, #888)' }} />
      {name}
    </span>
  );
}

/** Tabs for choosing which paper account (sleeve) a page shows, plus a "New sleeve" dialog. */
export function SleeveSwitcher({ selected, onSelect }: { selected: string; onSelect: (key: string) => void }) {
  const { data: sleeves } = useSleeves();
  const [creating, setCreating] = useState(false);
  if (!sleeves) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }} role="tablist" aria-label="Sleeve">
        {sleeves.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={s.key === selected}
            className={s.key === selected ? 'btn btn-primary' : 'btn btn-secondary'}
            onClick={() => onSelect(s.key)}
            title={`${s.style}${s.enabled ? '' : ' (disabled: opens no new trades)'}`}
          >
            <SleeveChip color={s.color} name={s.name} />
            {!s.enabled && <span className="text-muted"> · off</span>}
          </button>
        ))}
        <button type="button" className="btn btn-secondary" onClick={() => setCreating((v) => !v)}>
          {creating ? 'Cancel' : '+ New sleeve'}
        </button>
      </div>
      {creating && <CreateSleeveForm onDone={(key) => { setCreating(false); if (key) onSelect(key); }} />}
    </div>
  );
}

function CreateSleeveForm({ onDone }: { onDone: (createdKey?: string) => void }) {
  const [name, setName] = useState('');
  const [style, setStyle] = useState('');
  const [cash, setCash] = useState('100000');
  const { mutate, isPending, error } = useCreateSleeve();
  const amount = Number(cash);
  const valid = name.trim() !== '' && style.trim() !== '' && amount > 0;
  return (
    <form
      className="card"
      style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        mutate({ name: name.trim(), style: style.trim(), starting_cash: amount }, { onSuccess: (s) => onDone(s.key) });
      }}
    >
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="e.g. Momentum" />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Style
        <input value={style} onChange={(e) => setStyle(e.target.value)} maxLength={40} placeholder="e.g. momentum" />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Starting cash
        <input type="number" min={1} value={cash} onChange={(e) => setCash(e.target.value)} />
      </label>
      <button type="submit" className="btn btn-primary" disabled={!valid || isPending}>
        {isPending ? 'Creating…' : 'Create sleeve'}
      </button>
      {error && <ErrorBanner message={(error as ApiError).message} />}
    </form>
  );
}

/** Every sleeve side by side, from each sleeve's own closed trades and cash. */
export function SleevesOverview({ selected, onSelect }: { selected: string; onSelect: (key: string) => void }) {
  const { data: sleeves } = useSleeves();
  if (!sleeves || sleeves.length < 2) return null;
  return (
    <div className="card">
      <h3 style={{ fontSize: 15, marginBottom: 4 }}>Sleeves</h3>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
        Each sleeve is its own paper account with its own cash, positions and curve. Two sleeves may hold the same symbol at
        the same time; position and sector caps apply to each sleeve separately.
      </div>
      <table>
        <thead>
          <tr>
            <th>Sleeve</th>
            <th>Value</th>
            <th>Return</th>
            <th>Open</th>
            <th>Win rate</th>
            <th>Avg R</th>
            <th>Trades</th>
          </tr>
        </thead>
        <tbody>
          {sleeves.map((s) => (
            <tr key={s.key} style={{ cursor: 'pointer', fontWeight: s.key === selected ? 700 : undefined }} onClick={() => onSelect(s.key)}>
              <td><SleeveChip color={s.color} name={s.name} /></td>
              <td className="tabular-nums">{formatMoney(s.stats.portfolio_value)}</td>
              <td className={`tabular-nums ${s.stats.total_return > 0 ? 'text-green' : s.stats.total_return < 0 ? 'text-red' : ''}`}>{formatPct(s.stats.total_return)}</td>
              <td className="tabular-nums">{s.stats.active_positions}</td>
              <td className="tabular-nums">{s.stats.total_trades ? `${formatNumber(s.stats.win_rate, 0)}%` : '—'}</td>
              <td className="tabular-nums">{formatR(s.stats.avg_rr)}</td>
              <td className="tabular-nums">{s.stats.total_trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

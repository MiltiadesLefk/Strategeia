// Idea from OpenTerminal's ScreenerWidget (MIT, ErTasselli/OpenTerminal): a screen of
// rules over a list of stocks. No code was copied. The rows come from this app's own
// cached data (not TradingView's scanner), and a run says how many symbols it read.
import { useState } from 'react';
import { useDeleteSavedScreen, useRunScreen, useSaveScreen, useSavedScreens, useScreenerFields } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { ScreenerField, ScreenerFilter, ScreenerRunResponse, ScreenerSpec, ScreenerValue } from '../api/types';
import { TickerLink } from '../components/TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatNumber } from '../components/common';
import { toCsv } from '../lib/csv';

const OP_LABEL: Record<string, string> = { gt: '>', gte: '>=', lt: '<', lte: '<=', between: 'between', eq: 'equals', neq: 'not equal', contains: 'contains' };
const DEFAULT_SPEC: ScreenerSpec = { filters: [], sort_field: 'scanner_score', sort_dir: 'desc', limit: 50, columns: [] };

function formatValue(field: ScreenerField | undefined, v: ScreenerValue | undefined): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (!field) return String(v);
  if (field.unit === '$') return formatMoney(v, { compact: Math.abs(v) >= 1e7 });
  if (field.unit === '%') return `${formatNumber(v, 1)}%`;
  if (field.unit === 'x') return `${formatNumber(v, 2)}x`;
  return formatNumber(v, 2);
}

function FilterRow({
  filter,
  fields,
  onChange,
  onRemove,
}: {
  filter: ScreenerFilter;
  fields: ScreenerField[];
  onChange: (f: ScreenerFilter) => void;
  onRemove: () => void;
}) {
  const spec = fields.find((f) => f.name === filter.field);
  const isNumber = spec?.kind === 'number';
  const toValue = (raw: string) => (isNumber ? (raw === '' ? null : Number(raw)) : raw);
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }} data-screener-filter>
      <select
        aria-label="Field"
        value={filter.field}
        style={{ width: 'auto' }}
        onChange={(e) => {
          const next = fields.find((f) => f.name === e.target.value);
          onChange({ field: e.target.value, op: next?.operators[0] ?? 'eq', value: null, value2: null });
        }}
      >
        {fields.map((f) => (
          <option key={f.name} value={f.name}>
            {f.label}
          </option>
        ))}
      </select>
      <select aria-label="Operator" value={filter.op} style={{ width: 'auto' }} onChange={(e) => onChange({ ...filter, op: e.target.value })}>
        {(spec?.operators ?? []).map((o) => (
          <option key={o} value={o}>
            {OP_LABEL[o] ?? o}
          </option>
        ))}
      </select>
      <input
        aria-label="Value"
        type={isNumber ? 'number' : 'text'}
        step="any"
        style={{ width: 110 }}
        value={filter.value ?? ''}
        onChange={(e) => onChange({ ...filter, value: toValue(e.target.value) })}
      />
      {filter.op === 'between' && (
        <>
          <span className="text-muted">and</span>
          <input
            aria-label="Upper value"
            type="number"
            step="any"
            style={{ width: 110 }}
            value={filter.value2 ?? ''}
            onChange={(e) => onChange({ ...filter, value2: e.target.value === '' ? null : Number(e.target.value) })}
          />
        </>
      )}
      {spec?.unit && spec.unit !== 'pts' && (
        <span className="text-muted" style={{ fontSize: 12 }}>
          {spec.unit}
        </span>
      )}
      {spec?.costly && (
        <span className="badge badge-amber" title="Needs an extra fetch for every symbol read">
          slower
        </span>
      )}
      <button type="button" className="btn btn-secondary" style={{ padding: '2px 8px' }} onClick={onRemove} aria-label="Remove rule">
        Remove
      </button>
    </div>
  );
}

function ResultTable({ result, fields }: { result: ScreenerRunResponse; fields: ScreenerField[] }) {
  const byName = new Map(fields.map((f) => [f.name, f]));
  const cols = result.columns.map((c) => byName.get(c)).filter((f): f is ScreenerField => !!f);

  function exportCsv() {
    const csv = toCsv(
      ['Symbol', 'Name', ...cols.map((c) => c.label)],
      result.rows.map((r) => [r.symbol, r.name, ...cols.map((c) => r.values[c.name] ?? null)]),
    );
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'screener.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="card" data-screener-results>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div className="text-muted" style={{ fontSize: 13 }}>
          {result.scanned} of {result.universe_size} symbols scanned; {result.matched} matched, {result.rows.length} shown.
          {result.skipped_missing_data > 0 && ` ${result.skipped_missing_data} left out because a rule's value was missing for them.`}
          {result.missing.length > 0 && ` No price data for ${result.missing.length}: ${result.missing.slice(0, 8).join(', ')}${result.missing.length > 8 ? '…' : ''}.`}
        </div>
        <button type="button" className="btn btn-secondary" onClick={exportCsv} disabled={result.rows.length === 0}>
          Export CSV
        </button>
      </div>
      {result.rows.length === 0 ? (
        <EmptyState>No symbol in the scanned set matches every rule.</EmptyState>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                {cols.map((c) => (
                  <th key={c.name} style={{ textAlign: c.kind === 'number' ? 'right' : 'left' }}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((r) => (
                <tr key={r.symbol}>
                  <td>
                    <TickerLink symbol={r.symbol} />
                  </td>
                  <td className="text-muted">{r.name}</td>
                  {cols.map((c) => (
                    <td key={c.name} className="tabular-nums" style={{ textAlign: c.kind === 'number' ? 'right' : 'left' }}>
                      {formatValue(c, r.values[c.name])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ScreenerPage() {
  const fieldsQuery = useScreenerFields();
  const saved = useSavedScreens();
  const run = useRunScreen();
  const save = useSaveScreen();
  const del = useDeleteSavedScreen();
  const [spec, setSpec] = useState<ScreenerSpec>(DEFAULT_SPEC);
  const [scanCap, setScanCap] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  if (fieldsQuery.isLoading) return <LoadingSpinner label="Loading screener…" />;
  if (fieldsQuery.error || !fieldsQuery.data) {
    return <ErrorBanner message={(fieldsQuery.error as ApiError | null)?.message ?? 'The screener could not load.'} onRetry={() => fieldsQuery.refetch()} />;
  }
  const meta = fieldsQuery.data;
  const fields = meta.fields;
  const cap = scanCap ?? meta.default_scan_cap;
  const patch = (p: Partial<ScreenerSpec>) => setSpec((s) => ({ ...s, ...p }));

  function addFilter() {
    const f = fields[0];
    patch({ filters: [...spec.filters, { field: f.name, op: f.operators[0], value: null, value2: null }] });
  }

  function execute() {
    setMessage(null);
    run.mutate({ ...spec, scan_cap: cap });
  }

  function saveCurrent() {
    setMessage(null);
    save.mutate(
      { ...spec, name: name.trim() },
      {
        onSuccess: () => {
          setName('');
          setMessage('Saved.');
        },
        onError: (e) => setMessage((e as ApiError).message),
      },
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ margin: 0 }}>Screener</h1>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Your own rules over this app's symbol list, using daily bars and company data it already caches. Read-only: nothing here places a trade.
        </div>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {saved.data && saved.data.length > 0 && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }} data-screener-saved>
            <span className="text-muted" style={{ fontSize: 13 }}>
              Saved screens
            </span>
            {saved.data.map((s) => (
              <span key={s.id} style={{ display: 'inline-flex', gap: 4 }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setSpec({ filters: s.filters, sort_field: s.sort_field, sort_dir: s.sort_dir, limit: s.limit, columns: s.columns })}
                >
                  {s.name}
                </button>
                <button type="button" className="btn btn-secondary" aria-label={`Delete ${s.name}`} onClick={() => del.mutate(s.id)}>
                  x
                </button>
              </span>
            ))}
          </div>
        )}

        {spec.filters.length === 0 && (
          <div className="text-muted" style={{ fontSize: 13 }}>
            No rules yet: every symbol read is shown, sorted as chosen.
          </div>
        )}
        {spec.filters.map((f, i) => (
          <FilterRow
            key={i}
            filter={f}
            fields={fields}
            onChange={(next) => patch({ filters: spec.filters.map((x, j) => (j === i ? next : x)) })}
            onRemove={() => patch({ filters: spec.filters.filter((_, j) => j !== i) })}
          />
        ))}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button type="button" className="btn btn-secondary" onClick={addFilter} disabled={spec.filters.length >= meta.max_filters}>
            Add rule
          </button>
          <label className="text-muted" style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
            Sort by
            <select value={spec.sort_field ?? ''} style={{ width: 'auto' }} onChange={(e) => patch({ sort_field: e.target.value || null })}>
              <option value="">(none)</option>
              {fields.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.label}
                </option>
              ))}
            </select>
            <select aria-label="Sort direction" value={spec.sort_dir} style={{ width: 'auto' }} onChange={(e) => patch({ sort_dir: e.target.value as 'asc' | 'desc' })}>
              <option value="desc">high to low</option>
              <option value="asc">low to high</option>
            </select>
          </label>
          <label className="text-muted" style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
            Show
            <input
              type="number"
              min={1}
              max={meta.max_symbols}
              style={{ width: 80 }}
              value={spec.limit}
              onChange={(e) => patch({ limit: Math.max(1, Math.min(meta.max_symbols, Number(e.target.value) || 1)) })}
            />
            rows
          </label>
          <label className="text-muted" style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }} title="Only this many symbols are read, in list order, to keep a run quick">
            Read first
            <input
              type="number"
              min={1}
              max={meta.max_symbols}
              style={{ width: 80 }}
              value={cap}
              onChange={(e) => setScanCap(Math.max(1, Math.min(meta.max_symbols, Number(e.target.value) || 1)))}
            />
            symbols
          </label>
          <button type="button" className="btn" onClick={execute} disabled={run.isPending}>
            {run.isPending ? 'Running…' : 'Run screen'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input aria-label="Screen name" placeholder="Name this screen" maxLength={60} style={{ width: 200 }} value={name} onChange={(e) => setName(e.target.value)} />
          <button type="button" className="btn btn-secondary" onClick={saveCurrent} disabled={!name.trim() || save.isPending}>
            Save screen
          </button>
          {message && (
            <span className="text-muted" style={{ fontSize: 13 }}>
              {message}
            </span>
          )}
        </div>
      </div>

      {run.isPending && <LoadingSpinner label="Reading symbols…" />}
      {run.error && <ErrorBanner message={(run.error as ApiError).message} />}
      {run.data && <ResultTable result={run.data} fields={fields} />}
    </div>
  );
}

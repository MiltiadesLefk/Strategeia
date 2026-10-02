// Valuation tab: a simple discounted-earnings projection with editable
// assumptions, and a peer-multiple table. Rule-computed and informational only;
// it never changes a trade plan. The optional paragraph is labelled AI or rules.
import { useState } from 'react';
import { useValuation } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { ValuationInputs, ValuationResponse } from '../../api/types';
import { StatCard } from '../StatCard';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatNumber } from '../common';

const num = (s: string): number | undefined => {
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : undefined;
};
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`);

interface Form {
  growth: string;
  margin: string;
  discount: string;
  terminal: string;
  years: string;
}
const DEFAULT_FORM: Form = { growth: '', margin: '', discount: '10', terminal: '2.5', years: '5' };

function toInputs(f: Form, explain: boolean): ValuationInputs {
  return {
    growth: num(f.growth),
    margin: num(f.margin),
    discount: num(f.discount) ?? 10,
    terminal: num(f.terminal) ?? 2.5,
    years: Math.min(10, Math.max(1, Math.round(num(f.years) ?? 5))),
    explain,
  };
}

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="text-muted" style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 4 }}>
      {label}
      <input type="number" step="0.1" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} style={{ width: 110 }} />
    </label>
  );
}

function DcfCard({ data }: { data: ValuationResponse }) {
  const dcf = data.dcf;
  if (!dcf) return null;
  const a = dcf.assumptions;
  const terminals = [...new Set(dcf.sensitivity.map((c) => c.terminal_growth_pct))];
  const discounts = [...new Set(dcf.sensitivity.map((c) => c.discount_rate_pct))];
  return (
    <div className="card" data-valuation-part="dcf">
      <h3 style={{ marginBottom: 12 }}>Discounted earnings estimate</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 12 }}>
        <StatCard label="Estimated value per share" value={dcf.value_per_share === null ? '—' : formatMoney(dcf.value_per_share)} note={dcf.value_per_share === null ? 'needs market cap and price' : undefined} />
        <StatCard label="Price" value={formatMoney(data.price)} />
        <StatCard label="Difference to price" value={pct(dcf.upside_pct)} />
        <StatCard label="Share from terminal value" value={`${dcf.terminal_share_pct.toFixed(0)}%`} note="the further-out part of the estimate" />
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Year</th>
              <th style={{ textAlign: 'right' }}>Revenue</th>
              <th style={{ textAlign: 'right' }}>Net income</th>
              <th style={{ textAlign: 'right' }}>Present value</th>
            </tr>
          </thead>
          <tbody>
            {dcf.projection.map((y) => (
              <tr key={y.year}>
                <td>{y.year}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatMoney(y.revenue, { compact: true })}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatMoney(y.earnings, { compact: true })}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatMoney(y.present_value, { compact: true })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-muted" style={{ fontSize: 12, marginTop: 8 }}>
        Growth {a.growth_pct.toFixed(1)}% ({a.growth_source}); net margin {a.net_margin_pct.toFixed(1)}% ({a.margin_source}). Terminal value {formatMoney(dcf.terminal_value, { compact: true })}, equity value {formatMoney(dcf.equity_value, { compact: true })}.
      </div>
      {dcf.value_per_share !== null && (
        <div style={{ marginTop: 12 }}>
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 4 }}>
            Value per share at other discount rates (rows) and terminal growth rates (columns)
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th />
                {terminals.map((t) => (
                  <th key={t} style={{ textAlign: 'right' }}>{t.toFixed(1)}%</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {discounts.map((d) => (
                <tr key={d}>
                  <td>{d.toFixed(1)}%</td>
                  {dcf.sensitivity
                    .filter((c) => c.discount_rate_pct === d)
                    .map((c) => (
                      <td key={c.terminal_growth_pct} className="tabular-nums" style={{ textAlign: 'right' }}>
                        {c.value_per_share === null ? '—' : formatMoney(c.value_per_share)}
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

function CompsCard({ data }: { data: ValuationResponse }) {
  const comps = data.comps;
  if (!comps) return null;
  return (
    <div className="card" data-valuation-part="comps">
      <h3 style={{ marginBottom: 12 }}>Peer multiples{comps.sector ? ` (${comps.sector})` : ''}</h3>
      {comps.reason ? (
        <EmptyState>{comps.reason}</EmptyState>
      ) : (
        <>
          <div style={{ overflowX: 'auto', marginBottom: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Multiple</th>
                  <th style={{ textAlign: 'right' }}>This company</th>
                  <th style={{ textAlign: 'right' }}>Peer median</th>
                  <th style={{ textAlign: 'right' }}>Range</th>
                  <th style={{ textAlign: 'right' }}>Peers (n)</th>
                  <th style={{ textAlign: 'right' }}>Implied price</th>
                </tr>
              </thead>
              <tbody>
                {comps.multiples.map((m) => (
                  <tr key={m.name}>
                    <td>{m.name}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatNumber(m.subject, 1)}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatNumber(m.median, 1)}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{m.low === null || m.high === null ? '—' : `${m.low.toFixed(1)} to ${m.high.toFixed(1)}`}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{m.count}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{m.implied_price === null ? '—' : formatMoney(m.implied_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Peer</th>
                  <th style={{ textAlign: 'right' }}>Market cap</th>
                  <th style={{ textAlign: 'right' }}>P/E</th>
                  <th style={{ textAlign: 'right' }}>P/S</th>
                </tr>
              </thead>
              <tbody>
                {comps.peers.map((p) => (
                  <tr key={p.symbol}>
                    <td>
                      {p.symbol} <span className="text-muted">{p.name}</span>
                    </td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatMoney(p.market_cap, { compact: true })}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatNumber(p.pe_ratio, 1)}</td>
                    <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatNumber(p.price_to_sales, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export function ValuationPanel({ symbol }: { symbol: string }) {
  const [form, setForm] = useState<Form>(DEFAULT_FORM);
  const [applied, setApplied] = useState<ValuationInputs>(toInputs(DEFAULT_FORM, false));
  const { data, isLoading, error, refetch, isFetching } = useValuation(symbol, applied);
  const set = (k: keyof Form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  if (isLoading) return <LoadingSpinner label="Loading valuation…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />;
  if (!data) return null;
  if (!data.available) {
    return (
      <div className="card" data-valuation-state="none">
        <h3 style={{ marginBottom: 12 }}>Valuation</h3>
        <EmptyState>{data.reason ?? 'No valuation is available for this symbol.'}</EmptyState>
      </div>
    );
  }
  const a = data.dcf?.assumptions;
  return (
    <>
      <div className="card" data-valuation-state="ready">
        <h3 style={{ marginBottom: 8 }}>Assumptions</h3>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <Field label="Revenue growth %/yr" value={form.growth} onChange={set('growth')} placeholder={a?.growth_default_pct != null ? a.growth_default_pct.toFixed(1) : 'enter'} />
          <Field label="Net margin %" value={form.margin} onChange={set('margin')} placeholder={a?.margin_default_pct != null ? a.margin_default_pct.toFixed(1) : 'enter'} />
          <Field label="Discount rate %" value={form.discount} onChange={set('discount')} />
          <Field label="Terminal growth %" value={form.terminal} onChange={set('terminal')} />
          <Field label="Years" value={form.years} onChange={set('years')} />
          <button className="btn btn-primary" disabled={isFetching} onClick={() => setApplied(toInputs(form, applied.explain))}>
            Recalculate
          </button>
          <button
            className="btn"
            onClick={() => {
              setForm(DEFAULT_FORM);
              setApplied(toInputs(DEFAULT_FORM, applied.explain));
            }}
          >
            Reset
          </button>
          <button className="btn" disabled={isFetching} onClick={() => setApplied({ ...applied, explain: true })}>
            Explain in words
          </button>
        </div>
        <div className="text-muted" style={{ fontSize: 12, marginTop: 8 }}>Blank growth and margin use the reported figures shown greyed in the boxes.</div>
        {data.summary && (
          <p style={{ marginTop: 12 }}>
            {data.summary}{' '}
            <span className="text-muted" style={{ fontSize: 12 }}>({data.summary_source === 'ai' ? 'AI paragraph' : 'rule-based text'})</span>
          </p>
        )}
      </div>
      <DcfCard data={data} />
      <CompsCard data={data} />
      <div className="text-muted" style={{ fontSize: 11, display: 'flex', flexDirection: 'column', gap: 2 }}>
        {data.notes.map((n) => (
          <span key={n}>{n}</span>
        ))}
      </div>
    </>
  );
}

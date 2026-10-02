// Idea from OpenTerminal's OptionsWidget (MIT, ErTasselli/OpenTerminal): an options
// table around the money. No code was copied. The numbers come from our own data
// providers (not TradingView), the summary maths is computed server-side from the
// chain's real fields, and there are no Greeks because the free source has none.
import { useState } from 'react';
import { useOptionsChain } from '../../api/hooks';
import type { OptionLeg, OptionsChainResponse, OptionStrikeRow } from '../../api/types';
import { DataFreshness } from '../DataFreshness';
import { StatCard } from '../StatCard';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatNumber } from '../common';
import type { ApiError } from '../../api/client';

const fmtInt = (v: number | null | undefined) => (v === null || v === undefined ? '—' : Math.round(v).toLocaleString('en-US'));
const fmtPrice = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toFixed(2));
const fmtIv = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`);

const ITM_TINT = 'rgba(245, 158, 11, 0.08)';
const CALL_BAR = 'rgba(16, 185, 129, 0.28)';
const PUT_BAR = 'rgba(239, 68, 68, 0.28)';

/** A cell whose background is a bar as wide as `value` is against the largest value on screen. */
function BarCell({ value, max, colour, itm, align }: { value: number | null; max: number; colour: string; itm: boolean; align: 'left' | 'right' }) {
  const width = value !== null && max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const direction = align === 'right' ? 'to left' : 'to right';
  return (
    <td
      className="tabular-nums"
      style={{
        textAlign: 'right',
        backgroundColor: itm ? ITM_TINT : undefined,
        backgroundImage: width > 0 ? `linear-gradient(${direction}, ${colour} ${width}%, transparent ${width}%)` : undefined,
      }}
    >
      {fmtInt(value)}
    </td>
  );
}

function PlainCell({ children, itm }: { children: string; itm: boolean }) {
  return (
    <td className="tabular-nums" style={{ textAlign: 'right', backgroundColor: itm ? ITM_TINT : undefined }}>
      {children}
    </td>
  );
}

function CallCells({ leg, maxVol, maxOi }: { leg: OptionLeg | null; maxVol: number; maxOi: number }) {
  const itm = leg?.in_the_money === true;
  return (
    <>
      <PlainCell itm={itm}>{fmtIv(leg?.implied_volatility)}</PlainCell>
      <PlainCell itm={itm}>{fmtPrice(leg?.last_price)}</PlainCell>
      <PlainCell itm={itm}>{fmtPrice(leg?.bid)}</PlainCell>
      <PlainCell itm={itm}>{fmtPrice(leg?.ask)}</PlainCell>
      <BarCell value={leg?.volume ?? null} max={maxVol} colour={CALL_BAR} itm={itm} align="right" />
      <BarCell value={leg?.open_interest ?? null} max={maxOi} colour={CALL_BAR} itm={itm} align="right" />
    </>
  );
}

function PutCells({ leg, maxVol, maxOi }: { leg: OptionLeg | null; maxVol: number; maxOi: number }) {
  const itm = leg?.in_the_money === true;
  return (
    <>
      <BarCell value={leg?.open_interest ?? null} max={maxOi} colour={PUT_BAR} itm={itm} align="left" />
      <BarCell value={leg?.volume ?? null} max={maxVol} colour={PUT_BAR} itm={itm} align="left" />
      <PlainCell itm={itm}>{fmtPrice(leg?.bid)}</PlainCell>
      <PlainCell itm={itm}>{fmtPrice(leg?.ask)}</PlainCell>
      <PlainCell itm={itm}>{fmtPrice(leg?.last_price)}</PlainCell>
      <PlainCell itm={itm}>{fmtIv(leg?.implied_volatility)}</PlainCell>
    </>
  );
}

function ChainTable({ rows }: { rows: OptionStrikeRow[] }) {
  const all = rows.flatMap((r) => [r.call, r.put]);
  const maxVol = Math.max(0, ...all.map((l) => l?.volume ?? 0));
  const maxOi = Math.max(0, ...all.map((l) => l?.open_interest ?? 0));
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ minWidth: 860 }} data-options-table>
        <thead>
          <tr>
            <th colSpan={6} style={{ textAlign: 'center', color: 'var(--green)' }}>
              Calls
            </th>
            <th />
            <th colSpan={6} style={{ textAlign: 'center', color: 'var(--red)' }}>
              Puts
            </th>
          </tr>
          <tr>
            {['IV', 'Last', 'Bid', 'Ask', 'Volume', 'Open int.'].map((h) => (
              <th key={`c${h}`} style={{ textAlign: 'right' }}>
                {h}
              </th>
            ))}
            <th style={{ textAlign: 'center' }}>Strike</th>
            {['Open int.', 'Volume', 'Bid', 'Ask', 'Last', 'IV'].map((h) => (
              <th key={`p${h}`} style={{ textAlign: 'right' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.strike} data-atm={row.is_atm ? 'true' : undefined} style={row.is_atm ? { outline: '1px solid var(--amber)', outlineOffset: -1 } : undefined}>
              <CallCells leg={row.call} maxVol={maxVol} maxOi={maxOi} />
              <td className="tabular-nums" style={{ textAlign: 'center', fontWeight: 700, background: 'var(--card-alt)' }}>
                {formatNumber(row.strike, row.strike % 1 === 0 ? 0 : 2)}
              </td>
              <PutCells leg={row.put} maxVol={maxVol} maxOi={maxOi} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SummaryCards({ view }: { view: OptionsChainResponse }) {
  const s = view.summary;
  if (!s) return null;
  const ratio = (v: number | null) => (v === null ? '—' : v.toFixed(2));
  return (
    <div className="grid stat-grid">
      <StatCard label="Put/call volume" value={ratio(s.put_call_volume_ratio)} note={`${fmtInt(s.put_volume)} puts / ${fmtInt(s.call_volume)} calls traded`} />
      <StatCard label="Put/call open interest" value={ratio(s.put_call_oi_ratio)} note={`${fmtInt(s.put_open_interest)} puts / ${fmtInt(s.call_open_interest)} calls open`} />
      <StatCard
        label="At-the-money IV"
        value={fmtIv(s.atm_implied_volatility)}
        note={s.atm_strike !== null ? `strike ${formatNumber(s.atm_strike, 2)}, mean of call and put` : 'needs a current price'}
      />
      <StatCard
        label="Expected move"
        value={s.expected_move_pct === null ? '—' : `±${s.expected_move_pct.toFixed(1)}%`}
        note={
          s.expected_move_pct === null
            ? 'needs IV and a future expiration'
            : `${formatMoney(s.expected_move_dollars)} by ${view.expiration} (${view.days_to_expiration} days). A size, not a direction.`
        }
      />
      <StatCard label="Max pain" value={s.max_pain_strike === null ? '—' : formatNumber(s.max_pain_strike, 2)} note="strike with the least total payout, from open interest" />
      <StatCard
        label="IV skew"
        value={s.iv_skew_points === null ? '—' : `${s.iv_skew_points >= 0 ? '+' : ''}${s.iv_skew_points.toFixed(1)} pts`}
        note={s.iv_skew_label ? `${s.iv_skew_label}: puts 3-10% below the price against calls 3-10% above` : 'needs quoted puts and calls on both sides'}
      />
      <StatCard label="Price" value={formatMoney(view.spot)} note={view.spot === null ? 'no current price available' : undefined} />
      <StatCard label="Strikes shown" value={`${view.strikes_shown} / ${view.strikes_total}`} note="summary figures use every strike" />
    </div>
  );
}

export function OptionsPanel({ symbol }: { symbol: string }) {
  const [chosen, setChosen] = useState<{ symbol: string; expiration: string } | null>(null);
  // A choice made for another symbol must not leak into this one.
  const expiration = chosen && chosen.symbol === symbol ? chosen.expiration : null;
  const { data, isLoading, error, refetch, dataUpdatedAt, isFetching } = useOptionsChain(symbol, expiration);

  if (isLoading) return <LoadingSpinner label="Loading options chain…" />;
  if (error) return <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />;
  if (!data) return null;
  if (!data.available) {
    return (
      <div className="card" data-options-state="none">
        <h3 style={{ marginBottom: 12 }}>Options</h3>
        <EmptyState>{data.reason ?? 'No options data is available for this symbol.'}</EmptyState>
      </div>
    );
  }

  return (
    <>
      <div className="card" data-options-state="chain">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
          <h3>Options chain</h3>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <DataFreshness updatedAt={dataUpdatedAt} isFetching={isFetching} />
            <label className="text-muted" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              Expiration
              <select value={data.expiration ?? ''} onChange={(e) => setChosen({ symbol, expiration: e.target.value })} style={{ width: 'auto' }}>
                {data.expirations.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        <SummaryCards view={data} />
      </div>
      <div className="card">
        <ChainTable rows={data.rows} />
        <div className="text-muted" style={{ fontSize: 11, marginTop: 10, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span>Shaded rows are in the money; the outlined row is the strike nearest the price. Bars compare cells within this table.</span>
          {data.notes.map((n) => (
            <span key={n}>{n}</span>
          ))}
          <span>Quotes are delayed and can be stale or blank outside market hours. Nothing here is a recommendation.</span>
        </div>
      </div>
    </>
  );
}

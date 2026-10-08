import { useState } from 'react';
import { SymbolFilterSelect } from '../SymbolFilterSelect';
import { useFundsOverview, useOwnershipFilings } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { OwnershipFiling } from '../../api/types';
import { StatCard } from '../StatCard';
import { TickerLink } from '../TickerLink';
import { ErrorBanner, LoadingSpinner, isSafeHttpUrl } from '../common';
import { RefreshFundsControls } from './FundsTab';

const DAY_OPTIONS = [30, 90, 180, 365];
const SCHEDULE_OPTIONS = [
  { value: 'all', label: '13D and 13G' },
  { value: '13D', label: '13D only (may seek influence)' },
  { value: '13G', label: '13G only (passive)' },
];

function OwnershipNotes() {
  return (
    <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)', fontSize: 13 }} data-testid="ownership-notes">
      <strong>Read these before using the numbers.</strong>
      <ul style={{ margin: '6px 0 0 18px', display: 'flex', flexDirection: 'column', gap: 3 }}>
        <li>
          Anyone who ends up holding <strong>more than 5%</strong> of a listed company&apos;s shares files a Schedule 13D (it
          may want to influence the company: an activist) or a Schedule 13G (a passive holder, such as an index manager).
        </li>
        <li>
          For <strong>large companies this is rare and mostly routine</strong>: the 13Gs on a mega-cap are nearly always index
          managers re-filing. An individual or activist crossing 5% of a company that size almost never happens.
        </li>
        <li>
          A filing says what the holder owns <strong>now</strong>, not whether it is buying or selling; an amendment can report
          either. Direction is not knowable from the filing alone.
        </li>
        <li>Only structured filings (2024 onward) are read. Older free-text filings are counted on refresh and skipped.</li>
        <li>A new 13D feeds a silent signal that adds no points to a trade plan.</li>
      </ul>
    </div>
  );
}

function FilingRow({ f }: { f: OwnershipFiling }) {
  const [open, setOpen] = useState(false);
  return (
    <tr>
      <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>
        {f.known_at.slice(0, 10)}
        <div className="text-muted" style={{ fontSize: 11 }}>
          {f.event_date ? `event ${f.event_date}` : ''}
        </div>
      </td>
      <td>
        {f.symbol ? <TickerLink symbol={f.symbol} /> : <span className="text-muted">no ticker</span>}
        <div className="text-muted" style={{ fontSize: 11 }}>
          {f.issuer_name ?? ''}
        </div>
      </td>
      <td>
        <div style={{ fontWeight: 600 }}>{f.filer_name ?? 'unknown'}</div>
        {f.person_count > 1 && (
          <div className="text-muted" style={{ fontSize: 11 }}>
            and {f.person_count - 1} other reporting {f.person_count === 2 ? 'person' : 'persons'}
          </div>
        )}
      </td>
      <td>
        <span className={`badge ${f.schedule === '13D' ? 'badge-amber' : 'badge-neutral'}`}>
          {f.schedule}
          {f.is_amendment ? '/A' : ''}
        </span>
      </td>
      <td className="tabular-nums" style={{ textAlign: 'right' }}>
        {f.percent === null ? '—' : `${f.percent}%`}
        <div className="text-muted" style={{ fontSize: 11 }}>
          {f.shares === null ? '' : `${f.shares.toLocaleString()} shares`}
        </div>
      </td>
      <td style={{ maxWidth: 320, fontSize: 12 }}>
        {f.purpose ? (
          <>
            <div style={open ? undefined : { maxHeight: 54, overflow: 'hidden' }}>{f.purpose}</div>
            <button type="button" className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', marginTop: 4 }} onClick={() => setOpen(!open)}>
              {open ? 'Less' : 'More'}
            </button>
          </>
        ) : (
          <span className="text-muted">{f.schedule === '13G' ? `passive filing${f.rule ? ` (${f.rule})` : ''}` : '—'}</span>
        )}
      </td>
      <td>
        {f.filing_url && isSafeHttpUrl(f.filing_url) ? (
          <a href={f.filing_url} target="_blank" rel="noopener noreferrer">
            Filing
          </a>
        ) : (
          <span className="text-muted">—</span>
        )}
      </td>
    </tr>
  );
}

export function ActivistsTab() {
  const overview = useFundsOverview();
  const [days, setDays] = useState(90);
  const [schedule, setSchedule] = useState('all');
  const [symbol, setSymbol] = useState('');
  const filings = useOwnershipFilings({ days, schedule, symbol });
  const d = filings.data;

  return (
    <>
      <OwnershipNotes />
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div>
            <label>Window</label>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {DAY_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  Last {n} days
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Schedule</label>
            <select value={schedule} onChange={(e) => setSchedule(e.target.value)}>
              {SCHEDULE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Symbol</label>
            <SymbolFilterSelect value={symbol} onChange={setSymbol} width={170} />
          </div>
        </div>
        <RefreshFundsControls cooldownSeconds={overview.data?.refresh_cooldown_seconds ?? 60} />
      </div>
      {filings.isLoading && <LoadingSpinner />}
      {filings.error && <ErrorBanner message={(filings.error as ApiError).message} onRetry={() => filings.refetch()} />}
      {d && !d.has_data && (
        <div className="card text-muted" style={{ fontSize: 14 }} data-testid="ownership-empty">
          No 13D/13G filings are stored yet. That is not the same as nobody crossing 5%: nothing has been loaded. Use the button
          to look up filings for your watchlist companies and for the funds you follow.
        </div>
      )}
      {d && d.has_data && (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="stat-grid">
            <StatCard label="Schedule 13D (may seek influence)" value={String(d.count_13d)} note={`last ${d.days} days`} />
            <StatCard label="Schedule 13G (passive)" value={String(d.count_13g)} note={`last ${d.days} days`} />
          </div>
          {d.filings.length === 0 ? (
            <div className="text-muted" style={{ fontSize: 13 }}>
              No stored filings match. Large companies rarely have any in a short window.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table>
                <thead>
                  <tr>
                    <th title="The day SEC accepted the filing: the first moment anyone could see it">Public</th>
                    <th>Company</th>
                    <th>Holder</th>
                    <th>Form</th>
                    <th style={{ textAlign: 'right' }}>Of the class</th>
                    <th>Purpose (13D)</th>
                    <th>Link</th>
                  </tr>
                </thead>
                <tbody>
                  {d.filings.map((f) => (
                    <FilingRow key={f.accession} f={f} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {d.total > d.shown && (
            <div className="text-muted" style={{ fontSize: 12 }}>
              Showing the newest {d.shown} of {d.total}.
            </div>
          )}
        </div>
      )}
    </>
  );
}

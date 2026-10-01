import { useMemo, useState } from 'react';
import { useCalendar } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { CalendarItem, CalendarPositionCatalysts, CalendarResponse, CalendarSourceStatus } from '../api/types';
import { TickerLink } from '../components/TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from '../components/common';

type View = 'week' | 'month';

const KIND_CHIP: Record<string, { label: string; className: string }> = {
  economic: { label: 'Economic', className: 'badge badge-amber' },
  macro: { label: 'Fed / CPI / jobs', className: 'badge badge-amber' },
  earnings: { label: 'Earnings', className: 'badge badge-green' },
};

const IMPACT_CLASS: Record<string, string> = {
  High: 'badge badge-red',
  Medium: 'badge badge-amber',
  Low: 'badge badge-neutral',
  Holiday: 'badge badge-neutral',
};

// Dates are handled as plain YYYY-MM-DD strings (the server's US Eastern days).
// Building them from a local noon Date keeps a browser timezone from shifting the day.
function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d, 12);
}

function isoDay(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function addDays(iso: string, days: number): string {
  const date = parseDay(iso);
  date.setDate(date.getDate() + days);
  return isoDay(date);
}

function rangeFor(view: View, anchor: string): { from: string; to: string; label: string } {
  const date = parseDay(anchor);
  if (view === 'week') {
    const monday = addDays(anchor, -((date.getDay() + 6) % 7));
    const sunday = addDays(monday, 6);
    const fmt = (iso: string) => parseDay(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return { from: monday, to: sunday, label: `${fmt(monday)} – ${fmt(sunday)}, ${parseDay(sunday).getFullYear()}` };
  }
  const first = new Date(date.getFullYear(), date.getMonth(), 1, 12);
  const last = new Date(date.getFullYear(), date.getMonth() + 1, 0, 12);
  return {
    from: isoDay(first),
    to: isoDay(last),
    label: first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }),
  };
}

function shiftAnchor(view: View, anchor: string, direction: 1 | -1): string {
  if (view === 'week') return addDays(anchor, 7 * direction);
  const date = parseDay(anchor);
  return isoDay(new Date(date.getFullYear(), date.getMonth() + direction, 1, 12));
}

function countdown(days: number): string {
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  if (days === -1) return 'yesterday';
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

function dayHeading(iso: string): string {
  return parseDay(iso).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      className={active ? 'btn' : 'btn btn-secondary'}
      aria-pressed={active}
      onClick={onClick}
      style={{ padding: '6px 12px', fontSize: 13 }}
    >
      {children}
    </button>
  );
}

function SourceNotes({ sources }: { sources: CalendarSourceStatus[] }) {
  const problems = sources.filter((s) => s.status !== 'ok');
  if (problems.length === 0) return null;
  return (
    <div className="card" style={{ borderColor: 'var(--amber, var(--border))' }}>
      {problems.map((s) => (
        <div key={s.key} style={{ fontSize: 13, marginBottom: 4 }}>
          <strong>{s.label}</strong> ({s.status}): <span className="text-muted">{s.detail}</span>
        </div>
      ))}
    </div>
  );
}

function PositionCatalystsCard({ groups }: { groups: CalendarPositionCatalysts[] }) {
  if (groups.length === 0) return null;
  return (
    <div className="card">
      <h3 style={{ marginBottom: 4 }}>Coming up for your open positions</h3>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
        Earnings dates and Fed / CPI / jobs-report days in the next two weeks.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {groups.map((g) => (
          <div key={g.symbol} style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'baseline' }}>
            <div style={{ minWidth: 110 }}>
              <TickerLink symbol={g.symbol} />
              <span className="text-muted" style={{ fontSize: 12 }}>
                {g.direction}
              </span>
            </div>
            {g.catalysts.length === 0 ? (
              <span className="text-muted" style={{ fontSize: 13 }}>
                Nothing scheduled in the window.
              </span>
            ) : (
              g.catalysts.map((c) => (
                <span key={`${c.kind}-${c.date}-${c.title}`} className={KIND_CHIP[c.kind === 'macro' ? 'macro' : 'earnings'].className}>
                  {c.title} · {c.date.slice(5)} ({countdown(c.days_until)})
                </span>
              ))
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function EventRow({ item }: { item: CalendarItem }) {
  const chip = KIND_CHIP[item.kind] ?? KIND_CHIP.economic;
  const hasNumbers = item.forecast || item.previous || item.actual;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '64px minmax(0, 1fr)',
        gap: 12,
        padding: '10px 0',
        borderTop: '1px solid var(--border)',
        opacity: item.days_until < 0 ? 0.65 : 1,
      }}
    >
      <div className="tabular-nums text-muted" style={{ fontSize: 13 }} title="US Eastern time">
        {item.time_et ? `${item.time_et} ET` : 'all day'}
      </div>
      <div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className={chip.className}>{chip.label}</span>
          {item.impact && item.kind === 'economic' && <span className={IMPACT_CLASS[item.impact] ?? 'badge badge-neutral'}>{item.impact}</span>}
          {item.symbol ? <TickerLink symbol={item.symbol} /> : <span style={{ fontWeight: 600 }}>{item.title}</span>}
          {item.symbol && <span className="text-muted">earnings</span>}
          {item.my_position && (
            <span className="badge badge-green" title={item.position_symbols.join(', ')}>
              My position{item.position_symbols.length > 1 ? 's' : ''}
            </span>
          )}
          <span className="text-muted" style={{ fontSize: 12 }}>
            {countdown(item.days_until)}
          </span>
        </div>
        {hasNumbers && (
          <div className="tabular-nums text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            Forecast {item.forecast ?? '—'} · Previous {item.previous ?? '—'} · Actual {item.actual ?? 'not in this feed'}
          </div>
        )}
        <div className="text-muted" style={{ fontSize: 11, marginTop: 2 }}>
          {item.source_label}
          {item.kind === 'macro' && item.confirmed_by_feed === false && ' · the live feed does not list this day'}
          {item.kind === 'economic' && item.confirmed_by_feed === true && ' · matches the built-in dates'}
        </div>
      </div>
    </div>
  );
}

function Timeline({ data, showMacro, showEarnings, onlyMine }: { data: CalendarResponse; showMacro: boolean; showEarnings: boolean; onlyMine: boolean }) {
  const groups = useMemo(() => {
    const visible = data.items.filter((i) => (i.kind === 'earnings' ? showEarnings : showMacro) && (!onlyMine || i.my_position));
    const byDay = new Map<string, CalendarItem[]>();
    for (const item of visible) byDay.set(item.date, [...(byDay.get(item.date) ?? []), item]);
    return [...byDay.entries()];
  }, [data.items, showMacro, showEarnings, onlyMine]);

  if (groups.length === 0) return <EmptyState>Nothing matches these filters in this range.</EmptyState>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {groups.map(([day, items]) => (
        <div className="card" key={day}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <h3 style={{ fontSize: 15 }}>{dayHeading(day)}</h3>
            {day === data.today && <span className="badge badge-green">Today</span>}
          </div>
          {items.map((item) => (
            <EventRow key={item.id} item={item} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function CalendarPage() {
  const [view, setView] = useState<View>('week');
  const [anchor, setAnchor] = useState(() => isoDay(new Date()));
  const [showMacro, setShowMacro] = useState(true);
  const [showEarnings, setShowEarnings] = useState(true);
  const [onlyMine, setOnlyMine] = useState(false);

  const range = rangeFor(view, anchor);
  const { data, isLoading, error, refetch } = useCalendar(range.from, range.to);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h1 style={{ fontSize: 22 }}>Calendar</h1>
          <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
            Scheduled economic releases, Fed / CPI / jobs-report dates and earnings for your watchlist and open positions. Times are US Eastern.
          </div>
        </div>
      </div>

      <div className="card" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button type="button" className="btn btn-secondary" aria-label="Previous" onClick={() => setAnchor(shiftAnchor(view, anchor, -1))}>
            ‹
          </button>
          <strong style={{ minWidth: 170, textAlign: 'center' }}>{range.label}</strong>
          <button type="button" className="btn btn-secondary" aria-label="Next" onClick={() => setAnchor(shiftAnchor(view, anchor, 1))}>
            ›
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => setAnchor(isoDay(new Date()))}>
            Today
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <FilterChip active={view === 'week'} onClick={() => setView('week')}>
            Week
          </FilterChip>
          <FilterChip active={view === 'month'} onClick={() => setView('month')}>
            Month
          </FilterChip>
          <span style={{ width: 8 }} />
          <FilterChip active={showMacro} onClick={() => setShowMacro(!showMacro)}>
            Macro
          </FilterChip>
          <FilterChip active={showEarnings} onClick={() => setShowEarnings(!showEarnings)}>
            Earnings
          </FilterChip>
          <FilterChip active={onlyMine} onClick={() => setOnlyMine(!onlyMine)}>
            My positions
          </FilterChip>
        </div>
      </div>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />}
      {data && (
        <>
          <SourceNotes sources={data.sources} />
          <PositionCatalystsCard groups={data.position_catalysts} />
          <Timeline data={data} showMacro={showMacro} showEarnings={showEarnings} onlyMine={onlyMine} />
          <div className="text-muted" style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div>
              Sources: {data.sources.map((s) => `${s.label} (${s.status})`).join(' · ')}. Earnings checked for {data.earnings_symbols_checked} symbols.
              Updated {formatRelativeTime(data.generated_at)}.
            </div>
            {data.mismatches.length > 0 && (
              <div>
                Date check, live feed vs built-in table (the table is never changed automatically):
                <ul style={{ margin: '4px 0 0 18px' }}>
                  {data.mismatches.map((m) => (
                    <li key={m.message}>{m.message}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

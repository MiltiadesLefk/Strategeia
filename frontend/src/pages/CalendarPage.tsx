import { useMemo, useState } from 'react';
import { useCalendar } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { CalendarItem, CalendarPositionCatalysts, CalendarResponse, CalendarSourceStatus } from '../api/types';
import { TickerLink } from '../components/TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from '../components/common';
import './calendar.css';

type View = 'week' | 'month';

const KIND_CHIP: Record<string, { label: string; className: string }> = {
  economic: { label: 'Economic', className: 'chip chip-amber' },
  macro: { label: 'Fed / CPI / jobs', className: 'chip chip-amber' },
  earnings: { label: 'Earnings', className: 'chip chip-green' },
};

const IMPACT_CLASS: Record<string, string> = {
  High: 'chip chip-red',
  Medium: 'chip chip-amber',
  Low: 'chip',
  Holiday: 'chip',
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
    <button type="button" className="cal-tog" aria-pressed={active} onClick={onClick}>
      {children}
    </button>
  );
}

function SourceNotes({ sources }: { sources: CalendarSourceStatus[] }) {
  const problems = sources.filter((s) => s.status !== 'ok');
  if (problems.length === 0) return null;
  return (
    <div className="card" style={{ borderColor: 'var(--amber)' }}>
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
      <div className="panel-header">
        <span>Coming up for your open positions</span>
        <span className="label-mono">next 2 weeks</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {groups.map((g) => (
          <div key={g.symbol} style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
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
    <div className="cal-row" style={{ opacity: item.days_until < 0 ? 0.65 : 1 }}>
      <div className="mono text-muted" style={{ fontSize: 12 }} title="US Eastern time">
        {item.time_et ? `${item.time_et} ET` : 'all day'}
      </div>
      <div>
        <div className="cal-evt-line">
          <span className={chip.className}>{chip.label}</span>
          {item.impact && item.kind === 'economic' && <span className={IMPACT_CLASS[item.impact] ?? 'chip'}>{item.impact}</span>}
          {item.symbol ? <TickerLink symbol={item.symbol} /> : <span style={{ fontWeight: 600 }}>{item.title}</span>}
          {item.symbol && <span className="text-muted">earnings</span>}
          {item.my_position && (
            <span className="chip chip-green" title={item.position_symbols.join(', ')}>
              My position{item.position_symbols.length > 1 ? 's' : ''}
            </span>
          )}
          <span className="text-muted" style={{ fontSize: 12 }}>
            {countdown(item.days_until)}
          </span>
        </div>
        {hasNumbers && (
          <div className="mono text-muted" style={{ fontSize: 12, marginTop: 4 }}>
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

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// Week: Mon-Sun cells, so today shows even on a day with no events. Month: the whole month padded to full weeks.
function CalendarGrid({ view, from, to, data }: { view: View; from: string; to: string; data: CalendarResponse }) {
  const start = addDays(from, -((parseDay(from).getDay() + 6) % 7));
  const span = Math.round((parseDay(to).getTime() - parseDay(start).getTime()) / 864e5) + 1;
  const count = view === 'week' ? 7 : Math.ceil(span / 7) * 7;
  const byDay = new Map<string, CalendarItem[]>();
  for (const i of data.items) byDay.set(i.date, [...(byDay.get(i.date) ?? []), i]);
  return (
    <div className="card" style={{ padding: 8 }}>
      <div className="cal-dow label-mono" style={{ marginBottom: 4 }}>
        {DOW.map((d) => (
          <div key={d}>{d}</div>
        ))}
      </div>
      <div className="cal-grid">
        {Array.from({ length: count }, (_, i) => addDays(start, i)).map((day) => {
          const evs = byDay.get(day) ?? [];
          const shown = evs.slice(0, view === 'week' ? 6 : 3);
          const cls = `cal-cell${day === data.today ? ' today' : ''}${day < from || day > to ? ' out' : ''}`;
          return (
            <div key={day} className={cls} aria-current={day === data.today ? 'date' : undefined}>
              <span className="d">{parseDay(day).getDate()}</span>
              {shown.map((e) => (
                <div key={e.id} className={`cal-ev ${e.kind === 'earnings' ? 'earn' : 'macro'}`} title={e.symbol ? `${e.symbol} earnings` : e.title}>
                  {e.symbol ?? e.title}
                </div>
              ))}
              {evs.length > shown.length && <span className="label-mono">+{evs.length - shown.length} more</span>}
            </div>
          );
        })}
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {groups.map(([day, items]) => (
        <div className={day === data.today ? 'card cal-today' : 'card'} key={day}>
          <div className="panel-header">
            <span>{dayHeading(day)}</span>
            {day === data.today && <span className="chip chip-accent">Today</span>}
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
    <div className="cal">
      <div className="cal-head">
        <span className="label-mono cal-eyebrow">Macro &amp; earnings</span>
        <h1 className="page-title">Calendar</h1>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Scheduled economic releases, Fed / CPI / jobs-report dates and earnings for your watchlist and open positions. Times are US Eastern.
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Hold-only: these dates are context for holding, not trade triggers.
        </div>
      </div>

      <div className="card cal-bar">
        <div className="cal-group">
          <div className="cal-nav">
            <button type="button" className="btn btn-secondary" aria-label="Previous" onClick={() => setAnchor(shiftAnchor(view, anchor, -1))}>
              ‹
            </button>
            <span className="cal-range">{range.label}</span>
            <button type="button" className="btn btn-secondary" aria-label="Next" onClick={() => setAnchor(shiftAnchor(view, anchor, 1))}>
              ›
            </button>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => setAnchor(isoDay(new Date()))}>
            Today
          </button>
          <div className="cal-seg">
            <button type="button" aria-pressed={view === 'week'} onClick={() => setView('week')}>
              Week
            </button>
            <button type="button" aria-pressed={view === 'month'} onClick={() => setView('month')}>
              Month
            </button>
          </div>
          <span className="chip" title="Every time on this page is US Eastern">
            All times ET
          </span>
        </div>
        <div className="cal-group">
          <span className="label-mono">Filters</span>
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
          <CalendarGrid view={view} from={range.from} to={range.to} data={data} />
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

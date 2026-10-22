import { EvidenceItems, SentimentSummary } from '../components/EvidenceItems';
import { Fragment, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useLatestRatings, useRateSymbol, useRatingRun, useRatingRuns, useStartRatingRun } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { RatingRow } from '../api/types';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { RatingTable } from '../components/RatingTable';
import { tickerHref } from '../components/TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner } from '../components/common';

const LATEST = 'latest';
const OUTSIDE_LANES = ['big news', 'smart money'];
const REPORT_LANES: Record<string, string> = {
  'market news': 'Market news read (stocks outside the universe)',
  'smart money trades': 'Smart money trades analysed',
};

/** What the day's scan looked at beyond the universe's ratings: each item expands to what was read. */
function ReportSections({ rows }: { rows: RatingRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      {Object.entries(REPORT_LANES).map(([lane, title]) => {
        const mine = rows.filter((r) => r.lane === lane);
        if (mine.length === 0) return null;
        return (
          <div key={lane} style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>{title} ({mine.length})</h3>
            {mine.map((r) => {
              const id = `${lane}:${r.symbol}`;
              // shortcut: wire headlines are stored without their article url, so link a Google News search for the headline; replace with the real url once the wire fetch keeps it
              const items = (r.detail.items?.[lane] ?? []).map((it) => ({
                ...it, url: it.url || `https://news.google.com/search?q=${encodeURIComponent(it.text)}`,
              }));
              return (
                <div key={id} style={{ borderTop: '1px solid var(--border, #2a2d33)', padding: '6px 0', fontSize: 13 }}>
                  <strong>{r.symbol}</strong>{' '}
                  <Link to={tickerHref(r.symbol)} className="text-muted" style={{ fontSize: 12 }}>company →</Link>{' '}
                  <span className="text-muted">{r.note}</span>{' '}
                  <SentimentSummary items={items} />{' '}
                  {items.length > 0 && (
                    <button className="btn btn-secondary" style={{ padding: '0 6px', fontSize: 11 }} onClick={() => setOpen(open === id ? null : id)}>
                      {open === id ? 'close' : `${items.length} items`}
                    </button>
                  )}
                  {open === id && (
                    <EvidenceItems items={items} />
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

/** Stocks outside the universe that the run rated, and why; a highlighted row reached the bar and joins the universe. */
function OutsideRows({ rows }: { rows: RatingRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (rows.length === 0) return null;
  return (
    <div style={{ marginTop: 16 }}>
      <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>Scanned outside the universe ({rows.length})</h3>
      <div className="score-table-wrap">
        <table className="score-table">
          <thead>
            <tr><th>Stock</th><th style={{ textAlign: 'right' }}>Score /100</th><th>Result</th><th>Why scanned</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const id = `${r.lane}:${r.symbol}`;
              return (
                <Fragment key={id}>
                  <tr onClick={() => setOpen(open === id ? null : id)} style={{ cursor: 'pointer', background: r.passed ? 'rgba(91, 156, 255, 0.12)' : undefined }}>
                    <td>
                      <strong>{r.symbol}</strong>{' '}
                      <Link to={tickerHref(r.symbol)} onClick={(e) => e.stopPropagation()} className="text-muted" style={{ fontSize: 12 }}>company →</Link>
                    </td>
                    <td style={{ textAlign: 'right' }}>{r.percent}</td>
                    <td>
                      {r.passed ? <span style={{ color: 'var(--accent, #5b9cff)', fontWeight: 600 }}>Joins the universe</span> : 'Below bar'}
                    </td>
                    <td className="text-muted" style={{ fontSize: 13 }}>{r.detail.found ?? r.note}</td>
                    <td className="text-muted">{open === id ? 'hide table' : 'show table'}</td>
                  </tr>
                  {open === id && <tr><td colSpan={5}><RatingTable row={r} /></td></tr>}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function when(iso: string): string {
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString();
}

function RatingRows({ rows, onRate, rating }: { rows: RatingRow[]; onRate?: (symbol: string) => void; rating?: string | null }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="score-table-wrap">
      <table className="score-table">
        <thead>
          <tr>
            <th>Stock</th><th style={{ textAlign: 'right' }}>Score /100</th><th>Result</th><th>Dip</th>
            {onRate && <th>Rated</th>}<th />{onRate && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Fragment key={r.symbol}>
              <tr onClick={() => setOpen(open === r.symbol ? null : r.symbol)} style={{ cursor: 'pointer' }}>
                <td>
                  <strong>{r.symbol}</strong>{' '}
                  <Link to={tickerHref(r.symbol)} onClick={(e) => e.stopPropagation()} className="text-muted" style={{ fontSize: 12 }}>company →</Link>
                  {r.note.startsWith('Added to universe') && (
                    <div style={{ fontSize: 12, marginTop: 2, color: 'var(--accent, #5b9cff)', fontWeight: 600 }}>{r.note}</div>
                  )}
                </td>
                <td style={{ textAlign: 'right' }}>{r.percent}</td>
                <td>{r.bought ? 'Bought' : r.passed ? 'Passed' : 'Below bar'}</td>
                <td>{r.dip ? 'dip' : ''}</td>
                {onRate && <td className="text-muted" style={{ fontSize: 12 }}>{when(r.run_at)}</td>}
                <td className="text-muted">{open === r.symbol ? 'hide table' : 'show table'}</td>
                {onRate && (
                  <td onClick={(e) => e.stopPropagation()}>
                    <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: 12 }} disabled={!!rating} onClick={() => onRate(r.symbol)}>
                      {rating === r.symbol ? 'Rating…' : 'Rate'}
                    </button>
                  </td>
                )}
              </tr>
              {open === r.symbol && (
                <tr><td colSpan={onRate ? 7 : 5}><RatingTable row={r} /></td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Every rating the app has made: the newest per company (rate one now), and each daily or manual run as it was that day. */
export function RatingsPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get('view') ?? LATEST;
  const runs = useRatingRuns();
  const latest = useLatestRatings();
  const startAll = useStartRatingRun();
  const rateOne = useRateSymbol();
  const run = useRatingRun(view === LATEST ? null : view);
  const going = !!runs.data?.some((r) => r.running);
  // When a run finishes, the run list and the latest-per-company table must pick up what it saved.
  const queryClient = useQueryClient();
  const wasGoing = useRef(false);
  useEffect(() => {
    if (wasGoing.current && !going) {
      queryClient.invalidateQueries({ queryKey: ['rating-runs'] });
      queryClient.invalidateQueries({ queryKey: ['ratings-latest'] });
    }
    wasGoing.current = going;
  }, [going, queryClient]);
  const select = (v: string) => setParams(v === LATEST ? {} : { view: v });
  const latestRows = Object.values(latest.data ?? {}).sort((a, b) => b.percent - a.percent);
  const error = (startAll.error ?? rateOne.error) as ApiError | null;

  async function rateAll() {
    const result = await startAll.mutateAsync();
    select(result.run_id);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="page-header">
        <div>
          <h1>Ratings</h1>
          <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
            The 32-point evidence rating, explained part by part. Pick a day on the left to see that day&apos;s table. Rating only rates; it buys nothing.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <CompanyDropdown value="" onChange={(s) => rateOne.mutate(s)} />
          <button className="btn btn-primary" onClick={rateAll} disabled={startAll.isPending || going}>
            {going ? 'Rating…' : 'Rate all'}
          </button>
        </div>
      </div>
      {rateOne.isPending && <div className="text-muted" style={{ fontSize: 13 }}>Rating {rateOne.variables}…</div>}
      {error && <ErrorBanner message={error.message ?? 'Could not rate'} />}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) 1fr', gap: 16, alignItems: 'start' }}>
        <div className="card" style={{ padding: 8 }}>
          <button
            onClick={() => select(LATEST)}
            className="btn btn-secondary"
            style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 6, opacity: view === LATEST ? 1 : 0.7 }}
          >
            <div>Latest per company</div>
            <div className="text-muted" style={{ fontSize: 12 }}>{latestRows.length} companies · rate one by one here</div>
          </button>
          {runs.isLoading ? <LoadingSpinner /> : runs.error ? (
            <ErrorBanner message="Could not load the rating history" onRetry={() => runs.refetch()} />
          ) : (
            runs.data?.map((r) => (
              <button
                key={r.run_id}
                onClick={() => select(r.run_id)}
                className="btn btn-secondary"
                style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 6, opacity: r.run_id === view ? 1 : 0.7 }}
              >
                <div>{when(r.run_at)}{r.running ? ' · running' : ''}</div>
                <div className="text-muted" style={{ fontSize: 12 }}>
                  {r.source === 'scan' ? 'Morning scan' : r.source === 'replay' ? 'As-if replay' : r.source === 'find' ? 'Added to universe' : 'Manual'} · {r.rated} rated · {r.passed} passed · {r.bought} bought
                </div>
              </button>
            ))
          )}
        </div>
        <div className="card">
          {view === LATEST ? (
            latest.isLoading ? <LoadingSpinner /> : latestRows.length === 0 ? (
              <EmptyState>No ratings yet. Choose a company above, press Rate all, or turn on the daily scan (Settings).</EmptyState>
            ) : (
              <RatingRows rows={latestRows} onRate={(s) => rateOne.mutate(s)} rating={rateOne.isPending ? rateOne.variables : null} />
            )
          ) : run.isLoading ? <LoadingSpinner /> : run.error ? (
            <ErrorBanner message="Could not load this run" onRetry={() => run.refetch()} />
          ) : (
            <>
              <RatingRows key={view} rows={(run.data?.ratings ?? []).filter((r) => !(r.lane in REPORT_LANES) && !OUTSIDE_LANES.includes(r.lane))} />
              <OutsideRows rows={(run.data?.ratings ?? []).filter((r) => OUTSIDE_LANES.includes(r.lane))} />
              <ReportSections rows={run.data?.ratings ?? []} />
              {run.data?.running && <div className="text-muted" style={{ padding: 8 }}>{(run.data.ratings ?? []).length === 0
                ? 'Reading the news with the AI first (one call per batch of headlines, can take a few minutes); the ratings appear once that is done.'
                : 'Still rating… rows appear as they finish.'}</div>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

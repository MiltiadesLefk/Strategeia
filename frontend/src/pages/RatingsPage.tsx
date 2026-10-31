import { EvidenceItems, SentimentSummary } from '../components/EvidenceItems';
import { Fragment, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useLatestRatings, useRateSymbol, useRatingRun, useRatingRuns, useStartRatingRun } from '../api/hooks';
import type { ApiError } from '../api/client';
import type { RatingRow, RunUsage } from '../api/types';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { RatingTable } from '../components/RatingTable';
import { tickerHref } from '../components/TickerLink';
import './ratings.css';
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
          <div key={lane} className="ratings-section">
            <div className="panel-header"><span>{title}</span><span className="chip">{mine.length}</span></div>
            {mine.map((r) => {
              const id = `${lane}:${r.symbol}`;
              // shortcut: wire headlines are stored without their article url, so link a Google News search for the headline; replace with the real url once the wire fetch keeps it
              const items = (r.detail.items?.[lane] ?? []).map((it) => ({
                ...it, url: it.url || `https://news.google.com/search?q=${encodeURIComponent(it.text)}`,
              }));
              return (
                <div key={id} className="report-row">
                  <strong className="mono">{r.symbol}</strong>{' '}
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
      <div className="panel-header" style={{ padding: '12px 12px 8px', marginBottom: 0 }}><span>Scanned outside the universe</span><span className="chip">{rows.length}</span></div>
      <div className="score-table-wrap">
        <table className="ratings-table">
          <thead>
            <tr><th>Stock</th><th className="num">Score /100</th><th>Result</th><th>Why scanned</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const id = `${r.lane}:${r.symbol}`;
              return (
                <Fragment key={id}>
                  <tr onClick={() => setOpen(open === id ? null : id)} className={`row-click${r.passed ? ' row-joined' : ''}`}>
                    <td>
                      <span className="sym mono">{r.symbol}</span>{' '}
                      <Link to={tickerHref(r.symbol)} onClick={(e) => e.stopPropagation()} className="text-muted" style={{ fontSize: 12 }}>company →</Link>
                    </td>
                    <td className="num mono">{r.percent}</td>
                    <td>
                      {r.passed ? <span className="chip chip-accent">Joins the universe</span> : <span className="chip">Below bar</span>}
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

/** "Used about 1.5% of the 5-hour window · 41 AI calls · 190k tokens": the AI part of one Rate all run. */
function usageText(u: RunUsage | null | undefined): string | null {
  if (!u || u.calls === 0) return null;
  const tokens = Math.round((u.tokens_in + u.tokens_out) / 1000);
  const window = u.five_hour_used_pct === null ? 'window % unreadable' : `${u.five_hour_used_pct}% of the 5-hour window`;
  return `AI used ${window} · ${u.calls} calls · ${tokens}k tokens`;
}

function RatingRows({ rows, onRate, rating }: { rows: RatingRow[]; onRate?: (symbol: string) => void; rating?: string | null }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="score-table-wrap">
      <table className="ratings-table">
        <thead>
          <tr>
            <th>Stock</th><th className="num">Score /100</th><th>Result</th><th>Dip</th>
            {onRate && <th>Rated</th>}<th />{onRate && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Fragment key={r.symbol}>
              <tr onClick={() => setOpen(open === r.symbol ? null : r.symbol)} className="row-click">
                <td>
                  <span className="sym mono">{r.symbol}</span>{' '}
                  <Link to={tickerHref(r.symbol)} onClick={(e) => e.stopPropagation()} className="text-muted" style={{ fontSize: 12 }}>company →</Link>
                  {r.note.startsWith('Added to universe') && (
                    <div className="chip chip-accent" style={{ marginTop: 4 }}>{r.note}</div>
                  )}
                </td>
                <td className="num mono">{r.percent}</td>
                <td>{r.bought ? <span className="chip chip-accent">Bought</span> : r.passed ? <span className="chip chip-green">Passed</span> : <span className="chip chip-red">Below bar</span>}</td>
                <td>{r.dip ? <span className="chip chip-amber">dip</span> : ''}</td>
                {onRate && <td className="text-muted mono" style={{ fontSize: 11 }}>{when(r.run_at)}</td>}
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
          <h1 className="page-title">Ratings</h1>
          <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
            The evidence rating, explained part by part. Pick a day on the left to see that day&apos;s table. Rating only rates; it buys nothing.
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
      <div className="ratings-layout">
        <div className="card ratings-runs">
          <div className="panel-header"><span>Ratings runs</span></div>
          <button onClick={() => select(LATEST)} className={`run-item${view === LATEST ? ' active' : ''}`}>
            <div className="run-title">Latest per company <span className="chip chip-accent">aggregate</span></div>
            <div className="run-sub">{latestRows.length} companies · rate one by one here</div>
          </button>
          {runs.isLoading ? <LoadingSpinner /> : runs.error ? (
            <ErrorBanner message="Could not load the rating history" onRetry={() => runs.refetch()} />
          ) : (
            runs.data?.map((r) => (
              <button
                key={r.run_id}
                onClick={() => select(r.run_id)}
                className={`run-item${r.run_id === view ? ' active' : ''}`}
              >
                <div className="run-title">{when(r.run_at)}{r.running ? ' · running' : ''}</div>
                <div className="run-sub">
                  {r.source === 'scan' ? 'Morning scan' : r.source === 'replay' ? 'As-if replay' : r.source === 'find' ? 'Added to universe' : 'Manual'} · {r.rated} rated · {r.passed} passed · {r.bought} bought
                </div>
                {usageText(r.usage) && <div className="run-ai">{usageText(r.usage)}</div>}
              </button>
            ))
          )}
        </div>
        <div className="card card-flush">
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
              {usageText(run.data?.usage) && <div className="text-muted" style={{ padding: '0 8px 8px', fontSize: 13 }}>{usageText(run.data?.usage)}</div>}
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

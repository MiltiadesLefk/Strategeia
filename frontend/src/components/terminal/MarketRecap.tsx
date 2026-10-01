// Idea from OpenTerminal's RecapWidget (MIT, ErTasselli/OpenTerminal): a short
// market recap card. Ours is rule-based numbers first; an AI paragraph is
// added only on request, only when an AI provider is configured, and is
// labelled as AI-written.
import { useState } from 'react';
import { useSettingsStatus, useTerminalRecap } from '../../api/hooks';
import type { RecapResponse, SectorEtfTile, TerminalMover, TerminalSectorMove } from '../../api/types';
import { AiNoteCard, ErrorBanner, LoadingSpinner, formatPct } from '../common';
import { DataFreshness } from '../DataFreshness';
import { TickerLink } from '../TickerLink';

const RECAP_SAMPLE = 60;

function tone(change: number | null): string {
  return change === null ? 'text-muted' : change >= 0 ? 'text-green' : 'text-red';
}

function Movers({ title, rows }: { title: string; rows: TerminalMover[] }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 4 }}>
        {title}
      </div>
      {rows.length === 0 && <div className="text-muted">none</div>}
      {rows.map((m) => (
        <div key={m.symbol} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 13 }}>
          <TickerLink symbol={m.symbol} fontWeight={600} />
          <span className={`tabular-nums ${tone(m.change_pct)}`}>{formatPct(m.change_pct, 2)}</span>
        </div>
      ))}
    </div>
  );
}

function SectorList({ title, rows }: { title: string; rows: (TerminalSectorMove | SectorEtfTile)[] }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 4 }}>
        {title}
      </div>
      {rows.length === 0 && <div className="text-muted">none</div>}
      {rows.map((r) => {
        const change = 'avg_change_pct' in r ? r.avg_change_pct : r.change_pct;
        const label = 'avg_change_pct' in r ? `${r.sector} (${r.count})` : `${r.symbol} ${r.sector}`;
        return (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 13 }}>
            <span>{label}</span>
            <span className={`tabular-nums ${tone(change)}`}>{formatPct(change, 2)}</span>
          </div>
        );
      })}
    </div>
  );
}

function Breadth({ recap }: { recap: RecapResponse }) {
  const b = recap.breadth;
  const share = (n: number) => (b.total ? `${(n / b.total) * 100}%` : '0%');
  return (
    <div>
      <div style={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden', background: 'var(--border)' }} aria-hidden="true">
        <div style={{ width: share(b.advancers), background: 'var(--green)' }} />
        <div style={{ width: share(b.unchanged), background: 'var(--text-muted)', opacity: 0.4 }} />
        <div style={{ width: share(b.decliners), background: 'var(--red)' }} />
      </div>
      <div className="tabular-nums" style={{ fontSize: 13, marginTop: 6, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <span className="text-green">{b.advancers} up</span>
        <span className="text-red">{b.decliners} down</span>
        <span className="text-muted">{b.unchanged} flat</span>
        <span>
          {b.new_highs} at 52-week high · {b.new_lows} at low
        </span>
      </div>
      <div className="text-muted" style={{ fontSize: 11 }}>
        Counted within the {b.total} symbols read, not the whole market. Highs and lows judged for {b.high_low_judged} with a full year of history.
      </div>
    </div>
  );
}

export function MarketRecap() {
  const [wantAi, setWantAi] = useState(false);
  const status = useSettingsStatus();
  const aiAvailable = !!status.data?.ai_online;
  const query = useTerminalRecap(wantAi && aiAvailable, RECAP_SAMPLE);
  const data = query.data;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Market recap</h2>
        {aiAvailable && !wantAi && (
          <button type="button" className="btn btn-secondary" onClick={() => setWantAi(true)}>
            Add an AI paragraph
          </button>
        )}
      </div>
      {query.isLoading && <LoadingSpinner label="Building the recap…" />}
      {query.isError && <ErrorBanner message="Could not load the recap." onRetry={() => query.refetch()} />}
      {data && (
        <>
          <div>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 4 }}>
              Summary · rule-based, written from the numbers below
            </div>
            <div>{data.summary}</div>
          </div>
          {data.ai_paragraph && <AiNoteCard label="AI paragraph (written by a model from the same numbers, not rule-based)" provider={data.ai_provider} text={data.ai_paragraph} />}
          {wantAi && aiAvailable && !data.ai_paragraph && !query.isFetching && (
            <div className="text-muted" style={{ fontSize: 12 }}>
              The AI paragraph could not be written right now; the rule-based recap above is complete.
            </div>
          )}
          <Breadth recap={data} />
          <div className="terminal-recap-grid">
            <Movers title="Top gainers (1 day)" rows={data.top_gainers} />
            <Movers title="Top decliners (1 day)" rows={data.top_losers} />
            <SectorList title="Strongest sectors (your symbols)" rows={data.sector_leaders} />
            <SectorList title="Weakest sectors (your symbols)" rows={data.sector_laggards} />
            <SectorList title="Strongest sector ETFs" rows={data.etf_leaders} />
            <SectorList title="Weakest sector ETFs" rows={data.etf_laggards} />
          </div>
          <div className="text-muted" style={{ fontSize: 12, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <span>
              Read the first {data.sampled} of {data.universe_size} symbols in your universe.
              {data.vix_value !== null && ` VIX ${data.vix_value.toFixed(2)} (${data.vix_regime}).`}
            </span>
            <DataFreshness updatedAt={query.dataUpdatedAt} serverAsOf={data.as_of} isFetching={query.isFetching} />
          </div>
        </>
      )}
    </div>
  );
}

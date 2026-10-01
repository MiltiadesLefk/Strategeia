// Idea from OpenTerminal's HeatmapWidget (MIT, ErTasselli/OpenTerminal): a
// sector-grouped treemap sized by market cap and coloured by change. Rebuilt
// on our own universe and data providers, with our own treemap layout.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTerminalHeatmap } from '../../api/hooks';
import type { HeatmapResponse, TerminalWindow } from '../../api/types';
import { DataFreshness } from '../DataFreshness';
import { tickerHref } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatPct } from '../common';
import { squarify } from '../../lib/treemap';

const WINDOWS: { value: TerminalWindow; label: string }[] = [
  { value: '1d', label: '1 day' },
  { value: '5d', label: '5 days' },
  { value: '1m', label: '1 month' },
];
const SAMPLE_OPTIONS = [30, 60, 100, 200];
const DEFAULT_SAMPLE = 60;
// The change at which a tile reaches full colour, per window.
const FULL_COLOUR_AT: Record<TerminalWindow, number> = { '1d': 3, '5d': 6, '1m': 12 };
// Height reserved for each sector's name, as a share of the whole map.
const SECTOR_LABEL_HEIGHT_PCT = 4.5;

export function changeColour(change: number, window: TerminalWindow): { background: string; color: string } {
  const strength = Math.min(Math.abs(change) / FULL_COLOUR_AT[window], 1);
  const alpha = 0.18 + strength * 0.72;
  const rgb = change >= 0 ? '16, 185, 129' : '239, 68, 68';
  return { background: `rgba(${rgb}, ${alpha.toFixed(2)})`, color: alpha > 0.55 ? '#fff' : 'var(--text)' };
}

function Treemap({ data }: { data: HeatmapResponse }) {
  const sectors = squarify(data.sectors, (s) => s.weight);
  return (
    <div className="terminal-heatmap" role="img" aria-label={`Sector heatmap, ${data.window} change`}>
      {sectors.map(({ item: sector, x, y, w, h }) => {
        const labelH = Math.min(SECTOR_LABEL_HEIGHT_PCT / (h * 100), 0.5);
        const tiles = squarify(sector.tiles, (t) => t.weight, { x: 0, y: labelH, w: 1, h: 1 - labelH });
        return (
          <div
            key={sector.sector}
            className="terminal-sector"
            style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }}
          >
            <div className="terminal-sector-label" style={{ height: `${labelH * 100}%` }} title={`${sector.sector}: average ${formatPct(sector.avg_change_pct, 2)}`}>
              {sector.sector} <span className="tabular-nums">{formatPct(sector.avg_change_pct, 2)}</span>
            </div>
            {tiles.map(({ item: tile, x: tx, y: ty, w: tw, h: th }) => (
              <Link
                key={tile.symbol}
                to={tickerHref(tile.symbol)}
                className="terminal-tile"
                title={`${tile.name} (${tile.symbol}) ${formatPct(tile.change_pct, 2)}`}
                style={{ left: `${tx * 100}%`, top: `${ty * 100}%`, width: `${tw * 100}%`, height: `${th * 100}%`, ...changeColour(tile.change_pct, data.window) }}
              >
                <strong>{tile.symbol}</strong>
                <span className="tabular-nums">{formatPct(tile.change_pct, 2)}</span>
              </Link>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function SectorHeatmap() {
  const [window, setWindow] = useState<TerminalWindow>('1d');
  const [limit, setLimit] = useState(DEFAULT_SAMPLE);
  const query = useTerminalHeatmap(window, limit);
  const data = query.data;
  const windowLabel = WINDOWS.find((w) => w.value === window)?.label;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Sector heatmap</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <div role="group" aria-label="Change window" style={{ display: 'flex', gap: 4 }}>
            {WINDOWS.map((w) => (
              <button key={w.value} type="button" className={`btn ${window === w.value ? 'btn-primary' : 'btn-secondary'}`} aria-pressed={window === w.value} onClick={() => setWindow(w.value)}>
                {w.label}
              </button>
            ))}
          </div>
          <label className="text-muted" style={{ fontSize: 13 }}>
            Symbols read{' '}
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {SAMPLE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  first {n}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {query.isLoading && <LoadingSpinner label="Reading prices…" />}
      {query.isError && <ErrorBanner message="Could not load the heatmap." onRetry={() => query.refetch()} />}

      {data && (
        <>
          {data.sectors.length === 0 ? <EmptyState>No price data was available for the sampled symbols.</EmptyState> : <Treemap data={data} />}
          <div className="text-muted" style={{ fontSize: 12, display: 'flex', gap: 12, flexWrap: 'wrap', justifyContent: 'space-between' }}>
            <span>
              Showing the first {data.sampled} of {data.universe_size} symbols in your universe. Tile area is{' '}
              {data.weighting === 'market_cap' ? 'market cap (a missing one is filled with the median)' : 'equal, because market caps were mostly unavailable'}; colour is the {windowLabel} change, full colour at ±{FULL_COLOUR_AT[data.window]}%.
              {data.missing.length > 0 && ` No data for ${data.missing.length}: ${data.missing.slice(0, 8).join(', ')}${data.missing.length > 8 ? '…' : ''}.`}
            </span>
            <DataFreshness updatedAt={query.dataUpdatedAt} serverAsOf={data.as_of} isFetching={query.isFetching} />
          </div>

          <div>
            <div className="text-muted" style={{ fontSize: 13, marginBottom: 6 }}>
              Sector ETFs ({windowLabel}): the whole-market view of each sector, independent of your list
            </div>
            <div className="terminal-etf-row">
              {data.sector_etfs.map((e) => (
                <Link
                  key={e.symbol}
                  to={tickerHref(e.symbol)}
                  className="terminal-etf"
                  title={e.sector}
                  style={e.change_pct === null ? undefined : changeColour(e.change_pct, data.window)}
                >
                  <strong>{e.symbol}</strong>
                  <span className="tabular-nums">{e.change_pct === null ? 'n/a' : formatPct(e.change_pct, 2)}</span>
                </Link>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

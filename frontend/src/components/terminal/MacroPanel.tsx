// Idea from OpenTerminal's MacroWidget (MIT, ErTasselli/OpenTerminal): a grid
// of macro tiles. Ours reads daily bars from the app's data providers, shows a
// real sparkline and as-of date on every tile, and says "not available"
// instead of drawing anything it could not fetch.
import { useTerminalMacro } from '../../api/hooks';
import type { MacroResponse, MacroTile, YieldCurve } from '../../api/types';
import { DataFreshness } from '../DataFreshness';
import { Sparkline } from '../Sparkline';
import { ErrorBanner, LoadingSpinner, formatNumber, formatPct } from '../common';

function Badge({ tone, children }: { tone: 'red' | 'green' | 'neutral'; children: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

const GROUP_TITLES: Record<MacroTile['group'], string> = {
  index: 'Indexes',
  volatility: 'Volatility',
  currency: 'Dollar',
  yield: 'Treasury yields',
};
const GROUP_ORDER: MacroTile['group'][] = ['index', 'volatility', 'currency', 'yield'];

function tileValue(tile: MacroTile): string {
  if (tile.value === null) return 'not available';
  return tile.unit === 'percent' ? `${tile.value.toFixed(3)}%` : tile.value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function Tile({ tile, regime }: { tile: MacroTile; regime?: MacroResponse['vix_regime'] }) {
  const change = tile.change_pct;
  const colour = change === null ? 'var(--text-muted)' : change >= 0 ? 'var(--green)' : 'var(--red)';
  return (
    <div className="card terminal-macro-tile" data-available={tile.available}>
      <div className="text-muted" style={{ fontSize: 13, display: 'flex', justifyContent: 'space-between', gap: 6 }}>
        <span>{tile.label}</span>
        {tile.id === 'vix' && regime && <Badge tone={regime === 'elevated' ? 'red' : 'green'}>{regime}</Badge>}
      </div>
      <div className="tabular-nums" style={{ fontSize: 22, fontWeight: 600 }}>
        {tileValue(tile)}
      </div>
      {tile.available ? (
        <>
          <div className="tabular-nums" style={{ fontSize: 13, color: colour }}>
            {tile.unit === 'percent' && tile.change !== null ? `${tile.change >= 0 ? '+' : ''}${formatNumber(tile.change, 3)} pts` : formatPct(change, 2)}
          </div>
          <Sparkline values={tile.sparkline} width={150} height={30} />
          <div className="text-muted" style={{ fontSize: 11 }}>
            as of {tile.as_of ?? 'unknown'} · {tile.source}
          </div>
        </>
      ) : (
        <div className="text-muted" style={{ fontSize: 12 }}>
          The data providers returned nothing for {tile.symbol}.
        </div>
      )}
    </div>
  );
}

function YieldCurveCard({ curve }: { curve: YieldCurve }) {
  const known = curve.points.filter((p) => p.value !== null);
  const values = known.map((p) => p.value as number);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const width = 280;
  const height = 70;
  const coords = known.map((p, i) => {
    const x = known.length === 1 ? width / 2 : (i / (known.length - 1)) * width;
    const y = height - ((p.value as number) - min) / range * (height - 10) - 5;
    return { x, y, p };
  });
  return (
    <div className="card terminal-macro-tile">
      <div className="text-muted" style={{ fontSize: 13, display: 'flex', justifyContent: 'space-between' }}>
        <span>Yield curve</span>
        {curve.inverted === null ? <Badge tone="neutral">unknown</Badge> : <Badge tone={curve.inverted ? 'red' : 'green'}>{curve.inverted ? 'inverted' : 'normal'}</Badge>}
      </div>
      {known.length >= 2 ? (
        <svg width="100%" viewBox={`0 0 ${width} ${height + 16}`} role="img" aria-label="Treasury yield curve">
          <polyline points={coords.map((c) => `${c.x},${c.y}`).join(' ')} fill="none" stroke="var(--text-muted)" strokeWidth={1.5} />
          {coords.map((c) => (
            <g key={c.p.label}>
              <circle cx={c.x} cy={c.y} r={3} fill="var(--text)" />
              <text x={c.x} y={height + 12} fontSize={10} textAnchor={c.x <= 0 ? 'start' : c.x >= width ? 'end' : 'middle'} fill="var(--text-muted)">
                {c.p.label} {formatNumber(c.p.value, 2)}
              </text>
            </g>
          ))}
        </svg>
      ) : (
        <div className="text-muted">not available</div>
      )}
      <div className="tabular-nums" style={{ fontSize: 13 }}>
        10y minus 3m: {curve.spread_10y_3m === null ? 'not available' : `${curve.spread_10y_3m >= 0 ? '+' : ''}${curve.spread_10y_3m.toFixed(2)} pts`}
      </div>
      {curve.note && (
        <div className="text-muted" style={{ fontSize: 11 }}>
          {curve.note}
        </div>
      )}
    </div>
  );
}

export function MacroPanel() {
  const query = useTerminalMacro();
  const data = query.data;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Macro</h2>
        {data && <DataFreshness updatedAt={query.dataUpdatedAt} serverAsOf={data.as_of} isFetching={query.isFetching} />}
      </div>
      {query.isLoading && <LoadingSpinner label="Reading macro series…" />}
      {query.isError && <ErrorBanner message="Could not load the macro panel." onRetry={() => query.refetch()} />}
      {data && (
        <>
          {GROUP_ORDER.map((group) => {
            const tiles = data.tiles.filter((t) => t.group === group);
            if (tiles.length === 0) return null;
            return (
              <div key={group}>
                <div className="text-muted" style={{ fontSize: 12, marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  {GROUP_TITLES[group]}
                  {group === 'volatility' && ` (elevated from ${data.vix_threshold})`}
                </div>
                <div className="terminal-macro-grid">
                  {tiles.map((t) => (
                    <Tile key={t.id} tile={t} regime={data.vix_regime} />
                  ))}
                  {group === 'yield' && <YieldCurveCard curve={data.yield_curve} />}
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

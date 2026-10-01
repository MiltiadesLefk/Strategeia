import type { ShadowSignal } from '../api/types';

/** The silent (recorded, not scored) signals on a trade plan. Muted on purpose:
 *  these never change the confidence, direction or size. */
export function SilentSignals({ signals }: { signals?: ShadowSignal[] | null }) {
  if (!signals || signals.length === 0) return null;
  return (
    <div
      style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--border)', fontSize: 11, lineHeight: 1.5 }}
      className="text-muted"
      data-testid="silent-signals"
    >
      <div style={{ fontWeight: 700 }}>Silent signals (not scored)</div>
      {signals.map((s) => (
        <div key={s.name} title={s.reason}>
          {s.name.replace(/_/g, ' ')}:{' '}
          {s.available ? (
            <>
              {s.value ?? 'read'} · would have added{' '}
              <span className="tabular-nums">{s.would_score > 0 ? `+${s.would_score}` : s.would_score}</span>
            </>
          ) : (
            <>no data yet</>
          )}
        </div>
      ))}
      <div style={{ opacity: 0.8 }}>
        New signals are only recorded until a backtest shows they beat luck; then they earn points.
      </div>
    </div>
  );
}

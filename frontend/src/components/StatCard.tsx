import { Sparkline } from './Sparkline';

export function StatCard({
  label,
  value,
  positive,
  trend,
  note,
  size = 'default',
}: {
  label: string;
  value: string;
  positive?: boolean | null;
  /** Real historical series backing this stat (e.g. equity curve) — omit
   * rather than fabricate one when no genuine time series exists. */
  trend?: number[];
  /** Qualifier under the number. Its main job is sample size: "67% win rate"
   * off three trades is noise, and a stat card that renders it at the same
   * confidence as a hundred-trade figure is quietly overclaiming. */
  note?: string;
  /** 'hero' for the two stats that actually describe account performance, so
   * they aren't visually equal to scan-activity counters. */
  size?: 'default' | 'hero';
}) {
  const hero = size === 'hero';
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div className="text-muted" style={{ fontSize: 13 }}>
        {label}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
        <div
          className="tabular-nums"
          style={{
            fontSize: hero ? 34 : 26,
            fontWeight: 700,
            lineHeight: 1.1,
            color: positive === true ? 'var(--green)' : positive === false ? 'var(--red)' : 'var(--text)',
          }}
        >
          {value}
        </div>
        {trend && trend.length >= 2 && <Sparkline values={trend} width={hero ? 84 : 64} height={hero ? 30 : 24} />}
      </div>
      {note && (
        <div className="text-muted" style={{ fontSize: 11 }}>
          {note}
        </div>
      )}
    </div>
  );
}

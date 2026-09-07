import { Sparkline } from './Sparkline';

export function StatCard({
  label,
  value,
  positive,
  trend,
}: {
  label: string;
  value: string;
  positive?: boolean | null;
  /** Real historical series backing this stat (e.g. equity curve) — omit
   * rather than fabricate one when no genuine time series exists. */
  trend?: number[];
}) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div className="text-muted" style={{ fontSize: 13 }}>
        {label}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
        <div
          className="tabular-nums"
          style={{
            fontSize: 26,
            fontWeight: 700,
            color: positive === true ? 'var(--green)' : positive === false ? 'var(--red)' : 'var(--text)',
          }}
        >
          {value}
        </div>
        {trend && trend.length >= 2 && <Sparkline values={trend} width={64} height={24} />}
      </div>
    </div>
  );
}

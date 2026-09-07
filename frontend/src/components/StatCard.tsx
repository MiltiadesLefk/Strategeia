export function StatCard({
  label,
  value,
  positive,
}: {
  label: string;
  value: string;
  positive?: boolean | null;
}) {
  return (
    <div className="card">
      <div className="text-muted" style={{ fontSize: 13, marginBottom: 6 }}>
        {label}
      </div>
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
    </div>
  );
}

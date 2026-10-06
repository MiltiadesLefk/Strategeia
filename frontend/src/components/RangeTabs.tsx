const RANGES = [
  { value: '1d', label: '1D' },
  { value: '1w', label: '1W' },
  { value: '1mo', label: '1M' },
  { value: '3mo', label: '3M' },
  { value: '1y', label: '1Y' },
];

export function RangeTabs({ value, onChange }: { value: string; onChange: (range: string) => void }) {
  return (
    <div style={{ display: 'inline-flex', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 9999, padding: 3, gap: 2 }}>
      {RANGES.map((r) => (
        <button
          key={r.value}
          onClick={() => onChange(r.value)}
          style={{
            border: 'none',
            padding: '5px 12px',
            borderRadius: 9999,
            fontSize: 12,
            fontWeight: 600,
            cursor: 'pointer',
            background: value === r.value ? 'rgba(255,255,255,0.14)' : 'transparent',
            color: value === r.value ? 'var(--text)' : 'var(--text-muted)',
            boxShadow: 'none',
          }}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

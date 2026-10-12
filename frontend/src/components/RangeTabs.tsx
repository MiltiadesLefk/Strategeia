const RANGES = [
  { value: '1d', label: '1D' },
  { value: '1w', label: '1W' },
  { value: '1mo', label: '1M' },
  { value: '3mo', label: '3M' },
  { value: '1y', label: '1Y' },
];

export function RangeTabs({ value, onChange }: { value: string; onChange: (range: string) => void }) {
  return (
    <div style={{ display: 'inline-flex', background: 'var(--canvas)', border: '1px solid var(--border)', borderRadius: 6, padding: 2, gap: 2 }}>
      {RANGES.map((r) => (
        <button
          key={r.value}
          className="range-tab"
          aria-pressed={value === r.value}
          onClick={() => onChange(r.value)}
          style={{
            border: 'none',
            padding: '3px 10px',
            borderRadius: 4,
            fontSize: 12,
            fontWeight: 500,
            background: value === r.value ? 'var(--glass-strong)' : 'transparent',
            color: value === r.value ? 'var(--text)' : 'var(--text-muted)',
          }}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

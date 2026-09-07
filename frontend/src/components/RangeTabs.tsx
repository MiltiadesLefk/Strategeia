const RANGES = [
  { value: '1mo', label: '1M' },
  { value: '3mo', label: '3M' },
  { value: '6mo', label: '6M' },
  { value: '1y', label: '1Y' },
];

export function RangeTabs({ value, onChange }: { value: string; onChange: (range: string) => void }) {
  return (
    <div style={{ display: 'inline-flex', background: 'var(--canvas)', borderRadius: 8, padding: 3, gap: 2 }}>
      {RANGES.map((r) => (
        <button
          key={r.value}
          onClick={() => onChange(r.value)}
          style={{
            border: 'none',
            padding: '5px 12px',
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 600,
            cursor: 'pointer',
            background: value === r.value ? 'var(--card)' : 'transparent',
            color: value === r.value ? 'var(--text)' : 'var(--text-muted)',
            boxShadow: value === r.value ? 'var(--shadow)' : 'none',
          }}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

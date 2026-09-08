export interface TabItem {
  value: string;
  label: string;
}

export function Tabs({ tabs, value, onChange }: { tabs: TabItem[]; value: string; onChange: (value: string) => void }) {
  return (
    <div style={{ display: 'inline-flex', flexWrap: 'wrap', background: 'var(--canvas)', borderRadius: 8, padding: 3, gap: 2 }}>
      {tabs.map((t) => (
        <button
          key={t.value}
          onClick={() => onChange(t.value)}
          style={{
            border: 'none',
            padding: '7px 16px',
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 600,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
            background: value === t.value ? 'var(--card)' : 'transparent',
            color: value === t.value ? 'var(--text)' : 'var(--text-muted)',
            boxShadow: value === t.value ? 'var(--shadow)' : 'none',
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

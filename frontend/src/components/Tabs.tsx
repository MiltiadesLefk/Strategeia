export interface TabItem {
  value: string;
  label: string;
}

/** Underlined tab row: the selected tab carries an accent line on the container's hairline. */
export function Tabs({ tabs, value, onChange }: { tabs: TabItem[]; value: string; onChange: (value: string) => void }) {
  return (
    <div role="tablist" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, borderBottom: '1px solid var(--border)' }}>
      {tabs.map((t) => {
        const active = value === t.value;
        return (
          <button
            key={t.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.value)}
            style={{
              border: 'none',
              borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
              marginBottom: -1,
              padding: '8px 12px',
              borderRadius: 0,
              fontSize: 13,
              fontWeight: 500,
              whiteSpace: 'nowrap',
              background: 'transparent',
              color: active ? 'var(--text)' : 'var(--text-muted)',
              transition: 'color 120ms ease, border-color 120ms ease',
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

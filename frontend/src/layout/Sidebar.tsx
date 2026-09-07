import { NavLink } from 'react-router-dom';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/scan', label: 'Market Scan' },
  { to: '/analysis', label: 'Analysis' },
  { to: '/research', label: 'Research' },
  { to: '/risk', label: 'Risk Manager' },
  { to: '/trade-plans', label: 'Trade Plans' },
  { to: '/portfolio', label: 'Portfolio' },
  { to: '/settings', label: 'Settings' },
];

export function Sidebar() {
  return (
    <aside
      style={{
        width: 220,
        flexShrink: 0,
        background: 'var(--navy)',
        color: 'var(--text-inverse)',
        display: 'flex',
        flexDirection: 'column',
        padding: '20px 12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 8px 24px' }}>
        <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.01em' }}>Strategeia</span>
      </div>
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1 }}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            style={({ isActive }) => ({
              padding: '9px 12px',
              borderRadius: 8,
              fontSize: 14,
              fontWeight: 500,
              color: isActive ? 'var(--text-inverse)' : 'var(--text-inverse-muted)',
              background: isActive ? 'rgba(255,255,255,0.08)' : 'transparent',
              textDecoration: 'none',
            })}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          padding: '10px 12px',
          marginTop: 12,
          fontSize: 13,
          fontWeight: 600,
          color: 'var(--green)',
          background: 'var(--green-bg)',
          border: '1px solid rgba(16, 185, 129, 0.3)',
          borderRadius: 9999,
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
        Trading Bot Online
      </div>
      {/* Required attribution for Elbstream's free ticker-logo API (CompanyIcon.tsx) */}
      <a
        href="https://elbstream.com/logos"
        target="_blank"
        rel="noreferrer"
        className="text-muted"
        style={{ fontSize: 12, textAlign: 'center', marginTop: 10 }}
      >
        Logos by Elbstream
      </a>
    </aside>
  );
}

import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useSettingsStatus } from '../api/hooks';
import type { SettingsStatus } from '../api/types';
import logo from '../assets/logo.png';

function Icon({ path }: { path: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

const ICONS = {
  dashboard: 'M3 13h8V3H3v10Zm10 8h8V3h-8v18ZM3 21h8v-6H3v6Z',
  scan: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35',
  analysis: 'M3 3v18h18M7 15l4-5 3 3 5-7',
  plans: 'M9 2h6l3 3v17H6V5l3-3Zm0 0v4h6V2M9 12h6M9 16h6',
  portfolio: 'M3 8h18v12H3V8Zm4 0V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2M3 12h18',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.14-1.44l2.02-1.57-2-3.46-2.38.96a7.5 7.5 0 0 0-2.5-1.44L14 2h-4l-.4 2.55a7.5 7.5 0 0 0-2.5 1.44l-2.38-.96-2 3.46 2.02 1.57A7.4 7.4 0 0 0 4.6 12c0 .49.05.97.14 1.44l-2.02 1.57 2 3.46 2.38-.96c.73.62 1.58 1.11 2.5 1.44L10 22h4l.4-2.55a7.5 7.5 0 0 0 2.5-1.44l2.38.96 2-3.46-2.02-1.57c.09-.47.14-.95.14-1.44Z',
};

const NAV_ITEMS: { to: string; label: string; end?: boolean; icon: keyof typeof ICONS }[] = [
  { to: '/', label: 'Dashboard', end: true, icon: 'dashboard' },
  { to: '/scan', label: 'Market Scan', icon: 'scan' },
  { to: '/analysis', label: 'Analysis', icon: 'analysis' },
  { to: '/trade-plans', label: 'Trade Plans', icon: 'plans' },
  { to: '/portfolio', label: 'Portfolio', icon: 'portfolio' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
];

/**
 * Six stacked pills ate a third of the sidebar to tell you, almost always,
 * that everything is fine. This shows one summary row by default and expands
 * to the detail — and auto-expands when something is actually offline, which
 * is the only time the detail earns the space.
 *
 * "Offline" here is only counted for services that are configured at all:
 * Finnhub and Telegram are optional, and an unconfigured optional service is
 * not a fault to shout about.
 */
function ServiceStatus({ status }: { status?: SettingsStatus }) {
  const [expanded, setExpanded] = useState(false);

  const services = [
    { key: 'ai', online: !!status?.ai_online, label: status?.ai_online ? `AI · ${status.ai_provider}` : 'AI offline', optional: false },
    { key: 'overlay', online: !!status?.ai_overlay_online, label: status?.ai_overlay_online ? 'AI overlay on' : 'AI overlay off', optional: true },
    { key: 'finnhub', online: !!status?.finnhub_online, label: status?.finnhub_online ? 'Finnhub online' : 'Finnhub off', optional: true },
    { key: 'telegram', online: !!status?.telegram_online, label: status?.telegram_online ? 'Telegram online' : 'Telegram off', optional: true },
  ];
  const faults = services.filter((s) => !s.optional && !s.online);
  const show = expanded || faults.length > 0;

  return (
    <div style={{ marginTop: 12 }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={show}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          width: '100%',
          padding: '8px 12px',
          fontSize: 12,
          fontWeight: 600,
          borderRadius: 9999,
          border: `1px solid ${faults.length ? 'rgba(239, 68, 68, 0.35)' : 'var(--border)'}`,
          background: faults.length ? 'var(--red-bg)' : 'rgba(255,255,255,0.04)',
          color: faults.length ? 'var(--red)' : 'var(--text-inverse-muted)',
        }}
      >
        <span style={{ display: 'flex', gap: 3 }}>
          {services.map((s) => (
            <span
              key={s.key}
              title={s.label}
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                display: 'inline-block',
                background: s.online ? 'var(--green)' : s.optional ? 'var(--text-inverse-muted)' : 'var(--red)',
              }}
            />
          ))}
        </span>
        {faults.length ? `${faults.length} service down` : 'Services'}
      </button>
      {show && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
          {services.map((s) => (
            <StatusPill key={s.key} online={s.online} label={s.label} />
          ))}
        </div>
      )}
    </div>
  );
}

function StatusPill({ online, label }: { online: boolean; label: string }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        padding: '8px 12px',
        fontSize: 12,
        fontWeight: 600,
        color: online ? 'var(--green)' : 'var(--text-inverse-muted)',
        background: online ? 'var(--green-bg)' : 'rgba(255,255,255,0.04)',
        border: `1px solid ${online ? 'rgba(16, 185, 129, 0.3)' : 'var(--border)'}`,
        borderRadius: 9999,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: online ? 'var(--green)' : 'var(--text-inverse-muted)', display: 'inline-block' }} />
      {label}
    </div>
  );
}

export function Sidebar({ mobileOpen = false, onClose }: { mobileOpen?: boolean; onClose?: () => void }) {
  const { data: status } = useSettingsStatus();
  return (
    <aside
      className={`sidebar${mobileOpen ? ' open' : ''}`}
      style={{
        width: 232,
        flexShrink: 0,
        background: 'var(--navy)',
        color: 'var(--text-inverse)',
        display: 'flex',
        flexDirection: 'column',
        padding: '20px 14px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 6px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src={logo} alt="Strategeia" style={{ width: 32, height: 32, flexShrink: 0, objectFit: 'contain' }} />
          <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.01em' }}>Strategeia</span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close menu"
          className="mobile-only-close"
          style={{ display: 'none', background: 'none', border: 'none', color: 'var(--text-inverse-muted)', padding: 4 }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1 }}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onClose}
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '9px 12px',
              borderRadius: 8,
              fontSize: 14,
              fontWeight: 500,
              color: isActive ? 'var(--text-inverse)' : 'var(--text-inverse-muted)',
              background: isActive ? 'rgba(255,255,255,0.08)' : 'transparent',
              textDecoration: 'none',
            })}
          >
            <Icon path={ICONS[item.icon]} />
            {item.label}
          </NavLink>
        ))}
      </nav>
      {/* The old "Trading Bot Online" pill lived here: unconditional green
          markup with no state behind it, so it read "Online" with the backend
          on fire. In a project whose whole claim is that nothing is hardcoded
          to look good, a decorative status light is the one thing that can't
          stay. The four pills below are real readings of
          GET /api/settings/status — they're the honest version of it, and they
          collapse into one summary row unless something actually needs
          attention. */}
      <ServiceStatus status={status} />
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

import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useLogout, useSettingsStatus } from '../api/hooks';
import type { SettingsStatus } from '../api/types';
import logo from '../assets/logo.png';
import { paletteShortcutLabel } from '../lib/paletteShortcut';
import { prefetchRoute } from '../routePages';

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
  screener: 'M3 5h18l-7 8v6l-4 2v-8L3 5Z',
  analysis: 'M3 3v18h18M7 15l4-5 3 3 5-7',
  calendar: 'M4 5h16v15H4V5Zm0 5h16M8 3v4m8-4v4',
  plans: 'M9 2h6l3 3v17H6V5l3-3Zm0 0v4h6V2M9 12h6M9 16h6',
  portfolio: 'M3 8h18v12H3V8Zm4 0V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2M3 12h18',
  forecast: 'M3 20h18M6 16V9m4 7V5m4 11v-6m4 6V8',
  backtest: 'M3 3v18h18M7 14l3-3 3 2 5-6M7 18h2m3 0h2m3 0h2',
  committee: 'M16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm0 2c-2.7 0-5 1.3-5 3v3h10v-3c0-1.7-2.3-3-5-3Zm8 0c-.4 0-.8 0-1.2.1 1.2.8 2.2 1.8 2.2 2.9v3h6v-3c0-1.7-2.3-3-5-3Z',
  terminal: 'M3 3h7v7H3V3Zm11 0h7v4h-7V3ZM3 14h7v7H3v-7Zm11-3h7v10h-7V11Z',
  smartMoney: 'M12 2v20M17 6.5C17 4.6 14.8 3.5 12 3.5S7 4.6 7 6.5 9 9.4 12 10s5 1.4 5 3.5-2.2 3-5 3-5-1.1-5-3',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.14-1.44l2.02-1.57-2-3.46-2.38.96a7.5 7.5 0 0 0-2.5-1.44L14 2h-4l-.4 2.55a7.5 7.5 0 0 0-2.5 1.44l-2.38-.96-2 3.46 2.02 1.57A7.4 7.4 0 0 0 4.6 12c0 .49.05.97.14 1.44l-2.02 1.57 2 3.46 2.38-.96c.73.62 1.58 1.11 2.5 1.44L10 22h4l.4-2.55a7.5 7.5 0 0 0 2.5-1.44l2.38.96 2-3.46-2.02-1.57c.09-.47.14-.95.14-1.44Z',
};

const NAV_ITEMS: { to: string; label: string; end?: boolean; icon: keyof typeof ICONS }[] = [
  { to: '/', label: 'Dashboard', end: true, icon: 'dashboard' },
  { to: '/scan', label: 'Market Scan', icon: 'scan' },
  { to: '/screener', label: 'Screener', icon: 'screener' },
  { to: '/analysis', label: 'Analysis', icon: 'analysis' },
  { to: '/calendar', label: 'Calendar', icon: 'calendar' },
  { to: '/trade-plans', label: 'Trade Plans', icon: 'plans' },
  { to: '/portfolio', label: 'Portfolio', icon: 'portfolio' },
  { to: '/terminal', label: 'Market Terminal', icon: 'terminal' },
  { to: '/smart-money', label: 'Smart Money', icon: 'smartMoney' },
  { to: '/committee', label: 'AI Committee', icon: 'committee' },
  { to: '/backtests', label: 'Backtest Lab', icon: 'backtest' },
  { to: '/forecast-lab', label: 'Forecast Lab', icon: 'forecast' },
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
    { key: 'ai', online: !!status?.ai_online, label: status?.ai_online ? `AI · ${status.ai_provider}${status.ai_model ? ` (${status.ai_model})` : ''}` : 'AI offline', optional: false },
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

export function Sidebar({ mobileOpen = false, onClose, onOpenPalette }: { mobileOpen?: boolean; onClose?: () => void; onOpenPalette?: () => void }) {
  const { data: status } = useSettingsStatus();
  const { mutate: logout, isPending: loggingOut } = useLogout();
  const renderItem = (item: (typeof NAV_ITEMS)[number]) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.end}
      onClick={onClose}
      onMouseEnter={() => prefetchRoute(item.to)}
      onFocus={() => prefetchRoute(item.to)}
      className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
    >
      <Icon path={ICONS[item.icon]} />
      {item.label}
    </NavLink>
  );
  return (
    <aside
      className={`sidebar${mobileOpen ? ' open' : ''}`}
      style={{
        width: 240,
        flexShrink: 0,
        color: 'var(--text-inverse)',
        display: 'flex',
        flexDirection: 'column',
        padding: '20px 12px 14px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 6px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src={logo} alt="Strategeia" style={{ width: 32, height: 32, flexShrink: 0, objectFit: 'contain' }} />
          <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.01em' }}>Strategeia</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <button
            onClick={() => logout()}
            disabled={loggingOut}
            aria-label="Log out"
            title="Log out"
            style={{ background: 'none', border: 'none', color: 'var(--text-inverse-muted)', padding: 4, cursor: 'pointer' }}
          >
            <Icon path="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
          </button>
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
      </div>
      {onOpenPalette && (
        <button
          type="button"
          className="sidebar-search"
          onClick={() => {
            onClose?.();
            onOpenPalette();
          }}
          aria-label="Search symbols and pages"
          aria-keyshortcuts="Control+K Meta+K"
        >
          <Icon path={ICONS.scan} />
          Search
          <kbd>{paletteShortcutLabel()}</kbd>
        </button>
      )}
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1 }}>
        <div className="sidebar-section">MENU</div>
        {NAV_ITEMS.filter((i) => i.to !== '/settings').map(renderItem)}
        <div className="sidebar-section">SYSTEM</div>
        {NAV_ITEMS.filter((i) => i.to === '/settings').map(renderItem)}
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
      <div className="sidebar-user">
        <div className="sidebar-user-avatar">S</div>
        <div style={{ lineHeight: 1.25, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Operator</div>
          <div className="text-muted" style={{ fontSize: 11 }}>Paper trading</div>
        </div>
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

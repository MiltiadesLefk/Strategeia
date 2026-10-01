import { useCallback, useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { CommandPalette } from '../components/CommandPalette';
import { RouteBoundary } from '../components/RouteBoundary';
import { usePaletteShortcut } from '../lib/paletteShortcut';
import { prefetchAllWhenIdle } from '../routePages';
import { Sidebar } from './Sidebar';

export function DashboardLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const togglePalette = useCallback(() => setPaletteOpen((v) => !v), []);
  usePaletteShortcut(togglePalette);

  // Only mounts once signed in, so the login screen never downloads the pages.
  useEffect(() => {
    prefetchAllWhenIdle();
  }, []);

  return (
    <div className="app-shell">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} onOpenPalette={() => setPaletteOpen(true)} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      {mobileOpen && <div className="sidebar-scrim" onClick={() => setMobileOpen(false)} />}
      <div className="app-main-col">
        <div className="mobile-menu-btn" style={{ justifyContent: 'space-between' }}>
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setMobileOpen(true)}
            style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'none', border: 'none', color: 'inherit', padding: 0, font: 'inherit' }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
            <span style={{ fontWeight: 700, fontSize: 15 }}>Strategeia</span>
          </button>
          <button
            type="button"
            aria-label="Search symbols and pages"
            onClick={() => setPaletteOpen(true)}
            style={{ display: 'flex', background: 'none', border: 'none', color: 'inherit', padding: 6 }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35" />
            </svg>
          </button>
        </div>
        <main className="app-main">
          <RouteBoundary>
            <Outlet />
          </RouteBoundary>
        </main>
      </div>
    </div>
  );
}

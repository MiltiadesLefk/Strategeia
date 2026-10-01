import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { RouteBoundary } from '../components/RouteBoundary';
import { prefetchAllWhenIdle } from '../routePages';
import { Sidebar } from './Sidebar';

export function DashboardLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);

  // Only mounts once signed in, so the login screen never downloads the pages.
  useEffect(() => {
    prefetchAllWhenIdle();
  }, []);

  return (
    <div className="app-shell">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      {mobileOpen && <div className="sidebar-scrim" onClick={() => setMobileOpen(false)} />}
      <div className="app-main-col">
        <button className="mobile-menu-btn" aria-label="Open menu" onClick={() => setMobileOpen(true)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
          <span style={{ fontWeight: 700, fontSize: 15 }}>Strategeia</span>
        </button>
        <main className="app-main">
          <RouteBoundary>
            <Outlet />
          </RouteBoundary>
        </main>
      </div>
    </div>
  );
}

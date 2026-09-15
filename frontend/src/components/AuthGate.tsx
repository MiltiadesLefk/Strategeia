import type { ReactNode } from 'react';
import { useAuthStatus } from '../api/hooks';
import { LoginPage } from '../pages/LoginPage';
import { LoadingSpinner } from './common';

/** Gates the entire app behind GET /api/auth/status — one user, one
 * password, no signup (see backend/app/api/routers/auth.py). Reports
 * authenticated=true unconditionally while the backend's
 * ALLOW_UNAUTHENTICATED_API opt-out is on, so local/LAN dev never sees a
 * login screen guarding nothing.
 *
 * Deliberately blocks on the FIRST load (a spinner, not the app, while
 * status is unknown) rather than optimistically rendering the dashboard —
 * every page under this immediately fires its own authenticated API
 * calls, which would otherwise all 401 in the gap before the first status
 * check resolves. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { data, isLoading } = useAuthStatus();

  if (isLoading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <LoadingSpinner />
      </div>
    );
  }

  if (!data?.authenticated) {
    return <LoginPage />;
  }

  return <>{children}</>;
}

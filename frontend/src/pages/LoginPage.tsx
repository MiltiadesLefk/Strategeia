import { useState } from 'react';
import { useLogin } from '../api/hooks';
import type { ApiError } from '../api/client';
import logo from '../assets/logo.png';

/** The one-user login screen AuthGate shows whenever /api/auth/status
 * reports not authenticated. No signup, no "forgot password" flow — by
 * explicit design there is exactly one account, and its username/password
 * live in the backend operator's own config (see backend/app/config.py's
 * auth_username/auth_password, AUTH_USERNAME/AUTH_PASSWORD). A 429 here
 * means the backend's login lockout tripped (see app/auth.py's
 * LoginAttemptTracker) — shown as-is rather than translated, since its
 * message already says how long to wait. */
export function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const { mutate: login, isPending, error } = useLogin();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username || !password || isPending) return;
    login({ username, password });
  }

  const apiError = error as ApiError | null;
  const isLockedOut = apiError?.status === 429;

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
        background: 'var(--canvas)',
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="card"
        style={{ width: '100%', maxWidth: 360, display: 'flex', flexDirection: 'column', gap: 16 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <img src={logo} alt="" width={40} height={40} />
          <div style={{ fontWeight: 700, fontSize: 18 }}>Strategeia</div>
          <div className="text-muted" style={{ fontSize: 13 }}>
            Sign in to continue
          </div>
        </div>

        <div>
          <label htmlFor="login-username">Username</label>
          <input
            id="login-username"
            type="text"
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={isLockedOut}
          />
        </div>

        <div>
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={isLockedOut}
          />
        </div>

        {apiError && (
          <div className="text-red" style={{ fontSize: 13 }}>
            {isLockedOut ? apiError.message : 'Incorrect username or password.'}
          </div>
        )}

        <button type="submit" className="btn btn-primary" disabled={!username || !password || isPending || isLockedOut}>
          {isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

import { useState } from 'react';
import { useLogin } from '../api/hooks';
import type { ApiError } from '../api/client';
import logo from '../assets/logo.png';
import './login.css';

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
  const [showPw, setShowPw] = useState(false);
  const { mutate: login, isPending, error } = useLogin();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username || !password || isPending) return;
    login({ username, password });
  }

  const apiError = error as ApiError | null;
  const isLockedOut = apiError?.status === 429;

  return (
    <div className="login-page">
      <div className="card login-card">
        <div className={`login-bar${isLockedOut ? ' login-bar-locked' : apiError ? ' login-bar-error' : ''}`} />
        <form onSubmit={handleSubmit} className="login-form">
        <div className="login-head">
          <img src={logo} alt="" width={48} height={48} />
          <h1 className="login-title">Strategeia</h1>
          <div className="login-sub">Analyst Workstation</div>
        </div>

        {apiError && (
          <div className={`login-banner${isLockedOut ? ' login-banner-locked' : ''}`} role="alert">
            {isLockedOut ? apiError.message : 'Incorrect username or password'}
          </div>
        )}

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
          <div className="login-pw">
            <input
              id="login-password"
              type={showPw ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isLockedOut}
              className={apiError && !isLockedOut ? 'login-invalid' : undefined}
            />
            <button
              type="button"
              className="login-toggle"
              aria-pressed={showPw}
              aria-label="Show password"
              onClick={() => setShowPw((v) => !v)}
              disabled={isLockedOut}
            >
              {showPw ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>

        <button type="submit" className="btn btn-primary login-submit" disabled={!username || !password || isPending || isLockedOut}>
          {isPending ? 'Logging in…' : isLockedOut ? 'Access restricted' : 'Log in'}
        </button>

        <div className="login-foot">Paper trading only. No real money.</div>
        </form>
      </div>
    </div>
  );
}

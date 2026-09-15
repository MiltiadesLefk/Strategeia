const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
// Only relevant if the backend's matching InfraSettings.api_shared_secret
// (API_SHARED_SECRET in backend/.env) is set — see api/deps.py's
// require_auth. Increasingly a secondary path: logging in via
// AuthGate/useLogin gets a session cookie that authenticates every request
// below on its own (credentials: 'include'), with no build-time secret
// needed at all. This stays for anyone still relying on the older baked-in
// key (scripts, Swagger UI, a frontend built before login existed).
const API_SHARED_SECRET = import.meta.env.VITE_API_SHARED_SECRET || '';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    // The session cookie AuthGate's login sets is what actually
    // authenticates the SPA now (see api/routers/auth.py) — without this,
    // the browser never sends it back, and every request would fall
    // through to needing API_SHARED_SECRET again regardless of being
    // logged in.
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(API_SHARED_SECRET ? { 'X-API-Key': API_SHARED_SECRET } : {}),
      ...options?.headers,
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // response wasn't JSON; fall back to statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
};

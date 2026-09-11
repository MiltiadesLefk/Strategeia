const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
// Only relevant if the backend's matching InfraSettings.api_shared_secret
// (API_SHARED_SECRET in backend/.env) is set — see api/deps.py's
// require_shared_secret. Both unset (the default) is a no-op end to end;
// setting one without the other locks this frontend out with a 401.
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

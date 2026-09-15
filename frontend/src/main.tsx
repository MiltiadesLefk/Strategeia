import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { qk } from './api/hooks';
import { ApiError } from './api/client';
import './index.css';

// A 401 from ANY query — a session that expired mid-use (7 days by
// default, or the backend restarted with a fresh session_secret; see
// backend/app/auth.py) — immediately re-checks auth status instead of
// waiting for its own 60s poll (see useAuthStatus), so AuthGate flips back
// to the login screen right away rather than leaving the page up while
// every request underneath it quietly keeps failing. Query-side only:
// mutations (useLogin itself included) surface their own error to the
// component that triggered them instead, which is what the login form's
// error message and the lockout display actually need.
function handleQueryError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    queryClient.invalidateQueries({ queryKey: qk.authStatus });
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 15_000 },
  },
  queryCache: new QueryCache({ onError: handleQueryError }),
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);

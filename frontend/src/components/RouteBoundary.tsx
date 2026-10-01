import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { LoadingSpinner } from './common';

/** True for the errors a browser throws when a lazily-loaded page chunk can't
 * be fetched. After a redeploy the old hashed file names no longer exist on the
 * server, so a tab opened before the update fails exactly this way the next
 * time it navigates; the wording differs per browser. */
function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Loading chunk .* failed|ChunkLoadError/i.test(
    message,
  );
}

interface BoundaryProps {
  children: ReactNode;
  /** Changing this value clears a previous error, so moving to another route
   * gets a fresh attempt instead of staying stuck on the failure screen. */
  resetKey: string;
}

interface BoundaryState {
  error: Error | null;
  resetKey: string;
}

class ErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<BoundaryState> {
    return { error };
  }

  static getDerivedStateFromProps(props: BoundaryProps, state: BoundaryState): Partial<BoundaryState> | null {
    return props.resetKey === state.resetKey ? null : { error: null, resetKey: props.resetKey };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Route failed to render:', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const stale = isChunkLoadError(error);
    return (
      <div className="card" role="alert" style={{ maxWidth: 520, margin: '48px auto', textAlign: 'center' }}>
        <h2 style={{ marginTop: 0, marginBottom: 8 }}>{stale ? 'The app was updated' : 'Something went wrong'}</h2>
        <p className="text-muted" style={{ margin: '0 0 16px' }}>
          {stale
            ? 'A newer version of Strategeia is available and this page is out of date. Reload to continue.'
            : 'This page failed to display. Reloading usually fixes it.'}
        </p>
        <button type="button" className="btn btn-secondary" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}

/** Wraps the layout's <Outlet />: pages are lazy-loaded, so this shows a
 * loading state while a page's code downloads and a reload prompt if that
 * download fails. The loading text is a live region so screen readers hear it. */
export function RouteBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return (
    <ErrorBoundary resetKey={pathname}>
      <Suspense
        fallback={
          <div role="status" aria-live="polite">
            <LoadingSpinner />
          </div>
        }
      >
        {children}
      </Suspense>
    </ErrorBoundary>
  );
}

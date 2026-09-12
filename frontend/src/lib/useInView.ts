import { useEffect, useRef, useState } from 'react';

/**
 * True once the element has scrolled into view — and stays true afterwards.
 *
 * Used to defer per-card data fetching on Portfolio, so N open positions don't
 * fire N simultaneous one-year analysis requests on page load for charts that
 * are mostly below the fold.
 *
 * Falls back to true when IntersectionObserver is unavailable, so content
 * always loads rather than silently staying blank.
 */
export function useInView<T extends HTMLElement>(rootMargin = '200px') {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (inView) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setInView(true);
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [inView, rootMargin]);

  return { ref, inView };
}

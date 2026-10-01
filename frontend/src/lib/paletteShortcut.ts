import { useEffect } from 'react';

/** True for Ctrl+K and, on Mac, Cmd+K. Shift/Alt variants are left to the browser and other tools. */
export function isPaletteShortcut(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k';
}

/** "⌘K" on Apple platforms, "Ctrl K" elsewhere, for the hint next to the search button. */
export function paletteShortcutLabel(): string {
  const apple = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent);
  return apple ? '⌘K' : 'Ctrl K';
}

/** Calls `onToggle` on Ctrl/Cmd+K anywhere in the app, including while typing in a field. */
export function usePaletteShortcut(onToggle: () => void): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!isPaletteShortcut(e)) return;
      // Chrome binds Ctrl+K to "search from the address bar"; claim it while the app has focus.
      e.preventDefault();
      onToggle();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onToggle]);
}

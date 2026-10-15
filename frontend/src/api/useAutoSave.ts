import { useEffect, useRef } from 'react';

/** Saves a settings card on its own: `delayMs` after the last change, once the values differ from what is saved
 * (`dirty`) and are acceptable (`valid`). `changeKey` identifies the current values so typing restarts the wait.
 * A failed save leaves the card dirty and does not retry until something changes again. */
export function useAutoSave(changeKey: string, dirty: boolean, valid: boolean, save: () => void, delayMs = 600) {
  const latest = useRef(save);
  useEffect(() => {
    latest.current = save;
  });
  useEffect(() => {
    if (!dirty || !valid) return;
    const timer = window.setTimeout(() => latest.current(), delayMs);
    return () => window.clearTimeout(timer);
  }, [changeKey, dirty, valid, delayMs]);
}

/*
 * MIT License
 *
 * Copyright (c) 2026 OpenTerminal contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 * Adapted from OpenTerminal@95618ee web/components/Flash.tsx; changes: the
 * hook reports the direction of the change (up/down) instead of a boolean so
 * the cell can flash green or red; a `scope` argument re-baselines when the
 * value switches to a different subject (another symbol), so that does not
 * count as a price move; the change test lives in lib/flash.ts and is
 * unit-tested; an alternating counter lets back-to-back changes replay the
 * animation.
 */

import { useEffect, useRef, useState } from 'react';
import { FLASH_MS, flashDirection, type FlashDirection } from './flash';

export interface FlashState {
  direction: FlashDirection;
  /** Increments on every flash so consecutive changes can replay the CSS animation. */
  seq: number;
}

/**
 * The flash currently showing for `value`, or null. Becomes non-null for
 * FLASH_MS whenever `value` changes from its previous render to a different
 * number; never on the first value, and never when `scope` changed (the value
 * then belongs to a different subject, e.g. another symbol's price).
 */
export function useFlash(value: number | null | undefined, scope?: string): FlashState | null {
  const prev = useRef<{ value: number | null | undefined; scope: string | undefined }>({ value, scope });
  const seq = useRef(0);
  const [state, setState] = useState<FlashState | null>(null);

  useEffect(() => {
    const last = prev.current;
    prev.current = { value, scope };
    const direction = last.scope === scope ? flashDirection(last.value, value) : null;
    if (!direction) {
      setState(null);
      return;
    }
    seq.current += 1;
    setState({ direction, seq: seq.current });
    const timer = setTimeout(() => setState(null), FLASH_MS);
    return () => clearTimeout(timer);
  }, [value, scope]);

  return state;
}

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
 * Adapted from OpenTerminal@95618ee web/components/Flash.tsx; changes: flashes
 * green when the value rose and red when it fell (OpenTerminal flashes white),
 * styled with this app's colour variables, honours prefers-reduced-motion
 * (see the .flash-* rules in index.css), and takes a `scope` so switching to a
 * different symbol is not shown as a price move.
 */

import type { ReactNode } from 'react';
import { useFlash } from '../lib/useFlash';

/**
 * Wraps a displayed number in a span that briefly flashes green (it went up)
 * or red (it went down) when `value` changes between renders, e.g. when a
 * background refetch brings a new price. Not on first mount and not when
 * `scope` changes. The wrapped text is whatever the caller formats; only
 * `value` drives the flash.
 */
export function Flash({ value, scope, children }: { value: number | null | undefined; scope?: string; children: ReactNode }) {
  const flash = useFlash(value, scope);
  if (!flash) return <span className="flash">{children}</span>;
  // Alternate between two identical animations so a second change while the
  // first is still fading restarts the effect instead of being swallowed.
  return (
    <span className={`flash flash-${flash.direction} flash-${flash.seq % 2 === 0 ? 'a' : 'b'}`} data-flash={flash.direction}>
      {children}
    </span>
  );
}

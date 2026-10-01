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
 * Adapted from OpenTerminal@95618ee web/components/CommandPalette.tsx; changes:
 * searches this app's own symbol universe in the browser (fuzzy scoring in
 * lib/fuzzy.ts) instead of calling a /api/search endpoint; adds page and
 * "generate trade plan" rows, recent symbols, a focus trap, combobox/listbox
 * accessibility roles and a layout that works on a phone; navigates with
 * react-router instead of a global store; styled with this app's CSS.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUniverse } from '../api/hooks';
import { buildIndex } from '../lib/fuzzy';
import { buildPaletteItems, type PaletteItem } from '../lib/paletteItems';
import { pushRecent, readRecents, writeRecents } from '../lib/recentSymbols';

/**
 * The Ctrl/Cmd+K palette: type a ticker, company or sector to jump to its
 * Analysis page, or a page name to go there. Enter opens the highlighted row,
 * Shift+Enter on a symbol opens its Trade Plans page instead, Esc closes.
 *
 * Mounted only while open, so every open starts with an empty query and the
 * first row highlighted without any reset logic, and nothing runs while it is
 * closed. The universe is the same cached list the pickers use.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  // Called while closed too, so the universe is already cached by the first Ctrl+K.
  useUniverse();
  if (!open) return null;
  return <PaletteDialog onClose={onClose} />;
}

function PaletteDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const { data: universe, isLoading } = useUniverse();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  // Read once per open: the recents only change by choosing a row, which closes the palette.
  const [recents] = useState(readRecents);
  const inputRef = useRef<HTMLInputElement>(null);
  const uid = useId();
  const listId = `${uid}-list`;
  const optionId = (i: number) => `${uid}-opt-${i}`;

  // Lower-casing 500 entries once per universe load, not once per keystroke.
  const index = useMemo(() => buildIndex(universe ?? []), [universe]);
  const items = useMemo(() => buildPaletteItems(index, query, recents), [index, query, recents]);
  const active = Math.min(selected, Math.max(items.length - 1, 0));

  // Focus the field, and give focus back to whatever had it (the sidebar button, the page) on close.
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    inputRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  useEffect(() => {
    document.getElementById(`${uid}-opt-${active}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [uid, active, items]);

  function choose(item: PaletteItem, shift = false) {
    const to = shift && item.shiftTo ? item.shiftTo : item.to;
    if (item.symbol) writeRecents(pushRecent(recents, item.symbol));
    onClose();
    navigate(to);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        e.stopPropagation();
        onClose();
        break;
      case 'ArrowDown':
        e.preventDefault();
        if (items.length) setSelected((active + 1) % items.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (items.length) setSelected((active - 1 + items.length) % items.length);
        break;
      case 'Enter':
        e.preventDefault();
        if (items[active]) choose(items[active], e.shiftKey);
        break;
      case 'Tab':
        // Focus trap: the field is the only tab stop in the dialog, so Tab
        // would otherwise walk out of it into the page behind the overlay.
        e.preventDefault();
        break;
    }
  }

  const hasQuery = query.trim().length > 0;

  return (
    <div className="palette-overlay" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(e) => {
          e.stopPropagation();
          // Clicking padding or a row must not pull focus off the field.
          if (e.target !== inputRef.current) e.preventDefault();
        }}
        onKeyDown={onKeyDown}
      >
        <input
          ref={inputRef}
          type="text"
          className="palette-input"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          placeholder="Search a ticker, company or page…"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={items.length ? optionId(active) : undefined}
          aria-label="Search symbols and pages"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
        />
        <div className="palette-list" role="listbox" id={listId} aria-label="Results">
          {items.map((item, i) => {
            // Rows of one section are contiguous, so a header goes above the first of each run.
            const header = i === 0 || items[i - 1].section !== item.section ? item.section : null;
            return (
              <div key={item.id} role="presentation">
                {header && (
                  <div className="palette-section" aria-hidden="true">
                    {header}
                  </div>
                )}
                <div
                  id={optionId(i)}
                  role="option"
                  aria-selected={i === active}
                  className="palette-row"
                  onMouseMove={() => i !== active && setSelected(i)}
                  onClick={() => choose(item)}
                >
                  <span className={item.id.startsWith('symbol:') ? 'palette-ticker' : 'palette-label'}>{item.label}</span>
                  <span className="palette-detail">{item.detail}</span>
                </div>
              </div>
            );
          })}
          {items.length === 0 && (
            <div className="palette-empty">
              {isLoading && hasQuery ? 'Loading symbols…' : hasQuery ? `No symbol or page matches “${query.trim()}”` : 'Nothing to show yet'}
            </div>
          )}
        </div>
        <div className="palette-footer" aria-hidden="true">
          <span>↑↓ move</span>
          <span>↵ open</span>
          <span className="palette-footer-optional">⇧↵ trade plan</span>
          <span>esc close</span>
        </div>
        <div className="sr-only" role="status">
          {hasQuery ? `${items.length} result${items.length === 1 ? '' : 's'}` : ''}
        </div>
      </div>
    </div>
  );
}

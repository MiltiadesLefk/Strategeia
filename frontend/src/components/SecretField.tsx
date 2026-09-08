import { useState } from 'react';

/**
 * A saved secret (API key/token) never comes back from the backend as its
 * real value — only a fixed-length masked hint like "••••••••••••••••ab12"
 * (see AppSettings.redacted() / _mask_secret in config.py). This renders
 * that hint as a persistent, filled-looking field (not a placeholder that
 * fades on focus/refresh) with a "Replace" action to swap in a new value —
 * there's nothing real to "reveal" or "copy" since the backend never sends
 * the actual key back, by design.
 *
 * The caller should pass `key={masked}` when rendering this (see
 * SettingsPage.tsx) — `masked` changing after a successful save is what
 * forces a clean remount back to display mode. Deriving that purely from
 * props without a remount doesn't work: "just saved" and "clicked Replace,
 * haven't typed yet" both look identical (masked set, draft value empty).
 */
export function SecretField({
  label,
  masked,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  /** Masked hint from settings (e.g. "••••••••••••••••ab12"), "" if never set. */
  masked: string;
  /** Draft value being typed to replace the stored secret. */
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const [editing, setEditing] = useState(!masked);

  return (
    <div>
      <label>{label}</label>
      {editing ? (
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            autoComplete="off"
            style={{ flex: 1 }}
          />
          {masked && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                onChange('');
                setEditing(false);
              }}
            >
              Cancel
            </button>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 8 }}>
          <div
            className="tabular-nums"
            style={{
              flex: 1,
              padding: '8px 10px',
              border: '1px solid var(--border)',
              borderRadius: 8,
              background: 'var(--card-alt)',
              color: 'var(--text-muted)',
              letterSpacing: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {masked}
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => setEditing(true)}>
            Replace
          </button>
        </div>
      )}
    </div>
  );
}

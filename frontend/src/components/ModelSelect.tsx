// A model picker: a dropdown of known models, plus "Other..." for any id not listed
// (a pinned older version, a model newer than this list). A saved value that is not in
// the list shows up as Other with its text, so nothing already saved is lost.
import { useState } from 'react';

export interface ModelPreset {
  value: string;
  label: string;
}

// Claude CLI: the three aliases follow the latest of each family; full ids pin an exact version.
export const CLAUDE_MODEL_PRESETS: ModelPreset[] = [
  { value: 'sonnet', label: 'Sonnet (latest)' },
  { value: 'opus', label: 'Opus (latest)' },
  { value: 'haiku', label: 'Haiku (latest)' },
  { value: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5' },
  { value: 'claude-opus-5-5', label: 'Claude Opus 5.5' },
  { value: 'claude-haiku-5-5', label: 'Claude Haiku 5.5' },
  { value: 'claude-fable-5-1', label: 'Claude Fable 5.1' },
];

const OTHER = '__other__';

interface Props {
  value: string;
  onChange: (value: string) => void;
  presets: ModelPreset[];
  /** What the empty value means, shown as the first option. */
  blankLabel: string;
  maxLength: number;
}

export function ModelSelect({ value, onChange, presets, blankLabel, maxLength }: Props) {
  const [other, setOther] = useState(false);
  const known = value === '' || presets.some((p) => p.value === value);
  const showInput = other || !known;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <select
        value={showInput ? OTHER : value}
        onChange={(e) => {
          if (e.target.value === OTHER) {
            setOther(true);
          } else {
            setOther(false);
            onChange(e.target.value);
          }
        }}
      >
        <option value="">{blankLabel}</option>
        {presets.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
        <option value={OTHER}>Other…</option>
      </select>
      {showInput && (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="model id"
          maxLength={maxLength}
          spellCheck={false}
          autoComplete="off"
        />
      )}
    </div>
  );
}

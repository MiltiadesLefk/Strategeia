import { useEffect, useState } from 'react';

export function SymbolPicker({ value, onChange }: { value: string; onChange: (symbol: string) => void }) {
  const [draft, setDraft] = useState(value);

  // `value` can change without this component unmounting (e.g. picked from
  // CompanyDropdown, or a Link changes ?symbol= while staying on the same
  // route) — useState(value) only seeds the initial render, so without this
  // the input silently shows a stale symbol while the page loads a new one.
  useEffect(() => setDraft(value), [value]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) onChange(draft.trim().toUpperCase());
      }}
      style={{ display: 'flex', gap: 8 }}
    >
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value.toUpperCase())}
        placeholder="Symbol, e.g. AAPL"
        style={{ width: 160 }}
      />
      <button type="submit" className="btn btn-secondary">
        Go
      </button>
    </form>
  );
}

import { useState } from 'react';

export function SymbolPicker({ value, onChange }: { value: string; onChange: (symbol: string) => void }) {
  const [draft, setDraft] = useState(value);

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

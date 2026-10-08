import { useUniverse } from '../api/hooks';

/**
 * A symbol filter as a dropdown: "All symbols" (an empty value) plus every symbol on the watchlist.
 * A symbol that is selected but not on the list (an old link, a changed watchlist) stays selectable
 * so the filter never silently changes under the user.
 */
export function SymbolFilterSelect({
  value,
  onChange,
  label = 'Symbol',
  width = 190,
}: {
  value: string;
  onChange: (symbol: string) => void;
  label?: string;
  width?: number;
}) {
  const { data: universe, isLoading } = useUniverse();
  const listed = universe?.some((e) => e.symbol === value) ?? false;
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} style={{ width }} disabled={isLoading}>
      <option value="">{isLoading ? 'Loading…' : 'All symbols'}</option>
      {value && !listed && <option value={value}>{value}</option>}
      {universe?.map((entry) => (
        <option key={entry.symbol} value={entry.symbol}>
          {entry.symbol} — {entry.name}
        </option>
      ))}
    </select>
  );
}

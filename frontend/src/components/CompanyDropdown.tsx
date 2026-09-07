import { useUniverse } from '../api/hooks';

export function CompanyDropdown({ value, onChange }: { value: string; onChange: (symbol: string) => void }) {
  const { data: universe, isLoading } = useUniverse();

  return (
    <select
      value={universe?.some((e) => e.symbol === value) ? value : ''}
      onChange={(e) => e.target.value && onChange(e.target.value)}
      style={{ width: 220 }}
      disabled={isLoading}
    >
      <option value="">{isLoading ? 'Loading…' : 'Choose a company…'}</option>
      {universe?.map((entry) => (
        <option key={entry.symbol} value={entry.symbol}>
          {entry.symbol} — {entry.name}
        </option>
      ))}
    </select>
  );
}

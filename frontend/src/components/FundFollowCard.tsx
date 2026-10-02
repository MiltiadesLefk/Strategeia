import { useEffect, useState } from 'react';
import { useFundsOverview, useSettings, useUpdateSettings } from '../api/hooks';

const MAX_FOLLOWED_FUNDS = 50;

/** "Who to follow" for 13F funds on the Settings page: the manager CIK numbers whose quarterly holdings the Funds
 *  tab loads and the fund watcher checks. Empty means the built-in starting list. Saves on its own button. */
export function FundFollowCard() {
  const { data: settings } = useSettings();
  const overview = useFundsOverview();
  const update = useUpdateSettings();
  const [ciks, setCiks] = useState<string[]>([]);
  const [typed, setTyped] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) setCiks(settings.smart_money_followed_funds);
  }, [settings]);

  const clean = typed.trim().replace(/^0+/, '');
  const valid = /^[0-9]{1,10}$/.test(clean);
  const full = ciks.length >= MAX_FOLLOWED_FUNDS;
  const nameOf = (cik: string) => overview.data?.funds.find((f) => f.cik === cik)?.name;

  function add() {
    if (!valid || full || ciks.includes(clean)) return;
    setCiks([...ciks, clean]);
    setTyped('');
  }

  function save() {
    update.mutate(
      { smart_money_followed_funds: ciks },
      {
        onSuccess: () => {
          setSaved(true);
          window.setTimeout(() => setSaved(false), 2000);
        },
      },
    );
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="fund-follow">
      <h3>Funds: who to follow</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        The fund watcher checks these managers for new 13F reports, and the Funds tab shows what they hold. A 13F lists long
        positions only, as of a quarter end, filed up to 45 days later. Add a manager by its SEC CIK number (find it on sec.gov by
        searching the fund&apos;s name). Leave the list empty to use the built-in starting list of six well-known funds.
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {ciks.length === 0 && (
          <span className="text-muted" style={{ fontSize: 13 }}>
            Using the built-in starting list.
          </span>
        )}
        {ciks.map((c) => (
          <span key={c} className="badge badge-neutral" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            {nameOf(c) ?? `CIK ${c}`}
            <button
              type="button"
              aria-label={`Stop following CIK ${c}`}
              onClick={() => setCiks(ciks.filter((x) => x !== c))}
              style={{ border: 'none', background: 'transparent', cursor: 'pointer', padding: 0, color: 'inherit' }}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div>
        <label>Add a fund by CIK</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            type="text"
            inputMode="numeric"
            value={typed}
            maxLength={10}
            placeholder="e.g. 1067983"
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') add();
            }}
          />
          <button className="btn btn-secondary" type="button" onClick={add} disabled={!valid || full}>
            Add
          </button>
        </div>
        {typed && !valid && (
          <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
            A CIK is a number of up to 10 digits.
          </div>
        )}
        {full && (
          <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
            At most {MAX_FOLLOWED_FUNDS} funds.
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn btn-secondary" onClick={save} disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
        {saved && (
          <span className="text-green" style={{ fontSize: 13 }}>
            Saved
          </span>
        )}
        {update.error && (
          <span className="text-red" style={{ fontSize: 13 }}>
            {update.error.message}
          </span>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { useCongressMemberNames, useSettings, useUpdateSettings } from '../api/hooks';
import type { CongressFollowMode } from '../api/types';

const MAX_FOLLOWED_MEMBERS = 50;
const MAX_NAME_LENGTH = 80;

/** "Who to follow" on the Settings page: which members of Congress the House watcher alerts on and the
 *  Congress tab can filter to. Saves on its own. */
export function CongressFollowCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [mode, setMode] = useState<CongressFollowMode>('all');
  const [members, setMembers] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [saved, setSaved] = useState(false);
  const names = useCongressMemberNames(query.trim());

  useEffect(() => {
    if (!settings) return;
    setMode(settings.smart_money_follow_congress);
    setMembers(settings.smart_money_followed_members);
  }, [settings]);

  const has = (name: string) => members.some((m) => m.toLowerCase() === name.toLowerCase());
  const full = members.length >= MAX_FOLLOWED_MEMBERS;

  function add(raw: string) {
    const name = raw.split(/\s+/).filter(Boolean).join(' ');
    if (!name || name.length > MAX_NAME_LENGTH || has(name) || full) return;
    setMembers([...members, name]);
    setQuery('');
  }

  function save() {
    update.mutate(
      { smart_money_follow_congress: mode, smart_money_followed_members: members },
      {
        onSuccess: () => {
          setSaved(true);
          window.setTimeout(() => setSaved(false), 2000);
        },
      },
    );
  }

  const suggestions = (names.data?.names ?? []).filter((n) => !has(n.name)).slice(0, 8);
  const typed = query.trim();

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="congress-follow">
      <h3>Congress: who to follow</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        The House watcher alerts when a member you follow reports buying a stock on your watchlist. Everyone&apos;s reports are
        still stored and shown on the Smart Money page. Reports arrive up to 45 days after the trade, and amounts are ranges. The
        Senate is not available.
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="radio" name="congress-follow-mode" checked={mode === 'all'} onChange={() => setMode('all')} style={{ width: 'auto' }} />
          Follow every member
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="radio" name="congress-follow-mode" checked={mode === 'list'} onChange={() => setMode('list')} style={{ width: 'auto' }} />
          Only the members below
        </label>
      </div>
      {mode === 'list' && (
        <>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {members.length === 0 && <span className="text-muted" style={{ fontSize: 13 }}>Nobody yet: no member alerts until you add one.</span>}
            {members.map((m) => (
              <span key={m} className="badge badge-neutral" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                {m}
                <button
                  type="button"
                  aria-label={`Stop following ${m}`}
                  onClick={() => setMembers(members.filter((x) => x !== m))}
                  style={{ border: 'none', background: 'transparent', cursor: 'pointer', padding: 0, color: 'inherit' }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div>
            <label>Find a member (names seen in loaded reports) or type one</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type="text"
                value={query}
                maxLength={MAX_NAME_LENGTH}
                placeholder="e.g. Pelosi"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') add(typed);
                }}
              />
              <button className="btn btn-secondary" type="button" onClick={() => add(typed)} disabled={!typed || full}>
                Add
              </button>
            </div>
            {full && <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>At most {MAX_FOLLOWED_MEMBERS} members.</div>}
            {typed && suggestions.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                {suggestions.map((n) => (
                  <button key={n.name} type="button" className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => add(n.name)}>
                    {n.name}
                    {n.state_district ? ` (${n.state_district})` : ''}
                  </button>
                ))}
              </div>
            )}
            {typed && names.data && suggestions.length === 0 && (
              <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
                No loaded report has that name. Add it anyway: it matches names as the House Clerk prints them, ignoring case and punctuation.
              </div>
            )}
          </div>
        </>
      )}
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

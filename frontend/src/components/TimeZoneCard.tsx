import { useState } from 'react';
import {
  browserTimeZone,
  getDisplayTimeZone,
  listTimeZones,
  setDisplayTimeZone,
} from '../lib/timezone';

const BROWSER = '';

function nowIn(zone: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone: zone, weekday: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date());
}

export function TimeZoneCard() {
  const [choice, setChoice] = useState<string>(getDisplayTimeZone() ?? BROWSER);
  const zones = listTimeZones();
  const effective = choice || browserTimeZone();

  function change(value: string) {
    setChoice(value);
    setDisplayTimeZone(value || undefined);
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3>Time zone</h3>
      <div className="text-muted" style={{ fontSize: 13 }}>
        The zone used when the app writes a clock time: the "as of" labels, the equity curve, pause history,
        "redo at the open" times and position dates. Saved in this browser only, and applied the next time a
        page is shown.
      </div>
      <div>
        <label>Display time zone</label>
        <select value={choice} onChange={(e) => change(e.target.value)}>
          <option value={BROWSER}>This browser ({browserTimeZone()})</option>
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
          It is now {nowIn(effective)} in {effective}.
        </div>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        This only changes how times are shown. The US market session, the 09:45 redo, the auto-scan and the
        morning note schedules, and the Analysis intraday chart axis all stay on New York time.
      </div>
    </div>
  );
}

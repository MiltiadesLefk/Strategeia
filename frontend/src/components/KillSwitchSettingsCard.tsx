import { useEffect, useState } from 'react';
import { useSettings, useUpdateSettings } from '../api/hooks';
import { ToggleSwitch } from './common';

/** Kill switches on the Settings page: pause a sleeve's new positions after a drawdown or when live results drift. */
export function KillSwitchSettingsCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [drawdown, setDrawdown] = useState('');
  const [psi, setPsi] = useState('');

  useEffect(() => {
    if (!settings) return;
    setDrawdown(String(settings.kill_switch_drawdown_pct));
    setPsi(String(settings.kill_switch_drift_psi));
  }, [settings]);

  const dd = Number(drawdown);
  const ps = Number(psi);
  const valid = drawdown !== '' && psi !== '' && dd >= 0 && dd <= 100 && ps >= 0 && ps <= 5;
  const dirty = !!settings && (dd !== settings.kill_switch_drawdown_pct || ps !== settings.kill_switch_drift_psi);

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="kill-switch-settings">
      <h3>Kill switches</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        When on, a sleeve is paused if its equity falls too far below its peak, or (core sleeve) if live trade results
        drift away from the latest backtest. A pause only stops new positions; open ones keep being managed. You lift it
        by hand with Resume on the Portfolio page. Set a limit to 0 to switch that check off.
      </div>
      <ToggleSwitch
        checked={settings?.kill_switch_enabled ?? false}
        onChange={(checked) => update.mutate({ kill_switch_enabled: checked })}
        label="Kill switches"
        disabled={!settings || update.isPending}
      />
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
          Drawdown limit (% below peak)
          <input type="number" min={0} max={100} step={1} value={drawdown} onChange={(e) => setDrawdown(e.target.value)} />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
          Drift limit (PSI, 0.25 is a large shift)
          <input type="number" min={0} max={5} step={0.05} value={psi} onChange={(e) => setPsi(e.target.value)} />
        </label>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!valid || !dirty || update.isPending}
          onClick={() => update.mutate({ kill_switch_drawdown_pct: dd, kill_switch_drift_psi: ps })}
        >
          Save limits
        </button>
      </div>
      {update.error && (
        <span className="text-red" style={{ fontSize: 13 }}>
          {update.error.message}
        </span>
      )}
    </div>
  );
}

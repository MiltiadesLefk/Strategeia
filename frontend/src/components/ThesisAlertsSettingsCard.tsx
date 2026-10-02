import { useSettings, useUpdateSettings } from '../api/hooks';
import { ToggleSwitch } from './common';

/** Thesis tracker alerts on the Settings page. Saves on its own when the switch is flipped. */
export function ThesisAlertsSettingsCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="thesis-alerts-settings">
      <h3>Thesis alerts</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        Every open position has a thesis on the Portfolio page: the reasons the trade was taken, re-checked by rules
        against fresh price data. When the core trend pillar breaks, a warning shows on the position. With this on, one
        Telegram message is also sent the first time that happens (needs the Telegram settings above). It never closes
        or changes a position.
      </div>
      <ToggleSwitch
        checked={settings?.thesis_alerts ?? true}
        onChange={(checked) => update.mutate({ thesis_alerts: checked })}
        label="Thesis alerts"
        disabled={!settings || update.isPending}
      />
      {update.error && (
        <span className="text-red" style={{ fontSize: 13 }}>
          {update.error.message}
        </span>
      )}
    </div>
  );
}

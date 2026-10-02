import { useSettings, useUpdateSettings } from '../api/hooks';

/** AI Committee limits on the Settings page. Each field saves on its own when it changes. */
export function CommitteeSettingsCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();

  const field = (label: string, key: 'committee_max_llm_calls' | 'committee_debate_rounds' | 'committee_risk_rounds', min: number, max: number) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
      {label}
      <select
        value={settings?.[key] ?? ''}
        disabled={!settings || update.isPending}
        onChange={(e) => update.mutate({ [key]: Number(e.target.value) })}
        style={{ width: 120 }}
      >
        {Array.from({ length: max - min + 1 }, (_, i) => min + i).map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="committee-settings">
      <h3>AI Committee limits</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        One committee run is a dozen or so calls on your AI provider. The call limit is hard: optional steps (analysts and
        debate turns) are skipped before the three required ones (research manager, trader, final rating) can be starved.
        Debate rounds are capped at 3. The committee is an opinion only and never opens or changes a trade.
      </div>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        {field('Most AI calls per run', 'committee_max_llm_calls', 6, 40)}
        {field('Bull / bear rounds', 'committee_debate_rounds', 1, 3)}
        {field('Risk debate rounds', 'committee_risk_rounds', 1, 3)}
      </div>
      {update.error && (
        <span className="text-red" style={{ fontSize: 13 }}>
          {update.error.message}
        </span>
      )}
    </div>
  );
}

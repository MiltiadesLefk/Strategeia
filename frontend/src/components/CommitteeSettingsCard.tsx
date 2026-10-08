import { useSettings, useUpdateSettings } from '../api/hooks';

/** AI Committee limits on the Settings page. Each field saves on its own when it changes. */
export function CommitteeSettingsCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  // 5 analysts + 2 per bull/bear round + manager + trader + 3 per risk round + the final rating.
  const fullRunCalls = settings ? 5 + settings.committee_debate_rounds * 2 + 2 + settings.committee_risk_rounds * 3 + 1 : 0;

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
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, cursor: 'pointer' }}>
          <input
            type="checkbox"
            style={{ width: 'auto' }}
            checked={settings?.committee_gate_enabled ?? false}
            disabled={!settings || update.isPending}
            onChange={(e) => update.mutate({ committee_gate_enabled: e.target.checked })}
          />
          <strong>Use the committee as a final gate on trades</strong>
        </label>
        <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
          Off by default. The rules scan and score every stock with no AI. Only a plan the rules approved, at the moment a
          paper position would really open (auto-scan and the market-open redo), or that you generate by hand with the
          Generate button (never a backtest), is
          sent to the committee with all the data the app gathered. A Buy or Overweight backs a long and a Sell or
          Underweight backs a short; anything else is an objection. It can only stop a trade. It never starts one or
          changes its entry, stop or size, and a committee that cannot run or be read never blocks anything.
        </div>
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', opacity: settings?.committee_gate_enabled ? 1 : 0.5 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
            When the committee objects
            <select
              value={settings?.committee_gate_action ?? 'cancel'}
              disabled={!settings || update.isPending || !settings.committee_gate_enabled}
              onChange={(e) => update.mutate({ committee_gate_action: e.target.value as 'cancel' | 'hold' })}
              style={{ width: 260 }}
            >
              <option value="cancel">Cancel the trade (saved as no trade)</option>
              <option value="hold">Hold it for my manual review</option>
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
            Most committee runs per scan
            <select
              value={settings?.committee_gate_max_per_scan ?? 5}
              disabled={!settings || update.isPending || !settings.committee_gate_enabled}
              onChange={(e) => update.mutate({ committee_gate_max_per_scan: Number(e.target.value) })}
              style={{ width: 120 }}
            >
              {Array.from({ length: 20 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Each run is up to the call limit above, so a scan costs at most (runs per scan x calls per run) AI calls.
        </div>
      </div>
      {settings && (
        <div
          style={{ fontSize: 13, color: fullRunCalls > settings.committee_max_llm_calls ? 'var(--amber)' : undefined }}
          className={fullRunCalls > settings.committee_max_llm_calls ? undefined : 'text-muted'}
          data-testid="committee-call-math"
        >
          With these rounds a full run is up to {fullRunCalls} AI calls (5 analysts, {settings.committee_debate_rounds * 2} debate turns,
          research manager, trader, {settings.committee_risk_rounds * 3} risk turns, final rating).
          {fullRunCalls > settings.committee_max_llm_calls
            ? ` Your limit is ${settings.committee_max_llm_calls}, so the last optional steps (risk turns, then debate turns) are skipped. Raise the limit to ${fullRunCalls} or lower the rounds.`
            : ' That fits your limit.'}
        </div>
      )}
      {update.error && (
        <span className="text-red" style={{ fontSize: 13 }}>
          {update.error.message}
        </span>
      )}
    </div>
  );
}

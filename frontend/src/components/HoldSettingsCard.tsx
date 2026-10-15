import { useEffect, useState } from 'react';
import { useSettings, useUpdateSettings } from '../api/hooks';
import { ToggleSwitch } from './common';
import type { AppSettings } from '../api/types';

type Draft = Pick<
  AppSettings,
  | 'hold_buy_only_dips'
  | 'hold_stop_pct'
  | 'hold_alert_drawdown_pct'
  | 'hold_target_weight_pct'
  | 'hold_max_positions'
  | 'hold_max_per_sector'
  | 'hold_max_new_per_scan'
  | 'hold_failed_gate_size_pct'
  | 'news_discovery_enabled'
  | 'news_discovery_max_per_run'
>;

const NUMBER_FIELDS: { key: keyof Draft; label: string; hint: string; step?: number }[] = [
  { key: 'hold_target_weight_pct', label: 'Target weight per stock (%)', hint: 'Size of one buy, as a share of the account.', step: 0.5 },
  { key: 'hold_stop_pct', label: 'Disaster stop (% below entry)', hint: 'Closes on a daily close this far down, or under the 200-day average if lower. 0 = alert only.' },
  { key: 'hold_alert_drawdown_pct', label: 'Alert at (% below entry)', hint: 'A message only, never a trade. 0 = off.' },
  { key: 'hold_max_positions', label: 'Max positions', hint: 'Open hold positions in the sleeve.' },
  { key: 'hold_max_per_sector', label: 'Max per sector', hint: 'Same-sector hold positions.' },
  { key: 'hold_max_new_per_scan', label: 'Max new buys per daily scan', hint: 'Keeps one scan from buying the whole list.' },
  { key: 'hold_failed_gate_size_pct', label: 'Size of a smart-money buy that fails the gate (% of a normal slice)', hint: '0 = send it to the review queue instead.' },
  { key: 'news_discovery_max_per_run', label: 'Max news-discovered tickers per run', hint: 'Only used when news discovery is on.' },
];

/** Portfolio builder controls. Saved on their own button. */
export function HoldSettingsCard() {
  const { data } = useSettings();
  const { mutate, isPending, isSuccess } = useUpdateSettings();
  const [draft, setDraft] = useState<Draft | null>(null);

  useEffect(() => {
    if (data) {
      setDraft({
        hold_buy_only_dips: data.hold_buy_only_dips,
        hold_stop_pct: data.hold_stop_pct, hold_alert_drawdown_pct: data.hold_alert_drawdown_pct,
        hold_target_weight_pct: data.hold_target_weight_pct, hold_max_positions: data.hold_max_positions,
        hold_max_per_sector: data.hold_max_per_sector, hold_max_new_per_scan: data.hold_max_new_per_scan,
        hold_failed_gate_size_pct: data.hold_failed_gate_size_pct,
        news_discovery_enabled: data.news_discovery_enabled, news_discovery_max_per_run: data.news_discovery_max_per_run,
      });
    }
  }, [data]);
  if (!draft) return null;
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft({ ...draft, [key]: value });

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3>Portfolio builder</h3>
      <div className="text-muted" style={{ fontSize: 13 }}>
        Buys good stocks and holds them: no target, no time limit. The daily 10:00 New York scan runs a quality check
        (above the 200-day average, growing revenue, profitable, not expensive) and buys what passes at the target weight.
        Smart-money candidates go through the same check; one that fails waits in the review queue. Paper money only.
        Buying on its own needs Auto-Execute (above) and the Auto-Scan switch below.
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.hold_buy_only_dips} onChange={(v) => set('hold_buy_only_dips', v)} label="Only buy dips" />
        <span>Only buy a good stock while it is 10% or more under its 3-month high</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.news_discovery_enabled} onChange={(v) => set('news_discovery_enabled', v)} label="News discovery" />
        <span>Look for new tickers in the news of watched stocks (off by default)</span>
      </div>
      {NUMBER_FIELDS.map((f) => (
        <div key={f.key}>
          <label>{f.label}</label>
          <input type="number" step={f.step ?? 1} min={0} value={draft[f.key] as number} onChange={(e) => set(f.key, Number(e.target.value) as never)} />
          <div className="text-muted" style={{ fontSize: 12 }}>{f.hint}</div>
        </div>
      ))}
      <div>
        <button className="btn btn-primary" disabled={isPending} onClick={() => mutate(draft)}>
          {isPending ? 'Saving…' : 'Save portfolio builder settings'}
        </button>
        {isSuccess && <span className="text-muted" style={{ marginLeft: 10, fontSize: 13 }}>Saved.</span>}
      </div>
    </div>
  );
}

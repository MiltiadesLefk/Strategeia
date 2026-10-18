import { useEffect, useState } from 'react';
import { useSettings, useUpdateSettings } from '../api/hooks';
import { useAutoSave } from '../api/useAutoSave';
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
  | 'hold_min_revenue_growth_pct'
  | 'require_growth_acceleration'
  | 'require_operating_leverage'
  | 'require_financial_health'
  | 'block_on_distribution'
  | 'require_accumulation'
  | 'sell_on_distribution'
  | 'require_rising_ma200'
  | 'max_extension_above_ma200_pct'
  | 'big_news_buy_enabled'
  | 'big_news_size_pct'
  | 'big_news_lookback_hours'
  | 'big_news_skip_if_up_pct'
  | 'hold_stop_mode'
  | 'news_discovery_enabled'
  | 'hold_add_on_dips'
  | 'evidence_bonus_enabled'
  | 'news_discovery_max_per_run'
  | 'sell_ma200_days'
  | 'sell_trail_pct'
  | 'rebuy_cooldown_days'
  | 'hold_review_days'
>;

const NUMBER_FIELDS: { key: keyof Draft; label: string; hint: string; step?: number }[] = [
  { key: 'hold_target_weight_pct', label: 'Target weight per stock (%)', hint: 'Size of one buy, as a share of the account.', step: 0.5 },
  { key: 'hold_stop_pct', label: 'Disaster stop (% below entry)', hint: 'Closes on a daily close this far down, or under the 200-day average if lower. 0 = alert only.' },
  { key: 'hold_alert_drawdown_pct', label: 'Alert at (% below entry)', hint: 'A message only, never a trade. 0 = off.' },
  { key: 'hold_max_positions', label: 'Max positions', hint: 'Open hold positions in the sleeve.' },
  { key: 'hold_max_per_sector', label: 'Max per sector', hint: 'Same-sector hold positions.' },
  { key: 'hold_max_new_per_scan', label: 'Max new buys per daily scan', hint: 'Keeps one scan from buying the whole list.' },
  { key: 'hold_failed_gate_size_pct', label: 'Size of a smart-money buy that fails the gate (% of a normal slice)', hint: 'Half a slice by default (50).' },
  { key: 'max_extension_above_ma200_pct', label: 'Quality check: max % above the 200-day (0 = off)', hint: 'Skips a stock stretched far above its 200-day average; 30 is a common limit.', step: 1 },
  { key: 'hold_min_revenue_growth_pct', label: 'Quality check: minimum revenue growth (% year on year)', hint: '5 by default. 0 = only not shrinking.', step: 0.5 },
  { key: 'big_news_size_pct', label: 'Size of a big-news buy (% of a normal slice)', hint: 'Only used when big-news buys are on. Half a slice by default (50).' },
  { key: 'big_news_lookback_hours', label: 'Big news counts for (hours)', hint: 'A headline older than this is ignored; a bad headline in this window blocks the buy.' },
  { key: 'big_news_skip_if_up_pct', label: 'Big news: skip the buy if the stock is already up this % today (0 = off)', hint: 'Avoids buying after a pop. Off by default; you choose the number.', step: 0.5 },
  { key: 'news_discovery_max_per_run', label: 'Max news-discovered tickers per run', hint: 'Only used when news discovery is on.' },
  { key: 'sell_ma200_days', label: 'Sell plan: closes under the 200-day that count as a signal', hint: 'In a row.' },
  { key: 'sell_trail_pct', label: 'Sell plan: stop trails this % below the high (profit, one signal)', hint: '0 = move the stop to breakeven instead.' },
  { key: 'rebuy_cooldown_days', label: 'Rebuy watch: days to wait after a sale', hint: 'Then the check must pass and the stock must be in a dip.' },
  { key: 'hold_review_days', label: 'Review each holding every (days)', hint: 'A message with a fresh quality check; never sells. 0 = off.' },
];

function fromSettings(data: AppSettings): Draft {
  return {
    hold_buy_only_dips: data.hold_buy_only_dips,
    hold_stop_pct: data.hold_stop_pct, hold_alert_drawdown_pct: data.hold_alert_drawdown_pct,
    hold_target_weight_pct: data.hold_target_weight_pct, hold_max_positions: data.hold_max_positions,
    hold_max_per_sector: data.hold_max_per_sector, hold_max_new_per_scan: data.hold_max_new_per_scan,
    hold_failed_gate_size_pct: data.hold_failed_gate_size_pct,
    hold_min_revenue_growth_pct: data.hold_min_revenue_growth_pct, require_growth_acceleration: data.require_growth_acceleration, require_operating_leverage: data.require_operating_leverage, require_financial_health: data.require_financial_health,
    block_on_distribution: data.block_on_distribution, require_accumulation: data.require_accumulation,
    sell_on_distribution: data.sell_on_distribution,
    require_rising_ma200: data.require_rising_ma200, max_extension_above_ma200_pct: data.max_extension_above_ma200_pct,
    big_news_buy_enabled: data.big_news_buy_enabled, big_news_size_pct: data.big_news_size_pct,
    big_news_lookback_hours: data.big_news_lookback_hours, big_news_skip_if_up_pct: data.big_news_skip_if_up_pct, hold_stop_mode: data.hold_stop_mode,
    hold_add_on_dips: data.hold_add_on_dips, evidence_bonus_enabled: data.evidence_bonus_enabled, news_discovery_enabled: data.news_discovery_enabled, news_discovery_max_per_run: data.news_discovery_max_per_run,
    sell_ma200_days: data.sell_ma200_days, sell_trail_pct: data.sell_trail_pct, rebuy_cooldown_days: data.rebuy_cooldown_days,
    hold_review_days: data.hold_review_days,
  };
}

/** Portfolio builder controls. Saved on their own, a moment after the last change. */
export function HoldSettingsCard() {
  const { data } = useSettings();
  const { mutate, isPending } = useUpdateSettings();
  const [draft, setDraft] = useState<Draft | null>(null);

  useEffect(() => {
    if (data) setDraft(fromSettings(data));
  }, [data]);
  const saved = data ? JSON.stringify(fromSettings(data)) : '';
  const current = draft ? JSON.stringify(draft) : '';
  const valid = !!draft && NUMBER_FIELDS.every((f) => Number.isFinite(draft[f.key] as number) && (draft[f.key] as number) >= 0);
  useAutoSave(current, !!draft && current !== saved, valid, () => draft && mutate(draft));
  if (!draft) return null;
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft({ ...draft, [key]: value });

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3>Portfolio builder</h3>
      <div className="text-muted" style={{ fontSize: 13 }}>
        Buys good stocks and holds them: no target, no time limit. The daily 10:00 New York scan runs a quality check
        (above the 200-day average, growing revenue, profitable, not expensive) and buys what passes at the target weight.
        Smart-money candidates go through the same check; one that fails still buys, at a smaller size. Paper money only.
        Buying on its own needs Auto-Execute (above) and the Auto-Scan switch below.
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.hold_buy_only_dips} onChange={(v) => set('hold_buy_only_dips', v)} label="Only buy dips" />
        <span>Only buy a good stock while it is 10% or more under its 3-month high</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.hold_add_on_dips} onChange={(v) => set('hold_add_on_dips', v)} label="Add on dips" />
        <span>Top up a held stock toward its target weight when it passes the check and is in a dip (off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.require_growth_acceleration} onChange={(v) => set('require_growth_acceleration', v)} label="Growth must accelerate" />
        <span>The quality check also needs this year's revenue growth to beat last year's (three reported years; off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.require_operating_leverage} onChange={(v) => set('require_operating_leverage', v)} label="Operating leverage" />
        <span>The quality check also needs operating income to grow faster than revenue (missing operating income fails; off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.require_financial_health} onChange={(v) => set('require_financial_health', v)} label="Financial health" />
        <span>The quality check also needs debt-to-equity at most 1, current ratio at least 1 and positive free cash flow (missing figures fail; off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.require_rising_ma200} onChange={(v) => set('require_rising_ma200', v)} label="Rising 200-day" />
        <span>The quality check also needs the 200-day average to be at or above its value 20 bars ago (off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.block_on_distribution} onChange={(v) => set('block_on_distribution', v)} label="Block on distribution" />
        <span>The quality check fails a stock whose up-day volume is 0.77x its down-day volume or less over 20 days (sellers heavier; off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.require_accumulation} onChange={(v) => set('require_accumulation', v)} label="Require accumulation" />
        <span>Stricter: the quality check needs up-day volume at 1.3x down-day volume or more (off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.sell_on_distribution} onChange={(v) => set('sell_on_distribution', v)} label="Sell signal on distribution" />
        <span>Sellers heavier on volume while the price is under its 50-day average counts as one sell signal; never sells alone (off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.evidence_bonus_enabled} onChange={(v) => set('evidence_bonus_enabled', v)} label="Evidence bonus" />
        <span>Insider buys, Congress buys and big news for a stock rank it first and skip it on negative news; the quality check is still required</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.news_discovery_enabled} onChange={(v) => set('news_discovery_enabled', v)} label="News discovery" />
        <span>Look for new tickers in the news of watched stocks (off by default)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ToggleSwitch checked={draft.big_news_buy_enabled} onChange={(v) => set('big_news_buy_enabled', v)} label="Big-news buys" />
        <span>Buy a stock on a big headline or 8-K filing, from the watchlist or the whole market (wire releases, SEC filings) (FDA approval, contract, takeover offer, guidance raise...) without the quality check, at a smaller size (off by default)</span>
      </div>
      <div>
        <label>Disaster stop trigger</label>
        <select value={draft.hold_stop_mode} onChange={(e) => set('hold_stop_mode', e.target.value as Draft['hold_stop_mode'])}>
          <option value="touch">Touch: closes as soon as the price reaches the stop</option>
          <option value="close">Close: only a daily close beyond the stop</option>
        </select>
      </div>
      {NUMBER_FIELDS.map((f) => (
        <div key={f.key}>
          <label>{f.label}</label>
          <input type="number" step={f.step ?? 1} min={0} value={draft[f.key] as number} onChange={(e) => set(f.key, Number(e.target.value) as never)} />
          <div className="text-muted" style={{ fontSize: 12 }}>{f.hint}</div>
        </div>
      ))}
      {isPending && <div className="text-muted" style={{ fontSize: 13 }}>Saving…</div>}
    </div>
  );
}

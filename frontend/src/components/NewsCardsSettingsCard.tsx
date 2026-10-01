import { useEffect, useState } from 'react';
import { useSettings, useUpdateSettings } from '../api/hooks';
import { ToggleSwitch } from './common';

/** News cards on the Settings page: whether the AI labels saved headlines and how many per run.
 *  Saves on its own (it is not part of the Settings page's other forms). */
export function NewsCardsSettingsCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [enabled, setEnabled] = useState(false);
  const [limit, setLimit] = useState(20);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setEnabled(settings.news_cards_enabled);
    setLimit(settings.news_card_batch_limit);
  }, [settings]);

  const limitValid = Number.isInteger(limit) && limit >= 1 && limit <= 30;

  function save() {
    update.mutate(
      { news_cards_enabled: enabled, news_card_batch_limit: limit },
      {
        onSuccess: () => {
          setSaved(true);
          window.setTimeout(() => setSaved(false), 2000);
        },
      },
    );
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="news-cards-settings">
      <h3>News cards</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        When on, the AI you configured above labels each saved headline once with its event type, sentiment and
        materiality. Labels appear on the Analysis page&apos;s News tab and are recorded on every trade plan as a silent
        signal; they do not change any score today. Each run uses at most three AI calls. Off by default because it
        spends AI calls.
      </div>
      <ToggleSwitch checked={enabled} onChange={setEnabled} label="News cards" />
      <div>
        <label>Most headlines labelled per run</label>
        <input type="number" value={limit} min={1} max={30} onChange={(e) => setLimit(Number(e.target.value))} />
        {!limitValid && (
          <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
            Enter a whole number from 1 to 30.
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn btn-secondary" onClick={save} disabled={!limitValid || update.isPending}>
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

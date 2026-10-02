import { useEffect, useState } from 'react';
import { usePreviewNote, useSendNote, useSettings, useUpdateSettings } from '../api/hooks';
import { ErrorBanner, ToggleSwitch } from './common';

const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Notifications on the Settings page: the morning note, the Friday weekly digest and the
 *  automatic alerts on open positions. Saves on its own. Delivery needs Telegram (set above);
 *  the preview buttons work without it and send nothing. */
export function NotificationsSettingsCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const previewMorning = usePreviewNote('morning');
  const previewWeekly = usePreviewNote('weekly');
  const sendMorning = useSendNote('morning');
  const sendWeekly = useSendNote('weekly');

  const [morningOn, setMorningOn] = useState(false);
  const [morningTime, setMorningTime] = useState('08:45');
  const [weeklyOn, setWeeklyOn] = useState(false);
  const [positionAlerts, setPositionAlerts] = useState(true);
  const [stopAtr, setStopAtr] = useState(1);
  const [tpPct, setTpPct] = useState(1);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setMorningOn(settings.morning_note_enabled);
    setMorningTime(settings.morning_note_time_et);
    setWeeklyOn(settings.weekly_digest_enabled);
    setPositionAlerts(settings.price_alert_positions_enabled);
    setStopAtr(settings.price_alert_stop_atr);
    setTpPct(settings.price_alert_tp1_pct);
  }, [settings]);

  const timeValid = TIME_PATTERN.test(morningTime);
  const stopValid = stopAtr > 0 && stopAtr <= 10;
  const tpValid = tpPct > 0 && tpPct <= 20;
  const telegramReady = !!(settings?.telegram_bot_token && settings?.telegram_chat_id);

  function save() {
    update.mutate(
      {
        morning_note_enabled: morningOn,
        morning_note_time_et: morningTime,
        weekly_digest_enabled: weeklyOn,
        price_alert_positions_enabled: positionAlerts,
        price_alert_stop_atr: stopAtr,
        price_alert_tp1_pct: tpPct,
      },
      {
        onSuccess: () => {
          setSaved(true);
          window.setTimeout(() => setSaved(false), 2000);
        },
      },
    );
  }

  const preview = previewMorning.data ?? previewWeekly.data;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }} data-testid="notifications-settings">
      <h3>Notifications</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 680 }}>
        Telegram messages built from the app&apos;s own data. They only inform: none of them can open, change or close a
        paper trade. {telegramReady ? '' : 'Telegram is not configured above, so nothing is delivered yet (alerts are still recorded). '}
        Each note is rule-based text; if an AI provider is configured it also adds one short, labelled paragraph that restates
        the same facts.
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <ToggleSwitch checked={morningOn} onChange={setMorningOn} label="Morning note" />
        <div style={{ fontSize: 13 }}>
          <strong>Morning note</strong>: open positions, plans waiting, top setups, today&apos;s events and overnight levels, on
          trading days.
        </div>
        <div>
          <label>Send at (New York time, HH:MM)</label>
          <input type="text" value={morningTime} onChange={(e) => setMorningTime(e.target.value)} style={{ width: 90 }} aria-label="Morning note time" />
          {!timeValid && (
            <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
              Write the time as HH:MM on a 24-hour clock, for example 08:45.
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <ToggleSwitch checked={weeklyOn} onChange={setWeeklyOn} label="Weekly digest" />
        <div style={{ fontSize: 13 }}>
          <strong>Weekly digest</strong>: the week&apos;s closed trades in R, equity change, plans taken and missed, the
          calibration headline and next week&apos;s calendar. Sent at 16:30 New York time on the last trading day of the week.
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <ToggleSwitch checked={positionAlerts} onChange={setPositionAlerts} label="Alerts on open positions" />
        <div style={{ fontSize: 13 }}>
          <strong>Alerts on open positions</strong>: a message when an open paper position gets close to its stop or its first
          target (once per day for each). Checked every five minutes while the market is open.
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <label>Close to the stop: within (ATRs)</label>
            <input type="number" min={0.1} max={10} step={0.1} value={stopAtr} onChange={(e) => setStopAtr(Number(e.target.value))} style={{ width: 100 }} />
          </div>
          <div>
            <label>Close to the first target: within (% of price)</label>
            <input type="number" min={0.1} max={20} step={0.1} value={tpPct} onChange={(e) => setTpPct(Number(e.target.value))} style={{ width: 100 }} />
          </div>
        </div>
        {(!stopValid || !tpValid) && (
          <div className="text-red" style={{ fontSize: 12 }}>
            Stop distance must be above 0 and at most 10 ATRs; target distance above 0 and at most 20%.
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" onClick={save} disabled={!timeValid || !stopValid || !tpValid || update.isPending}>
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

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn btn-secondary" onClick={() => { previewWeekly.reset(); previewMorning.mutate(false); }} disabled={previewMorning.isPending}>
          {previewMorning.isPending ? 'Building…' : 'Preview morning note'}
        </button>
        <button className="btn btn-secondary" onClick={() => { previewMorning.reset(); previewWeekly.mutate(false); }} disabled={previewWeekly.isPending}>
          {previewWeekly.isPending ? 'Building…' : 'Preview weekly digest'}
        </button>
        <button className="btn btn-secondary" onClick={() => sendMorning.mutate()} disabled={!telegramReady || sendMorning.isPending} title={telegramReady ? undefined : 'Configure Telegram first'}>
          Send morning note now
        </button>
        <button className="btn btn-secondary" onClick={() => sendWeekly.mutate()} disabled={!telegramReady || sendWeekly.isPending} title={telegramReady ? undefined : 'Configure Telegram first'}>
          Send weekly digest now
        </button>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        A preview builds the note from live data and sends nothing; it does not use the AI. The send buttons deliver it to
        Telegram now (any day) and add the AI paragraph when an AI provider is configured.
      </div>
      {(previewMorning.error || previewWeekly.error) && <ErrorBanner message={(previewMorning.error ?? previewWeekly.error)!.message} />}
      {sendMorning.data && <div className={sendMorning.data.ok ? 'text-green' : 'text-red'} style={{ fontSize: 13 }}>Morning note: {sendMorning.data.message}</div>}
      {sendWeekly.data && <div className={sendWeekly.data.ok ? 'text-green' : 'text-red'} style={{ fontSize: 13 }}>Weekly digest: {sendWeekly.data.message}</div>}
      {sendMorning.error && <ErrorBanner message={sendMorning.error.message} />}
      {sendWeekly.error && <ErrorBanner message={sendWeekly.error.message} />}
      {preview && (
        <div data-testid="note-preview">
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 4 }}>
            Preview ({preview.parts} Telegram message{preview.parts === 1 ? '' : 's'}
            {preview.ai_used ? ', with AI paragraph' : ', rule-based'})
          </div>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, padding: 12, borderRadius: 8, background: 'var(--surface-2, rgba(128,128,128,0.08))' }}>
            {preview.text}
          </pre>
        </div>
      )}
    </div>
  );
}

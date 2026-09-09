import { useEffect, useState } from 'react';
import { useSettings, useTestConnection, useUpdateSettings } from '../api/hooks';
import { LoadingSpinner } from '../components/common';
import { SecretField } from '../components/SecretField';

const LLM_OPTIONS = [
  { value: 'none', label: 'None (rule-based text)' },
  { value: 'claude_code_cli', label: 'Claude Code CLI (uses your existing login, no key)' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'orcarouter', label: 'OrcaRouter' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'gemini', label: 'Google Gemini' },
];

export function SettingsPage() {
  const { data: settings, isLoading } = useSettings();
  const { mutate: update, isPending: saving } = useUpdateSettings();
  const { mutate: testLlm, data: llmTestResult, isPending: testingLlm } = useTestConnection();
  const { mutate: testFinnhub, data: finnhubTestResult, isPending: testingFinnhub } = useTestConnection();
  const { mutate: testTelegram, data: telegramTestResult, isPending: testingTelegram } = useTestConnection();

  const [llmProvider, setLlmProvider] = useState('none');
  const [openrouterKey, setOpenrouterKey] = useState('');
  const [openrouterModel, setOpenrouterModel] = useState('');
  const [orcarouterKey, setOrcarouterKey] = useState('');
  const [orcarouterModel, setOrcarouterModel] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [geminiKey, setGeminiKey] = useState('');
  const [finnhubEnabled, setFinnhubEnabled] = useState(false);
  const [finnhubKey, setFinnhubKey] = useState('');
  const [telegramToken, setTelegramToken] = useState('');
  const [telegramChatId, setTelegramChatId] = useState('');
  const [startingCash, setStartingCash] = useState(100_000);
  const [defaultRiskPct, setDefaultRiskPct] = useState(1);
  const [scanSize, setScanSize] = useState(50);
  const [autoExecute, setAutoExecute] = useState(true);
  const [autoScanEnabled, setAutoScanEnabled] = useState(false);
  const [maxConcurrentPositions, setMaxConcurrentPositions] = useState(5);
  const [aiOverlayEnabled, setAiOverlayEnabled] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setLlmProvider(settings.llm_provider);
    setOpenrouterModel(settings.openrouter_model);
    setOrcarouterModel(settings.orcarouter_model);
    setFinnhubEnabled(settings.finnhub_enabled);
    setTelegramChatId(settings.telegram_chat_id);
    setStartingCash(settings.paper_starting_cash);
    setDefaultRiskPct(settings.default_risk_pct);
    setScanSize(settings.scan_universe_size);
    setAutoExecute(settings.auto_execute_trade_plans);
    setAutoScanEnabled(settings.auto_scan_enabled);
    setMaxConcurrentPositions(settings.max_concurrent_positions);
    setAiOverlayEnabled(settings.ai_trading_overlay_enabled);
  }, [settings]);

  function saveLlm() {
    update(
      {
        llm_provider: llmProvider,
        ...(openrouterKey ? { openrouter_api_key: openrouterKey } : {}),
        openrouter_model: openrouterModel,
        ...(orcarouterKey ? { orcarouter_api_key: orcarouterKey } : {}),
        orcarouter_model: orcarouterModel,
        ...(openaiKey ? { openai_api_key: openaiKey } : {}),
        ...(geminiKey ? { gemini_api_key: geminiKey } : {}),
      },
      {
        onSuccess: () => {
          setOpenrouterKey('');
          setOrcarouterKey('');
          setOpenaiKey('');
          setGeminiKey('');
        },
      },
    );
  }

  function saveFinnhub() {
    update(
      { finnhub_enabled: finnhubEnabled, ...(finnhubKey ? { finnhub_api_key: finnhubKey } : {}) },
      { onSuccess: () => setFinnhubKey('') },
    );
  }

  function saveTelegram() {
    update(
      {
        telegram_chat_id: telegramChatId,
        ...(telegramToken ? { telegram_bot_token: telegramToken } : {}),
      },
      { onSuccess: () => setTelegramToken('') },
    );
  }

  function saveAccount() {
    update({
      paper_starting_cash: startingCash,
      default_risk_pct: defaultRiskPct,
      scan_universe_size: scanSize,
      auto_execute_trade_plans: autoExecute,
    });
  }

  function saveAutomation() {
    update({
      auto_scan_enabled: autoScanEnabled,
      max_concurrent_positions: maxConcurrentPositions,
    });
  }

  function saveAiOverlay() {
    update({ ai_trading_overlay_enabled: aiOverlayEnabled });
  }

  if (isLoading) return <LoadingSpinner label="Loading settings…" />;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(440px, 1fr))', gap: 20, alignItems: 'start' }}>
      <h1 style={{ fontSize: 22, gridColumn: '1 / -1' }}>Settings</h1>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>AI Narrative Provider</h3>
        <div>
          <label>Provider</label>
          <select value={llmProvider} onChange={(e) => setLlmProvider(e.target.value)}>
            {LLM_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        {llmProvider === 'openrouter' && (
          <>
            <SecretField
              key={settings?.openrouter_api_key ?? ''}
              label="OpenRouter API Key"
              masked={settings?.openrouter_api_key ?? ''}
              value={openrouterKey}
              onChange={setOpenrouterKey}
              placeholder="sk-or-..."
            />
            <div>
              <label>Model</label>
              <input type="text" value={openrouterModel} onChange={(e) => setOpenrouterModel(e.target.value)} />
            </div>
          </>
        )}
        {llmProvider === 'orcarouter' && (
          <>
            <SecretField
              key={settings?.orcarouter_api_key ?? ''}
              label="OrcaRouter API Key"
              masked={settings?.orcarouter_api_key ?? ''}
              value={orcarouterKey}
              onChange={setOrcarouterKey}
              placeholder="orca-..."
            />
            <div>
              <label>Model</label>
              <input type="text" value={orcarouterModel} onChange={(e) => setOrcarouterModel(e.target.value)} />
              <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
                Default <code>orcarouter/auto</code> lets the gateway pick a model per request. Or pin one, e.g.{' '}
                <code>openai/gpt-4o-mini</code>, <code>anthropic/claude-opus-4.7</code>.
              </div>
            </div>
          </>
        )}
        {llmProvider === 'openai' && (
          <SecretField
            key={settings?.openai_api_key ?? ''}
            label="OpenAI API Key"
            masked={settings?.openai_api_key ?? ''}
            value={openaiKey}
            onChange={setOpenaiKey}
            placeholder="sk-..."
          />
        )}
        {llmProvider === 'gemini' && (
          <SecretField
            key={settings?.gemini_api_key ?? ''}
            label="Gemini API Key"
            masked={settings?.gemini_api_key ?? ''}
            value={geminiKey}
            onChange={setGeminiKey}
          />
        )}
        {llmProvider === 'claude_code_cli' && (
          <div className="text-muted" style={{ fontSize: 13 }}>
            Best-effort option: shells out to your local Claude Code CLI. No key needed, but no SLA either — higher
            latency than a direct API and depends on the CLI being installed and logged in on this machine.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={saveLlm} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button className="btn btn-secondary" onClick={() => testLlm('llm')} disabled={testingLlm}>
            {testingLlm ? 'Testing…' : 'Test Connection'}
          </button>
        </div>
        {llmTestResult && (
          <div className={llmTestResult.ok ? 'text-green' : 'text-red'} style={{ fontSize: 13 }}>
            {llmTestResult.message}
          </div>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>AI Trading Overlay</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Every trade decision above is made by deterministic, rule-based math — technicals, fundamentals, and news
          scored by named thresholds, never by an LLM. Turning this on does NOT change that: it adds a second,
          independent read from the AI provider above, shown alongside the rule-based decision — never blended into
          it, and never able to override the direction or confidence score. The AI sees the same raw data (price,
          volume, indicators, fundamentals, full news headlines, earnings date) and gives its own stance and
          reasoning, which can agree or disagree with the rule-based verdict.
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Costs one extra AI call per symbol evaluated — including ones the rule-based engine rejects as "no trade,"
          which normally skip the AI entirely to save tokens. Requires a real provider selected above (has no effect
          while Provider is "None").
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="checkbox"
            checked={aiOverlayEnabled}
            onChange={(e) => setAiOverlayEnabled(e.target.checked)}
            style={{ width: 'auto' }}
          />
          Enable AI second opinion on every evaluation
        </label>
        <button className="btn btn-primary" onClick={saveAiOverlay} disabled={saving} style={{ alignSelf: 'flex-start' }}>
          Save
        </button>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Finnhub (optional, free tier)</h3>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={finnhubEnabled} onChange={(e) => setFinnhubEnabled(e.target.checked)} style={{ width: 'auto' }} />
          Enable Finnhub for quotes/news/earnings
        </label>
        {finnhubEnabled && (
          <SecretField
            key={settings?.finnhub_api_key ?? ''}
            label="Finnhub API Key"
            masked={settings?.finnhub_api_key ?? ''}
            value={finnhubKey}
            onChange={setFinnhubKey}
          />
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={saveFinnhub} disabled={saving}>
            Save
          </button>
          {finnhubEnabled && (
            <button className="btn btn-secondary" onClick={() => testFinnhub('finnhub')} disabled={testingFinnhub}>
              {testingFinnhub ? 'Testing…' : 'Test Connection'}
            </button>
          )}
        </div>
        {finnhubTestResult && (
          <div className={finnhubTestResult.ok ? 'text-green' : 'text-red'} style={{ fontSize: 13 }}>
            {finnhubTestResult.message}
          </div>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Telegram Notifications</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Get a Telegram message whenever a new AI trade plan is generated, and an alert if the AI provider, Finnhub,
          or market-data connection goes offline or comes back (checked on the same interval as mark-to-market,
          below). Create a bot via <code>@BotFather</code>, then message your new bot once and check{' '}
          <code>https://api.telegram.org/bot&lt;token&gt;/getUpdates</code> for your chat ID.
        </div>
        <SecretField
          key={settings?.telegram_bot_token ?? ''}
          label="Bot Token"
          masked={settings?.telegram_bot_token ?? ''}
          value={telegramToken}
          onChange={setTelegramToken}
          placeholder="123456:ABC-..."
        />
        <div>
          <label>Chat ID</label>
          <input type="text" value={telegramChatId} onChange={(e) => setTelegramChatId(e.target.value)} placeholder="e.g. 123456789" />
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={saveTelegram} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button className="btn btn-secondary" onClick={() => testTelegram('telegram')} disabled={testingTelegram}>
            {testingTelegram ? 'Testing…' : 'Test Connection'}
          </button>
        </div>
        {telegramTestResult && (
          <div className={telegramTestResult.ok ? 'text-green' : 'text-red'} style={{ fontSize: 13 }}>
            {telegramTestResult.message}
          </div>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Paper Account</h3>
        <div>
          <label>Starting Cash ($)</label>
          <input type="number" value={startingCash} onChange={(e) => setStartingCash(Number(e.target.value))} />
        </div>
        <div>
          <label>Default Risk per Trade (%)</label>
          <input type="number" value={defaultRiskPct} onChange={(e) => setDefaultRiskPct(Number(e.target.value))} step={0.1} />
        </div>
        <div>
          <label>Scan Universe Size</label>
          <select value={scanSize} onChange={(e) => setScanSize(Number(e.target.value))}>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={64}>Full bundled list (64, incl. BTC/ETH/SOL)</option>
          </select>
        </div>
        <div>
          <label>Auto-Execute Trade Plans</label>
          <select value={autoExecute ? 'enabled' : 'disabled'} onChange={(e) => setAutoExecute(e.target.value === 'enabled')}>
            <option value="enabled">Enabled — open a paper position the moment a plan is generated</option>
            <option value="disabled">Disabled — review each plan and click Execute manually</option>
          </select>
        </div>
        <button className="btn btn-primary" onClick={saveAccount} disabled={saving} style={{ alignSelf: 'flex-start' }}>
          Save
        </button>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Starting cash only takes effect for a fresh paper account — use "Reset Paper Account" on the Portfolio page
          to apply a changed value to an account that already has history.
        </div>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Unattended Auto-Scan</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Closes the full loop: 3 times a day (Asia, London, and New York session opens), fully evaluates every
          symbol in the bundled universe — price, volume, indicators, fundamentals, news, earnings — and either
          generates (and, if Auto-Execute above is on, opens) a trade plan or explicitly records "no trade" with a
          reason. Off by default; unlike Auto-Execute, this decides which symbols to trade with no human in the loop
          at all.
        </div>
        <div>
          <label>Auto-Scan</label>
          <select value={autoScanEnabled ? 'enabled' : 'disabled'} onChange={(e) => setAutoScanEnabled(e.target.value === 'enabled')}>
            <option value="disabled">Disabled — scan and generate plans manually</option>
            <option value="enabled">Enabled — scan and trade on a schedule, unattended</option>
          </select>
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Schedule is fixed at 00:00, 08:00, and 13:30 UTC (Asia/London/New York session opens) — not configurable
          per-account, see the scheduler.
        </div>
        <div>
          <label>Max Concurrent Open Positions</label>
          <select value={maxConcurrentPositions} onChange={(e) => setMaxConcurrentPositions(Number(e.target.value))}>
            <option value={1}>1</option>
            <option value={3}>3</option>
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={20}>20</option>
          </select>
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            Auto-scan keeps evaluating and proposing plans past this cap — it stops AUTO-EXECUTING new ones once open
            positions reach it, the guardrail that keeps unattended scanning from opening unlimited simultaneous
            risk. Applies to auto-scan only, not manual "Execute Trade Plan" clicks.
          </div>
        </div>
        <button className="btn btn-primary" onClick={saveAutomation} disabled={saving} style={{ alignSelf: 'flex-start' }}>
          Save
        </button>
      </div>
    </div>
  );
}

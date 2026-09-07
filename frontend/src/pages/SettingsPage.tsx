import { useEffect, useState } from 'react';
import { useSettings, useTestConnection, useUpdateSettings } from '../api/hooks';
import { LoadingSpinner } from '../components/common';

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
  const { mutate: testConnection, data: testResult, isPending: testing } = useTestConnection();

  const [llmProvider, setLlmProvider] = useState('none');
  const [openrouterKey, setOpenrouterKey] = useState('');
  const [openrouterModel, setOpenrouterModel] = useState('');
  const [orcarouterKey, setOrcarouterKey] = useState('');
  const [orcarouterModel, setOrcarouterModel] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [geminiKey, setGeminiKey] = useState('');
  const [finnhubEnabled, setFinnhubEnabled] = useState(false);
  const [finnhubKey, setFinnhubKey] = useState('');
  const [startingCash, setStartingCash] = useState(100_000);
  const [defaultRiskPct, setDefaultRiskPct] = useState(1);
  const [scanSize, setScanSize] = useState(50);

  useEffect(() => {
    if (!settings) return;
    setLlmProvider(settings.llm_provider);
    setOpenrouterModel(settings.openrouter_model);
    setOrcarouterModel(settings.orcarouter_model);
    setFinnhubEnabled(settings.finnhub_enabled);
    setStartingCash(settings.paper_starting_cash);
    setDefaultRiskPct(settings.default_risk_pct);
    setScanSize(settings.scan_universe_size);
  }, [settings]);

  function saveLlm() {
    update({
      llm_provider: llmProvider,
      ...(openrouterKey ? { openrouter_api_key: openrouterKey } : {}),
      openrouter_model: openrouterModel,
      ...(orcarouterKey ? { orcarouter_api_key: orcarouterKey } : {}),
      orcarouter_model: orcarouterModel,
      ...(openaiKey ? { openai_api_key: openaiKey } : {}),
      ...(geminiKey ? { gemini_api_key: geminiKey } : {}),
    });
  }

  function saveFinnhub() {
    update({ finnhub_enabled: finnhubEnabled, ...(finnhubKey ? { finnhub_api_key: finnhubKey } : {}) });
  }

  function saveAccount() {
    update({ paper_starting_cash: startingCash, default_risk_pct: defaultRiskPct, scan_universe_size: scanSize });
  }

  if (isLoading) return <LoadingSpinner label="Loading settings…" />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 640 }}>
      <h1 style={{ fontSize: 22 }}>Settings</h1>

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
            <div>
              <label>OpenRouter API Key {settings?.openrouter_api_key && '(configured — leave blank to keep)'}</label>
              <input type="text" value={openrouterKey} onChange={(e) => setOpenrouterKey(e.target.value)} placeholder="sk-or-..." />
            </div>
            <div>
              <label>Model</label>
              <input type="text" value={openrouterModel} onChange={(e) => setOpenrouterModel(e.target.value)} />
            </div>
          </>
        )}
        {llmProvider === 'orcarouter' && (
          <>
            <div>
              <label>OrcaRouter API Key {settings?.orcarouter_api_key && '(configured — leave blank to keep)'}</label>
              <input type="text" value={orcarouterKey} onChange={(e) => setOrcarouterKey(e.target.value)} placeholder="orca-..." />
            </div>
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
          <div>
            <label>OpenAI API Key {settings?.openai_api_key && '(configured — leave blank to keep)'}</label>
            <input type="text" value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)} placeholder="sk-..." />
          </div>
        )}
        {llmProvider === 'gemini' && (
          <div>
            <label>Gemini API Key {settings?.gemini_api_key && '(configured — leave blank to keep)'}</label>
            <input type="text" value={geminiKey} onChange={(e) => setGeminiKey(e.target.value)} />
          </div>
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
          <button className="btn btn-secondary" onClick={() => testConnection('llm')} disabled={testing}>
            {testing ? 'Testing…' : 'Test Connection'}
          </button>
        </div>
        {testResult && (
          <div className={testResult.ok ? 'text-green' : 'text-red'} style={{ fontSize: 13 }}>
            {testResult.message}
          </div>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Finnhub (optional, free tier)</h3>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={finnhubEnabled} onChange={(e) => setFinnhubEnabled(e.target.checked)} style={{ width: 'auto' }} />
          Enable Finnhub for quotes/news/earnings
        </label>
        {finnhubEnabled && (
          <div>
            <label>Finnhub API Key {settings?.finnhub_api_key && '(configured — leave blank to keep)'}</label>
            <input type="text" value={finnhubKey} onChange={(e) => setFinnhubKey(e.target.value)} />
          </div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={saveFinnhub} disabled={saving}>
            Save
          </button>
          {finnhubEnabled && (
            <button className="btn btn-secondary" onClick={() => testConnection('finnhub')} disabled={testing}>
              Test Connection
            </button>
          )}
        </div>
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
            <option value={60}>Full bundled list (60)</option>
          </select>
        </div>
        <button className="btn btn-primary" onClick={saveAccount} disabled={saving} style={{ alignSelf: 'flex-start' }}>
          Save
        </button>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Starting cash only takes effect for a fresh paper account (before any trades are recorded).
        </div>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from 'react';
import { useSettings, useTestConnection, useUpdateSettings, useWatchlist } from '../api/hooks';
import { LoadingSpinner, ToggleSwitch } from '../components/common';
import { SecretField } from '../components/SecretField';
import { DecisionModelField } from '../components/DecisionModelField';
import { decisionModelInvalid } from '../lib/decisionModel';
import { DataCacheCard } from '../components/DataCacheCard';
import { DataSourcesCard } from '../components/DataSourcesCard';
import { WatchersCard } from '../components/WatchersCard';
import { NotificationsSettingsCard } from '../components/NotificationsSettingsCard';
import { CongressFollowCard } from '../components/CongressFollowCard';
import { FundFollowCard } from '../components/FundFollowCard';
import { NewsCardsSettingsCard } from '../components/NewsCardsSettingsCard';
import { KillSwitchSettingsCard } from '../components/KillSwitchSettingsCard';
import { OnboardingCard } from '../components/OnboardingCard';
import { CommitteeSettingsCard } from '../components/CommitteeSettingsCard';
import { ThesisAlertsSettingsCard } from '../components/ThesisAlertsSettingsCard';
import { WatchlistCard } from '../components/WatchlistCard';
import type { AiOverlayObjectionAction, ResearchMode, TestConnectionOverrides } from '../api/types';

const LLM_OPTIONS = [
  { value: 'none', label: 'None (rule-based text)' },
  { value: 'claude_code_cli', label: 'Claude Code CLI (uses your existing login, no key)' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'orcarouter', label: 'OrcaRouter' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'gemini', label: 'Google Gemini' },
];

/** Which providers have a real web-search mechanism for research calls (mirrors each
 *  backend provider's `supports_web_search`). OrcaRouter documents none. */
const WEB_SEARCH_PROVIDERS = new Set(['claude_code_cli', 'openrouter', 'openai', 'gemini']);

const RESEARCH_MODES: { value: ResearchMode; label: string; help: string }[] = [
  {
    value: 'our_data_only',
    label: 'Our data only',
    help: 'Research answers use only the data this app already holds (prices, fundamentals, filings, news it has fetched). Nothing is searched on the web.',
  },
  {
    value: 'allow_web_search',
    label: 'Allow web search',
    help: 'Research answers may search the web and must cite the pages they used. Web pages are untrusted text, and the AI is told never to follow instructions found in them. Uses more of your AI quota or credit.',
  },
];

/** Mirrors backend config.CLAUDE_CLI_MODEL_PATTERN (a plain model name: starts with a letter or digit). */
const CLAUDE_MODEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:@[\]-]*$/;

/** Save -> Saving… -> a brief green "Saved" confirmation -> back to Save.
 * One shared component so every card's save button behaves and looks the
 * same, instead of each card silently going back to plain "Save" with no
 * feedback that anything happened. */
function SaveButton({
  pending,
  justSaved,
  onClick,
  disabled,
}: {
  pending: boolean;
  justSaved: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="btn btn-primary"
      onClick={onClick}
      disabled={pending || disabled}
      style={{
        transition: 'background-color 200ms ease, border-color 200ms ease',
        ...(justSaved ? { background: 'var(--green)', borderColor: 'var(--green)' } : {}),
      }}
    >
      {pending ? 'Saving…' : justSaved ? '✓ Saved' : 'Save'}
    </button>
  );
}

/** Small at-a-glance ON/OFF pill so an enabled/disabled setting doesn't
 * depend on reading a dropdown's full sentence to register — same visual
 * language as the sidebar's online/offline status pills. */
/** `inactive` is for a sub-setting whose master switch is off: the setting
 * itself is still ON, but nothing it controls can happen. Rendering "OFF"
 * there flatly contradicted the toggle beside it, which is still drawn in
 * its true (green, on) position — two controls describing the same state
 * two different ways. */
/** Alternatives, not a ladder of independent switches: "cancel" and "hold"
 *  trigger on the identical condition and cancel strictly wins, so as two
 *  booleans one combination was always dead code and the UI could show a
 *  setting as ON that could never fire once. */
const OVERLAY_ACTIONS: { value: AiOverlayObjectionAction; label: string; help: string }[] = [
  {
    value: 'cancel',
    label: 'Cancel the trade',
    help: 'Strongest. The evaluation becomes an explicit No Trade with the AI named as the reason, instead of a plan. Nothing is left to execute.',
  },
  {
    value: 'hold',
    label: 'Hold it for manual review',
    help: 'The plan is written normally with the rule-based direction, but auto-execute leaves it Pending instead of opening the position. You decide.',
  },
  {
    value: 'none',
    label: 'Nothing — just record it',
    help: 'The objection is shown on the plan (and still costs confidence, if that is on above) but stops nothing. Use this to find out whether the AI is actually right before letting it block trades.',
  },
];

function OnOffBadge({ on, inactive }: { on: boolean; inactive?: boolean }) {
  if (inactive) {
    return (
      <span className="badge badge-neutral" title="Saved as on, but has no effect while the switch above is off.">
        INACTIVE
      </span>
    );
  }
  return <span className={`badge ${on ? 'badge-green' : 'badge-neutral'}`}>{on ? 'ON' : 'OFF'}</span>;
}

/** Discards whatever's been typed/toggled in this card and reverts it to
 * the last-saved server values — a safety net for "I've been fiddling with
 * this and want to bail out without saving." */
function ResetButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="btn btn-secondary" onClick={onClick}>
      Reset
    </button>
  );
}

export function SettingsPage() {
  const { data: settings, isLoading } = useSettings();
  const { data: watchlistInfo } = useWatchlist();
  const { mutate: update, isPending: saving } = useUpdateSettings();
  const { mutate: testLlm, data: llmTestResult, isPending: testingLlm, reset: resetLlmTest } = useTestConnection();
  const { mutate: testFinnhub, data: finnhubTestResult, isPending: testingFinnhub, reset: resetFinnhubTest } = useTestConnection();
  const { mutate: testTelegram, data: telegramTestResult, isPending: testingTelegram, reset: resetTelegramTest } = useTestConnection();

  const [llmProvider, setLlmProvider] = useState('none');
  const [openrouterKey, setOpenrouterKey] = useState('');
  const [openrouterModel, setOpenrouterModel] = useState('');
  const [orcarouterKey, setOrcarouterKey] = useState('');
  const [orcarouterModel, setOrcarouterModel] = useState('');
  const [claudeCliModel, setClaudeCliModel] = useState('');
  const [openaiModel, setOpenaiModel] = useState('');
  const [geminiModel, setGeminiModel] = useState('');
  // Decision model per provider (the AI overlay's verdict); blank = the provider's routine model.
  const [claudeDecisionModel, setClaudeDecisionModel] = useState('');
  const [openrouterDecisionModel, setOpenrouterDecisionModel] = useState('');
  const [orcarouterDecisionModel, setOrcarouterDecisionModel] = useState('');
  const [openaiDecisionModel, setOpenaiDecisionModel] = useState('');
  const [geminiDecisionModel, setGeminiDecisionModel] = useState('');
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
  const [minConfidence, setMinConfidence] = useState(30);
  const [markToMarketMinutes, setMarkToMarketMinutes] = useState(15);
  const [maxHoldingDays, setMaxHoldingDays] = useState(20);
  const [aiOverlayEnabled, setAiOverlayEnabled] = useState(false);
  const [aiOverlayScores, setAiOverlayScores] = useState(true);
  const [aiOverlayAction, setAiOverlayAction] = useState<AiOverlayObjectionAction>('cancel');
  const [researchMode, setResearchMode] = useState<ResearchMode>('our_data_only');

  const [justSavedKey, setJustSavedKey] = useState<string | null>(null);
  const flashTimeout = useRef<number | undefined>(undefined);
  function flashSaved(key: string) {
    window.clearTimeout(flashTimeout.current);
    setJustSavedKey(key);
    flashTimeout.current = window.setTimeout(() => setJustSavedKey((cur) => (cur === key ? null : cur)), 1800);
  }

  // Bumped on every Reset so each SecretField (keyed on `${masked}-${resetNonce}`)
  // is forced to remount even when the masked hint itself didn't change —
  // otherwise a field mid-"Replace" (its own local editing state, not
  // tracked here) wouldn't visibly snap back to showing the saved value.
  const [resetNonce, setResetNonce] = useState(0);

  // "Check for empty" before Save is even clickable — a provider/integration
  // left half-configured shouldn't be one click away from a false "Saved".
  const llmNeedsKey = ['openrouter', 'orcarouter', 'openai', 'gemini'].includes(llmProvider);
  const llmDraftKey = { openrouter: openrouterKey, orcarouter: orcarouterKey, openai: openaiKey, gemini: geminiKey }[llmProvider] ?? '';
  const llmExistingKey =
    ({ openrouter: settings?.openrouter_api_key, orcarouter: settings?.orcarouter_api_key, openai: settings?.openai_api_key, gemini: settings?.gemini_api_key }[
      llmProvider
    ] as string | undefined) ?? '';
  // Same rule as the backend (config.normalize_claude_cli_model): blank is fine
  // (= don't pin), otherwise a plain model name. The backend re-checks it.
  const claudeModelInvalid =
    llmProvider === 'claude_code_cli' &&
    claudeCliModel.trim() !== '' &&
    !(CLAUDE_MODEL_PATTERN.test(claudeCliModel.trim()) && claudeCliModel.trim().length <= 64);
  const llmMissingKey = llmNeedsKey && !llmDraftKey && !llmExistingKey;
  const decisionModelBad =
    (llmProvider === 'claude_code_cli' && decisionModelInvalid('claude', claudeDecisionModel)) ||
    (llmProvider === 'openrouter' && decisionModelInvalid('api', openrouterDecisionModel)) ||
    (llmProvider === 'orcarouter' && decisionModelInvalid('api', orcarouterDecisionModel)) ||
    (llmProvider === 'openai' && (decisionModelInvalid('api', openaiDecisionModel) || decisionModelInvalid('api', openaiModel))) ||
    (llmProvider === 'gemini' && (decisionModelInvalid('gemini', geminiDecisionModel) || decisionModelInvalid('gemini', geminiModel)));

  const finnhubMissingKey = finnhubEnabled && !finnhubKey && !settings?.finnhub_api_key;

  const telegramHasToken = !!(telegramToken || settings?.telegram_bot_token);
  const telegramHasChatId = !!telegramChatId;
  const telegramIncomplete = telegramHasToken !== telegramHasChatId; // exactly one set — the other is required too

  useEffect(() => {
    if (!settings) return;
    setLlmProvider(settings.llm_provider);
    setOpenrouterModel(settings.openrouter_model);
    setOrcarouterModel(settings.orcarouter_model);
    setClaudeCliModel(settings.claude_cli_model);
    setOpenaiModel(settings.openai_model);
    setGeminiModel(settings.gemini_model);
    setClaudeDecisionModel(settings.claude_cli_decision_model);
    setOpenrouterDecisionModel(settings.openrouter_decision_model);
    setOrcarouterDecisionModel(settings.orcarouter_decision_model);
    setOpenaiDecisionModel(settings.openai_decision_model);
    setGeminiDecisionModel(settings.gemini_decision_model);
    setFinnhubEnabled(settings.finnhub_enabled);
    setTelegramChatId(settings.telegram_chat_id);
    setStartingCash(settings.paper_starting_cash);
    setDefaultRiskPct(settings.default_risk_pct);
    setScanSize(settings.scan_universe_size);
    setAutoExecute(settings.auto_execute_trade_plans);
    setAutoScanEnabled(settings.auto_scan_enabled);
    setMaxConcurrentPositions(settings.max_concurrent_positions);
    setMinConfidence(settings.min_confidence_for_trade);
    setMarkToMarketMinutes(settings.mark_to_market_interval_minutes);
    setMaxHoldingDays(settings.max_holding_days);
    setAiOverlayEnabled(settings.ai_trading_overlay_enabled);
    setAiOverlayScores(settings.ai_overlay_scores_confidence);
    setAiOverlayAction(settings.ai_overlay_objection_action);
    setResearchMode(settings.research_mode ?? 'our_data_only');
  }, [settings]);

  // What the test buttons send: the values in the form right now, saved or not.
  // A secret is only included once something was typed into it (an untouched
  // field is the masked saved key, which the backend treats as "use the saved one").
  function testOverridesLlm(): TestConnectionOverrides {
    const o: TestConnectionOverrides = { llm_provider: llmProvider };
    if (llmProvider === 'claude_code_cli') {
      o.claude_cli_model = claudeCliModel.trim();
      o.claude_cli_decision_model = claudeDecisionModel.trim();
    } else if (llmProvider === 'openrouter') {
      if (openrouterKey) o.openrouter_api_key = openrouterKey;
      o.openrouter_model = openrouterModel.trim();
      o.openrouter_decision_model = openrouterDecisionModel.trim();
    } else if (llmProvider === 'orcarouter') {
      if (orcarouterKey) o.orcarouter_api_key = orcarouterKey;
      o.orcarouter_model = orcarouterModel.trim();
      o.orcarouter_decision_model = orcarouterDecisionModel.trim();
    } else if (llmProvider === 'openai') {
      if (openaiKey) o.openai_api_key = openaiKey;
      o.openai_model = openaiModel.trim();
      o.openai_decision_model = openaiDecisionModel.trim();
    } else if (llmProvider === 'gemini') {
      if (geminiKey) o.gemini_api_key = geminiKey;
      o.gemini_model = geminiModel.trim();
      o.gemini_decision_model = geminiDecisionModel.trim();
    }
    return o;
  }
  const testOverridesFinnhub = (): TestConnectionOverrides => (finnhubKey ? { finnhub_api_key: finnhubKey } : {});
  const testOverridesTelegram = (): TestConnectionOverrides => ({
    ...(telegramToken ? { telegram_bot_token: telegramToken } : {}),
    telegram_chat_id: telegramChatId,
  });

  function saveLlm() {
    if (llmMissingKey || claudeModelInvalid || decisionModelBad) return;
    update(
      {
        llm_provider: llmProvider,
        research_mode: researchMode,
        claude_cli_model: claudeCliModel.trim(),
        claude_cli_decision_model: claudeDecisionModel.trim(),
        openrouter_decision_model: openrouterDecisionModel.trim(),
        orcarouter_decision_model: orcarouterDecisionModel.trim(),
        openai_model: openaiModel.trim(),
        openai_decision_model: openaiDecisionModel.trim(),
        gemini_model: geminiModel.trim(),
        gemini_decision_model: geminiDecisionModel.trim(),
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
          // Don't call it "Saved" until we've actually confirmed the
          // provider works — a green checkmark next to "not configured"
          // (e.g. picking claude_code_cli with no CLI on this machine, or a
          // bad key) is worse than no confirmation at all.
          testLlm('llm', { onSuccess: (result) => result.ok && flashSaved('llm') });
        },
      },
    );
  }

  function saveFinnhub() {
    if (finnhubMissingKey) return;
    update(
      { finnhub_enabled: finnhubEnabled, ...(finnhubKey ? { finnhub_api_key: finnhubKey } : {}) },
      {
        onSuccess: () => {
          setFinnhubKey('');
          if (finnhubEnabled) {
            testFinnhub('finnhub', { onSuccess: (result) => result.ok && flashSaved('finnhub') });
          } else {
            flashSaved('finnhub'); // turning it off always "works" — nothing to verify
          }
        },
      },
    );
  }

  function saveTelegram() {
    if (telegramIncomplete) return;
    update(
      {
        telegram_chat_id: telegramChatId,
        ...(telegramToken ? { telegram_bot_token: telegramToken } : {}),
      },
      {
        onSuccess: () => {
          setTelegramToken('');
          if (telegramHasToken && telegramHasChatId) {
            testTelegram('telegram', { onSuccess: (result) => result.ok && flashSaved('telegram') });
          } else {
            flashSaved('telegram'); // both cleared — intentionally off, nothing to verify
          }
        },
      },
    );
  }

  function saveAccount() {
    update(
      {
        paper_starting_cash: startingCash,
        default_risk_pct: defaultRiskPct,
        scan_universe_size: scanSize,
        auto_execute_trade_plans: autoExecute,
        min_confidence_for_trade: minConfidence,
        mark_to_market_interval_minutes: markToMarketMinutes,
        max_holding_days: maxHoldingDays,
      },
      { onSuccess: () => flashSaved('account') },
    );
  }

  function saveAutomation() {
    update(
      {
        auto_scan_enabled: autoScanEnabled,
        max_concurrent_positions: maxConcurrentPositions,
      },
      { onSuccess: () => flashSaved('automation') },
    );
  }

  function saveAiOverlay() {
    update(
      {
        ai_trading_overlay_enabled: aiOverlayEnabled,
        ai_overlay_scores_confidence: aiOverlayScores,
        ai_overlay_objection_action: aiOverlayAction,
      },
      { onSuccess: () => flashSaved('ai-overlay') },
    );
  }

  function resetLlm() {
    if (!settings) return;
    setLlmProvider(settings.llm_provider);
    setOpenrouterModel(settings.openrouter_model);
    setOrcarouterModel(settings.orcarouter_model);
    setClaudeCliModel(settings.claude_cli_model);
    setOpenaiModel(settings.openai_model);
    setGeminiModel(settings.gemini_model);
    setClaudeDecisionModel(settings.claude_cli_decision_model);
    setOpenrouterDecisionModel(settings.openrouter_decision_model);
    setOrcarouterDecisionModel(settings.orcarouter_decision_model);
    setOpenaiDecisionModel(settings.openai_decision_model);
    setGeminiDecisionModel(settings.gemini_decision_model);
    setResearchMode(settings.research_mode ?? 'our_data_only');
    setOpenrouterKey('');
    setOrcarouterKey('');
    setOpenaiKey('');
    setGeminiKey('');
    setResetNonce((n) => n + 1);
    resetLlmTest(); // discard any test result for whatever config we're abandoning
  }

  function resetFinnhub() {
    if (!settings) return;
    setFinnhubEnabled(settings.finnhub_enabled);
    setFinnhubKey('');
    setResetNonce((n) => n + 1);
    resetFinnhubTest();
  }

  function resetTelegram() {
    if (!settings) return;
    setTelegramChatId(settings.telegram_chat_id);
    setTelegramToken('');
    setResetNonce((n) => n + 1);
    resetTelegramTest();
  }

  function resetAccount() {
    if (!settings) return;
    setStartingCash(settings.paper_starting_cash);
    setDefaultRiskPct(settings.default_risk_pct);
    setScanSize(settings.scan_universe_size);
    setAutoExecute(settings.auto_execute_trade_plans);
    setMinConfidence(settings.min_confidence_for_trade);
    setMarkToMarketMinutes(settings.mark_to_market_interval_minutes);
    setMaxHoldingDays(settings.max_holding_days);
  }

  function resetAutomation() {
    if (!settings) return;
    setAutoScanEnabled(settings.auto_scan_enabled);
    setMaxConcurrentPositions(settings.max_concurrent_positions);
  }

  function resetAiOverlay() {
    if (!settings) return;
    setAiOverlayEnabled(settings.ai_trading_overlay_enabled);
    setAiOverlayScores(settings.ai_overlay_scores_confidence);
    setAiOverlayAction(settings.ai_overlay_objection_action);
  }

  if (isLoading) return <LoadingSpinner label="Loading settings…" />;

  return (
    <div className="settings-columns">
      <h1 style={{ fontSize: 22 }}>Settings</h1>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>AI Narrative Provider</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Which AI writes the plain-English commentary on charts, research cards and trade plans. This is narration
          only — it describes numbers the rule-based engine already computed and never decides a signal. Leaving this
          on "None" is fully supported: every screen falls back to rule-based wording and nothing breaks. It is also
          the provider the AI Trading Overlay below uses, and the overlay stays inert until a real one is selected
          here.
        </div>
        <div>
          <label>Provider</label>
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 4 }}>
            <code>Claude Code CLI</code> shells out to the <code>claude</code> command already installed on this
            machine — no API key and no metered billing. The others are REST APIs and need a key below.
          </div>
          <select
            value={llmProvider}
            onChange={(e) => {
              setLlmProvider(e.target.value);
              // A stale "claude_code_cli is not configured" left over from
              // testing the *previous* provider must not linger under a
              // now-different selection — it reads as describing this one.
              resetLlmTest();
            }}
          >
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
              key={`${settings?.openrouter_api_key ?? ''}-${resetNonce}`}
              label="OpenRouter API Key"
              masked={settings?.openrouter_api_key ?? ''}
              value={openrouterKey}
              onChange={setOpenrouterKey}
              placeholder="sk-or-..."
            />
            <div>
              <label>Model</label>
              <input type="text" value={openrouterModel} onChange={(e) => setOpenrouterModel(e.target.value)} />
              <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
                Any model slug OpenRouter lists, e.g. <code>anthropic/claude-3.5-haiku</code>. These calls are short
                and frequent, so a small fast model is usually the right trade-off over a frontier one.
              </div>
            </div>
            <DecisionModelField kind="api" value={openrouterDecisionModel} onChange={setOpenrouterDecisionModel} routineModel={openrouterModel} />
          </>
        )}
        {llmProvider === 'orcarouter' && (
          <>
            <SecretField
              key={`${settings?.orcarouter_api_key ?? ''}-${resetNonce}`}
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
            <DecisionModelField kind="api" value={orcarouterDecisionModel} onChange={setOrcarouterDecisionModel} routineModel={orcarouterModel} />
          </>
        )}
        {llmProvider === 'openai' && (
          <>
            <SecretField
              key={`${settings?.openai_api_key ?? ''}-${resetNonce}`}
              label="OpenAI API Key"
              masked={settings?.openai_api_key ?? ''}
              value={openaiKey}
              onChange={setOpenaiKey}
              placeholder="sk-..."
            />
            <div>
              <label>Model</label>
              <input type="text" value={openaiModel} onChange={(e) => setOpenaiModel(e.target.value)} placeholder="gpt-4o-mini" maxLength={96} spellCheck={false} autoComplete="off" />
            </div>
            <DecisionModelField kind="api" value={openaiDecisionModel} onChange={setOpenaiDecisionModel} routineModel={openaiModel} />
          </>
        )}
        {llmProvider === 'gemini' && (
          <>
            <SecretField
              key={`${settings?.gemini_api_key ?? ''}-${resetNonce}`}
              label="Gemini API Key"
              masked={settings?.gemini_api_key ?? ''}
              value={geminiKey}
              onChange={setGeminiKey}
            />
            <div>
              <label>Model</label>
              <input type="text" value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)} placeholder="gemini-1.5-flash" maxLength={96} spellCheck={false} autoComplete="off" />
            </div>
            <DecisionModelField kind="gemini" value={geminiDecisionModel} onChange={setGeminiDecisionModel} routineModel={geminiModel} />
          </>
        )}
        {llmProvider === 'claude_code_cli' && (
          <div>
            <label>Model</label>
            <input
              type="text"
              value={claudeCliModel}
              onChange={(e) => setClaudeCliModel(e.target.value)}
              placeholder="sonnet"
              maxLength={64}
              spellCheck={false}
              autoComplete="off"
            />
            <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
              The model every call is pinned to, so it can't change underneath you. Use an alias —{' '}
              <code>sonnet</code>, <code>opus</code> or <code>haiku</code> (always the latest of that family) — or a
              full id such as <code>claude-sonnet-5-5</code> for an exact version. Leave it blank to not pin:{' '}
              <strong>blank means "whatever the CLI is set to"</strong> (the model last picked in an interactive{' '}
              <code>claude</code> session, or your account default), which can change without you noticing.
            </div>
            {claudeModelInvalid && (
              <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
                Use letters, digits and . _ - : @ [ ] only (max 64), starting with a letter or digit.
              </div>
            )}
          </div>
        )}
        {llmProvider === 'claude_code_cli' && (
          <DecisionModelField kind="claude" value={claudeDecisionModel} onChange={setClaudeDecisionModel} routineModel={claudeCliModel} />
        )}
        {llmProvider === 'claude_code_cli' && (
          <div className="text-muted" style={{ fontSize: 13 }}>
            Best-effort option: shells out to your local Claude Code CLI. No key needed, but no SLA either — higher
            latency than a direct API and depends on the CLI being installed and logged in on this machine.
            <strong> Running the backend in Docker?</strong> This can never come online there — the container has no
            access to your machine's CLI or its login, by design (that's the whole reason it needs no key). Either
            run the backend directly with <code>uvicorn</code> instead of Docker, or pick a key-based provider above.
          </div>
        )}
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>Research mode</div>
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
            Applies only to AI calls that gather background for research pages. The AI Trading Overlay and the
            narration never use the web, in either mode.
          </div>
          {RESEARCH_MODES.map((opt) => (
            <label key={opt.value} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 10, cursor: 'pointer' }}>
              <input
                type="radio"
                name="research-mode"
                value={opt.value}
                checked={researchMode === opt.value}
                onChange={() => setResearchMode(opt.value)}
                style={{ width: 'auto', marginTop: 3 }}
              />
              <span>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{opt.label}</span>
                <span className="text-muted" style={{ fontSize: 12, display: 'block', marginTop: 2 }}>
                  {opt.help}
                </span>
              </span>
            </label>
          ))}
          <div className={WEB_SEARCH_PROVIDERS.has(llmProvider) ? 'text-green' : 'text-muted'} style={{ fontSize: 12 }} data-testid="research-web-support">
            {llmProvider === 'none'
              ? 'No AI provider is selected, so there is nothing to search the web with.'
              : WEB_SEARCH_PROVIDERS.has(llmProvider)
                ? 'The selected provider supports web search.'
                : 'The selected provider has no web search, so research answers will use our data only.'}
          </div>
        </div>
        {llmMissingKey && (
          <div className="text-red" style={{ fontSize: 12 }}>
            Enter an API key above before saving — {LLM_OPTIONS.find((o) => o.value === llmProvider)?.label} needs one.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <SaveButton pending={saving || testingLlm} justSaved={justSavedKey === 'llm'} onClick={saveLlm} disabled={llmMissingKey || claudeModelInvalid || decisionModelBad} />
          <ResetButton onClick={resetLlm} />
          <button className="btn btn-secondary" onClick={() => testLlm({ target: 'llm', overrides: testOverridesLlm() })}
            disabled={testingLlm || claudeModelInvalid || decisionModelBad}
            title="Tests what is in this form right now, saved or not"
          >
            {testingLlm ? 'Testing…' : 'Test Connection'}
          </button>
          {llmProvider !== 'none' && (
            <button
              className="btn btn-secondary"
              onClick={() => testLlm({ target: 'llm', tier: 'decision', overrides: testOverridesLlm() })}
              disabled={testingLlm || claudeModelInvalid || decisionModelBad}
              title="Tests the decision model in this form (the routine one when it is blank), saved or not"
            >
              Test decision model
            </button>
          )}
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
          The setup itself is still decided by deterministic, rule-based math — technicals, fundamentals and news
          scored by named thresholds. The AI never picks the direction, entry, stop or position size, and it can
          never talk the engine into a trade the rules rejected. What it can do is argue against one: it sees the
          same raw data (price, volume, indicators, fundamentals, full news headlines, earnings date) and answers
          two separate questions — where it thinks the stock goes, and whether it would actually take this trade.
          The second answer is the one acted on, and when it is no, the plan loses confidence and can be stopped
          outright.
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Costs one extra AI call per symbol evaluated — including ones the rule-based engine rejects as "no trade,"
          which normally skip the AI entirely to save tokens. Requires a real provider selected above (has no effect
          while Provider is "None").
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          This call uses the provider's <strong>Decision model</strong> (set in the card above; blank = the same model as
          the narration), so you can spend a stronger model on the one answer that can stop a trade.
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ToggleSwitch checked={aiOverlayEnabled} onChange={setAiOverlayEnabled} label="Enable AI second opinion on every evaluation" />
          <span>Enable AI second opinion on every evaluation</span>
          <OnOffBadge on={aiOverlayEnabled} />
        </div>

        <div
          style={{
            borderLeft: '2px solid var(--border)',
            paddingLeft: 14,
            marginLeft: 4,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            opacity: aiOverlayEnabled ? 1 : 0.45,
          }}
        >
          <div className="text-muted" style={{ fontSize: 12 }}>
            How much its opinion counts. Both settings below do nothing while the overlay above is off.
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <ToggleSwitch
                checked={aiOverlayScores}
                onChange={setAiOverlayScores}
                disabled={!aiOverlayEnabled}
                label="Count disagreement in the confidence score"
              />
              <span>Count disagreement in the confidence score</span>
              <OnOffBadge on={aiOverlayScores} inactive={!aiOverlayEnabled} />
            </div>
            <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
              An objection subtracts up to 3 points (scaled by how sure the AI says it is), shown as "AI Overlay"
              in the plan's score breakdown. Agreement is deliberately worth 0 — the AI is shown the rule-based
              verdict before it answers, so agreeing with it proves little.
            </div>
          </div>

          <div>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>When the AI says not to take the trade</div>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
              Pick one. These are alternatives, not extras — cancelling and holding react to the same moment, and a
              cancelled trade never reaches auto-execute, so only one of them can ever actually happen.
            </div>
            {OVERLAY_ACTIONS.map((opt) => (
              <label
                key={opt.value}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  marginBottom: 10,
                  cursor: aiOverlayEnabled ? 'pointer' : 'default',
                }}
              >
                <input
                  type="radio"
                  name="ai-overlay-objection-action"
                  value={opt.value}
                  checked={aiOverlayAction === opt.value}
                  disabled={!aiOverlayEnabled}
                  onChange={() => setAiOverlayAction(opt.value)}
                  style={{ width: 'auto', marginTop: 3 }}
                />
                <span>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{opt.label}</span>
                  <span className="text-muted" style={{ fontSize: 12, display: 'block', marginTop: 2 }}>
                    {opt.help}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <SaveButton pending={saving} justSaved={justSavedKey === 'ai-overlay'} onClick={saveAiOverlay} />
          <ResetButton onClick={resetAiOverlay} />
        </div>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Finnhub (optional, free tier)</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          A fourth market-data source, off by default because the app does not need it: quotes, fundamentals and news
          already come from Yahoo, with Nasdaq, Stooq and SEC EDGAR behind it. Worth enabling mainly as extra
          redundancy during a Yahoo outage. The free tier cannot serve full financials history or intraday candles,
          so those keep coming from the other providers regardless.
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={finnhubEnabled} onChange={(e) => setFinnhubEnabled(e.target.checked)} style={{ width: 'auto' }} />
          Enable Finnhub for quotes/news/earnings
          <OnOffBadge on={finnhubEnabled} />
        </label>
        {finnhubEnabled && (
          <SecretField
            key={`${settings?.finnhub_api_key ?? ''}-${resetNonce}`}
            label="Finnhub API Key"
            masked={settings?.finnhub_api_key ?? ''}
            value={finnhubKey}
            onChange={setFinnhubKey}
          />
        )}
        {finnhubMissingKey && (
          <div className="text-red" style={{ fontSize: 12 }}>
            Enter a Finnhub API key above before saving, or turn this off first.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <SaveButton pending={saving || testingFinnhub} justSaved={justSavedKey === 'finnhub'} onClick={saveFinnhub} disabled={finnhubMissingKey} />
          <ResetButton onClick={resetFinnhub} />
          {finnhubEnabled && (
            <button className="btn btn-secondary" onClick={() => testFinnhub({ target: 'finnhub', overrides: testOverridesFinnhub() })}
              disabled={testingFinnhub}
              title="Tests what is in this form right now, saved or not"
            >
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
          key={`${settings?.telegram_bot_token ?? ''}-${resetNonce}`}
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
        {telegramIncomplete && (
          <div className="text-red" style={{ fontSize: 12 }}>
            {telegramHasToken ? 'Enter a Chat ID too' : 'Enter a Bot Token too'} — Telegram needs both to send notifications.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <SaveButton pending={saving || testingTelegram} justSaved={justSavedKey === 'telegram'} onClick={saveTelegram} disabled={telegramIncomplete} />
          <ResetButton onClick={resetTelegram} />
          <button className="btn btn-secondary" onClick={() => testTelegram({ target: 'telegram', overrides: testOverridesTelegram() })}
            disabled={testingTelegram || telegramIncomplete}
            title="Tests what is in this form right now, saved or not"
          >
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
        <div className="text-muted" style={{ fontSize: 13 }}>
          Simulated money only. Positions are priced against real market data, but no broker is connected and no
          order is ever placed anywhere.
        </div>
        <div>
          <label>Starting Cash ($)</label>
          <input type="number" value={startingCash} onChange={(e) => setStartingCash(Number(e.target.value))} />
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            The account's opening balance, and the cash the position sizer is allowed to deploy. A plan asking for
            more shares than the balance covers is trimmed to what the account can actually fund.
          </div>
        </div>
        <div>
          <label>Default Risk per Trade (%)</label>
          <input type="number" value={defaultRiskPct} onChange={(e) => setDefaultRiskPct(Number(e.target.value))} step={0.1} />
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            How much of the account a single trade is allowed to lose if its stop is hit — this is what decides the
            share count, working backwards from the distance between entry and stop. 1% is the conventional swing
            default: a wider stop buys fewer shares, so the dollar risk stays fixed regardless of the setup.
          </div>
        </div>
        <div>
          <label>Minimum Confidence to Trade (%)</label>
          <input
            type="number"
            value={minConfidence}
            min={0}
            max={100}
            onChange={(e) => setMinConfidence(Number(e.target.value))}
          />
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            The bar a setup has to clear to become a tradeable plan at all — below it the symbol is still fully
            evaluated and recorded, as an explicit "No Trade" with its reason, rather than skipped. Confidence is the
            share of the engine's 16 evidence points a setup earned, so it moves in ~6% steps: the default 30% means
            5 points. Raise it for fewer, higher-conviction plans; lower it to see more marginal setups.
          </div>
        </div>
        <div>
          <label>Scan Universe Size</label>
          <select value={scanSize} onChange={(e) => setScanSize(Number(e.target.value))}>
            {[...new Set([25, 50, 100, 200, scanSize])]
              .sort((a, b) => a - b)
              .map((n) => (
                <option key={n} value={n}>
                  {n}
                  {n === 200 ? ' (maximum)' : ''}
                </option>
              ))}
          </select>
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            How many symbols from the top of the Watchlist (card below) a market scan looks at. Every symbol costs
            several provider requests, so a smaller universe is faster and much less likely to hit a free-tier rate
            limit.
            {watchlistInfo && (
              <>
                {' '}
                Your watchlist has {watchlistInfo.entries.length} symbol{watchlistInfo.entries.length === 1 ? '' : 's'}:
                {scanSize >= watchlistInfo.entries.length
                  ? ' all of them are scanned.'
                  : ` scanning the first ${scanSize} of ${watchlistInfo.entries.length}; the rest are skipped.`}
              </>
            )}
          </div>
        </div>
        <div>
          <label>Mark-to-Market Interval (minutes)</label>
          <input
            type="number"
            value={markToMarketMinutes}
            min={1}
            onChange={(e) => setMarkToMarketMinutes(Number(e.target.value))}
          />
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            How often open positions are repriced and checked for a stop or target hit, and how often the
            connection-health alerts above run. Exit checks walk every bar since entry, so a longer interval delays
            when a close is recorded but never causes one to be missed. Runs on a fixed clock, not only during
            market hours.
          </div>
        </div>
        <div>
          <label>Maximum Holding Time (trading days)</label>
          <input
            type="number"
            value={maxHoldingDays}
            min={0}
            max={60}
            step={1}
            onChange={(e) => setMaxHoldingDays(Number(e.target.value))}
          />
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            A third way out after the stop and the first target: a position still open after this many trading days
            (weekends and holidays don't count) is closed at that day's close, as a market order, so it pays
            slippage like a stop. It only fires if neither the stop nor TP1 was touched that day. Trade plans are
            labelled 1-4 weeks, so the default 20 (four weeks) is the end of that horizon; a position that stalls
            past it ties up one of your slots and a sector cap. 0 turns the limit off. Applies to positions that
            are already open, so lowering it can close stalled ones at the next check.
          </div>
        </div>
        <div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            Auto-Execute Trade Plans
            <OnOffBadge on={autoExecute} />
          </label>
          <select value={autoExecute ? 'enabled' : 'disabled'} onChange={(e) => setAutoExecute(e.target.value === 'enabled')}>
            <option value="enabled">Enabled — open a paper position the moment a plan is generated</option>
            <option value="disabled">Disabled — review each plan and click Execute manually</option>
          </select>
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            Whether a generated plan turns itself into an open paper position without you clicking Execute. Applies
            to plans you generate by hand as well as ones from Auto-Scan.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <SaveButton pending={saving} justSaved={justSavedKey === 'account'} onClick={saveAccount} />
          <ResetButton onClick={resetAccount} />
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          Starting cash only takes effect for a fresh paper account — use "Reset Paper Account" on the Portfolio page
          to apply a changed value to an account that already has history.
        </div>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Unattended Auto-Scan</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Closes the full loop: 3 times a day (Asia, London, and New York session opens), fully evaluates every
          symbol in the scan universe (the top of your Watchlist) — price, volume, indicators, fundamentals, news, earnings — and either
          generates (and, if Auto-Execute above is on, opens) a trade plan or explicitly records "no trade" with a
          reason. Off by default; unlike Auto-Execute, this decides which symbols to trade with no human in the loop
          at all.
        </div>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <ToggleSwitch checked={autoScanEnabled} onChange={setAutoScanEnabled} label="Auto-Scan" />
            <span>Auto-Scan</span>
            <OnOffBadge on={autoScanEnabled} />
          </div>
          <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
            {autoScanEnabled ? 'Enabled — scan and trade on a schedule, unattended.' : 'Disabled — scan and generate plans manually.'}
          </div>
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
        <div style={{ display: 'flex', gap: 8 }}>
          <SaveButton pending={saving} justSaved={justSavedKey === 'automation'} onClick={saveAutomation} />
          <ResetButton onClick={resetAutomation} />
        </div>
      </div>

      <NotificationsSettingsCard />

      <WatchersCard />

      <NewsCardsSettingsCard />
      <ThesisAlertsSettingsCard />
      <OnboardingCard />
      <KillSwitchSettingsCard />
      <CommitteeSettingsCard />

      <CongressFollowCard />

      <FundFollowCard />

      <WatchlistCard />

      <DataSourcesCard />

      <DataCacheCard />
    </div>
  );
}

import { useState } from 'react';
import { useOnboardingSuggestion, useSettings, useUpdateSettings } from '../api/hooks';

const QUESTIONS = [
  {
    key: 'onboarding_risk_tolerance' as const,
    label: 'How do you feel about losing money on a trade?',
    options: [
      ['low', 'I dislike it a lot'],
      ['medium', 'I can take normal losses'],
      ['high', 'I am comfortable with big swings'],
    ],
  },
  {
    key: 'onboarding_time_horizon' as const,
    label: 'How long do you want to hold a trade?',
    options: [
      ['days', 'A few days'],
      ['weeks', 'One to four weeks'],
      ['months', 'Months'],
    ],
  },
  {
    key: 'onboarding_experience' as const,
    label: 'How much trading experience do you have?',
    options: [
      ['beginner', 'Beginner'],
      ['intermediate', 'Some'],
      ['advanced', 'A lot'],
    ],
  },
];

/** Three questions that suggest a style and a risk %. Answers are saved; nothing else changes until Apply is pressed. */
export function OnboardingCard() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const suggestion = useOnboardingSuggestion();
  const [applied, setApplied] = useState(false);

  const answered = !!settings && QUESTIONS.every((q) => settings[q.key]);
  const result = suggestion.data;

  const suggest = () => {
    if (!settings) return;
    setApplied(false);
    suggestion.mutate({
      risk_tolerance: settings.onboarding_risk_tolerance,
      time_horizon: settings.onboarding_time_horizon,
      experience: settings.onboarding_experience,
    });
  };

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="onboarding-card">
      <h3>Suggested starting style</h3>
      <div className="text-muted" style={{ fontSize: 13, maxWidth: 640 }}>
        Answer three questions to get a suggested trading style and risk per trade. It is only a suggestion: nothing
        changes until you press Apply.
      </div>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        {QUESTIONS.map((q) => (
          <label key={q.key} style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
            {q.label}
            <select
              value={settings?.[q.key] ?? ''}
              disabled={!settings || update.isPending}
              onChange={(e) => update.mutate({ [q.key]: e.target.value })}
            >
              <option value="">Choose...</option>
              {q.options.map(([v, text]) => (
                <option key={v} value={v}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div>
        <button type="button" disabled={!answered || suggestion.isPending} onClick={suggest}>
          Suggest
        </button>
      </div>
      {result && (
        <div data-testid="onboarding-result" style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div>
            Suggested style: <strong>{result.style}</strong>. {result.style_description}
          </div>
          <div>
            Suggested risk per trade: <strong>{result.risk_pct}%</strong>
            {settings && <span className="text-muted"> (currently {settings.default_risk_pct}%)</span>}
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {result.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <div>
            <button
              type="button"
              disabled={update.isPending || applied}
              onClick={() => update.mutate({ default_risk_pct: result.risk_pct }, { onSuccess: () => setApplied(true) })}
            >
              {applied ? 'Applied' : `Apply ${result.risk_pct}% risk`}
            </button>
          </div>
        </div>
      )}
      {(update.error || suggestion.error) && (
        <span className="text-red" style={{ fontSize: 13 }}>
          {(update.error ?? suggestion.error)?.message}
        </span>
      )}
    </div>
  );
}

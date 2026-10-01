// Client-side check for the decision-model inputs in Settings.
// Field note: these mirror the backend's config.normalize_claude_cli_model /
// normalize_api_model; the backend re-checks on save.

export type DecisionModelKind = 'claude' | 'api' | 'gemini';

// A plain model id that starts with a letter
// or digit. Gateways use "vendor/model" ids, so "/" is allowed except for Gemini,
// whose id becomes part of a URL path.
export const PATTERNS: Record<DecisionModelKind, { pattern: RegExp; maxLength: number }> = {
  claude: { pattern: /^[A-Za-z0-9][A-Za-z0-9._:@[\]-]*$/, maxLength: 64 },
  api: { pattern: /^[A-Za-z0-9][A-Za-z0-9._:@[\]/-]*$/, maxLength: 96 },
  gemini: { pattern: /^[A-Za-z0-9][A-Za-z0-9._:@[\]-]*$/, maxLength: 96 },
};

export function decisionModelInvalid(kind: DecisionModelKind, value: string): boolean {
  const v = value.trim();
  if (v === '') return false;
  const { pattern, maxLength } = PATTERNS[kind];
  return !(pattern.test(v) && v.length <= maxLength);
}


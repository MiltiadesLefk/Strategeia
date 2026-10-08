// The "Decision model (AI overlay)" input shown under each AI provider's model
// field in Settings. Blank means the provider's routine model answers the AI
// overlay too, which is also what every existing install does.
import { PATTERNS, decisionModelInvalid, type DecisionModelKind } from '../lib/decisionModel';

interface Props {
  kind: DecisionModelKind;
  value: string;
  onChange: (value: string) => void;
  /** The routine model, shown as the placeholder so "blank" visibly means that one. */
  routineModel: string;
  placeholder?: string;
}

export function DecisionModelField({ kind, value, onChange, routineModel, placeholder }: Props) {
  const invalid = decisionModelInvalid(kind, value);
  return (
    <div>
      <label>Decision model (optional, for the AI overlay and committee verdicts)</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? (routineModel.trim() ? `same as above (${routineModel.trim()})` : 'same as above')}
        maxLength={PATTERNS[kind].maxLength}
        spellCheck={false}
        autoComplete="off"
      />
      <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
        Answers the AI overlay's "would you take this trade?" question (the one call that can stop a trade) and the
        AI Committee's manager, trader, risk and final-rating steps. It can be a stronger model than the everyday one
        above. <strong>Blank = use the same model.</strong>
      </div>
      {invalid && (
        <div className="text-red" style={{ fontSize: 12, marginTop: 4 }}>
          Use letters, digits and . _ - : @ [ ]{kind === 'gemini' ? '' : ' /'} only, starting with a letter or digit.
        </div>
      )}
    </div>
  );
}

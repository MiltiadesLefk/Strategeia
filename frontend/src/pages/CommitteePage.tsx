import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCommitteeRun, useCommitteeRuns, useStartCommitteeRun } from '../api/hooks';
import type { CommitteeRun, CommitteeStep } from '../api/types';
import './committee.css';
import { CompanyDropdown } from '../components/CompanyDropdown';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from '../components/common';

const RATING_CHIP: Record<string, string> = {
  Buy: 'chip chip-green',
  Overweight: 'chip chip-green',
  Hold: 'chip',
  Underweight: 'chip chip-amber',
  Sell: 'chip chip-red',
};
const RATING_BIG: Record<string, string> = {
  Buy: 'r-green',
  Overweight: 'r-green',
  Hold: 'r-neutral',
  Underweight: 'r-amber',
  Sell: 'r-red',
};

const STATUS_LABEL: Record<CommitteeStep['status'], string> = {
  pending: 'waiting',
  running: 'writing…',
  done: 'done',
  skipped: 'skipped',
  failed: 'failed',
};
const STATUS_CHIP: Record<CommitteeStep['status'], string> = {
  pending: 'chip',
  running: 'chip chip-amber',
  done: 'chip chip-green',
  skipped: 'chip',
  failed: 'chip chip-red',
};

const STAGES: { role: CommitteeStep['role'][]; label: string }[] = [
  { role: ['analyst'], label: 'Analysts' },
  { role: ['debate'], label: 'Bull and bear debate' },
  { role: ['manager', 'trader'], label: 'Research manager and trader' },
  { role: ['risk'], label: 'Risk debate' },
  { role: ['final'], label: 'Final rating' },
];

function stageState(steps: CommitteeStep[]): 'pending' | 'running' | 'done' | 'failed' {
  if (steps.some((s) => s.status === 'failed')) return 'failed';
  if (steps.some((s) => s.status === 'running')) return 'running';
  if (steps.length > 0 && steps.every((s) => s.status === 'done' || s.status === 'skipped')) return 'done';
  return 'pending';
}

function StepCard({ step }: { step: CommitteeStep }) {
  const quiet = step.status === 'pending' || step.status === 'skipped';
  return (
    <details
      className="cm-step"
      style={{ opacity: quiet ? 0.7 : 1 }}
      open={step.status === 'running' || undefined}
      data-testid={`committee-step-${step.key}`}
    >
      <summary>
        <strong>
          {step.title}
          {step.round > 0 ? ` (round ${step.round})` : ''}
        </strong>
        <span className={STATUS_CHIP[step.status]}>{STATUS_LABEL[step.status]}</span>
        <span className="label-mono">
          {step.tier === 'decision' ? 'decision model' : 'routine model'}
          {step.model ? ` · ${step.model}` : ''}
        </span>
      </summary>
      <div className="cm-step-body">
        {step.text && (
          <div className="cm-step-text">
            <span className="chip chip-ai">AI NOTE</span>
            <div>{step.text}</div>
          </div>
        )}
        {step.error && (
          <div className="text-red" style={{ fontSize: 13, marginTop: 6 }}>
            {step.error}
          </div>
        )}
        {step.note && (
          <div className="text-muted" style={{ fontSize: 13, marginTop: 6 }}>
            {step.note}
          </div>
        )}
        {step.ungrounded.length > 0 && (
          <div style={{ fontSize: 12, marginTop: 6, color: 'var(--amber)' }}>
            Figures not found in the data given (check them before relying on this text): {step.ungrounded.join('; ')}
          </div>
        )}
        {!step.text && !step.error && !step.note && <div className="text-muted">Nothing written yet.</div>}
      </div>
    </details>
  );
}

function RatingCard({ run }: { run: CommitteeRun }) {
  const active = run.status === 'queued' || run.status === 'running';
  return (
    <div className="card ai-note" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="committee-rating">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div>
          <span className="chip chip-ai">AI NOTE</span>
          <h2 style={{ margin: '6px 0 0', fontSize: 18 }}>
            Committee rating for <span className="mono">{run.symbol}</span>
          </h2>
        </div>
        {run.rating && <span className={`cm-rating ${RATING_BIG[run.rating]}`}>{run.rating}</span>}
      </div>
      {run.rating ? (
        <>
          <div className="label-mono">
            {run.rating_conviction && <>conviction: {run.rating_conviction}</>}
            {run.rating_parse === 'lenient' && <> · reply read leniently</>}
          </div>
          {run.rating_summary && <div style={{ whiteSpace: 'pre-wrap' }}>{run.rating_summary}</div>}
          {run.rating_key_risks && (
            <div>
              <strong>Key risks: </strong>
              {run.rating_key_risks}
            </div>
          )}
        </>
      ) : active ? (
        <div className="text-muted">The committee is still working. The rating appears here when the last step is done.</div>
      ) : (
        <div className="text-muted">No rating was recorded for this run.</div>
      )}
      {run.error && (
        <div className="text-red" style={{ fontSize: 13 }}>
          {run.error}
        </div>
      )}
      <div className="label-mono" style={{ textTransform: 'none' }}>
        AI calls used: {run.llm_calls_used} of {run.llm_calls_max} allowed · debate rounds {run.debate_rounds}, risk rounds{' '}
        {run.risk_rounds}
        {run.provider ? ` · ${run.provider}` : ''}
        {run.web_search ? ' · web search used by the analysts' : ''}
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        {run.note}
      </div>
    </div>
  );
}

export function LiveRun({ id }: { id: number }) {
  const run = useCommitteeRun(id);
  if (run.isLoading) return <LoadingSpinner />;
  if (run.error || !run.data) return <ErrorBanner message={run.error?.message ?? 'Could not load this run'} onRetry={() => run.refetch()} />;
  const data = run.data;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="card">
        <div className="panel-header">
          <span className="label-mono">Deliberation pipeline</span>
        </div>
        <div className="cm-stages">
          {STAGES.map((stage, i) => {
            const st = stageState(data.steps.filter((s) => stage.role.includes(s.role)));
            const chip = st === 'done' ? 'chip chip-green' : st === 'running' ? 'chip chip-amber' : st === 'failed' ? 'chip chip-red' : 'chip';
            return (
              <div key={stage.label} className={`cm-stage is-${st}`}>
                <span className="label-mono">Stage {String(i + 1).padStart(2, '0')}</span>
                <div className="cm-stage-title">{stage.label}</div>
                <span className={chip}>{st === 'pending' ? 'waiting' : st}</span>
              </div>
            );
          })}
        </div>
      </div>
      <RatingCard run={data} />
      {STAGES.map((stage) => {
        const steps = data.steps.filter((s) => stage.role.includes(s.role));
        if (steps.length === 0) return null;
        return (
          <section key={stage.label} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="label-mono">{stage.label}</div>
            {steps.map((s) => (
              <StepCard key={s.key} step={s} />
            ))}
          </section>
        );
      })}
    </div>
  );
}

export function CommitteePage() {
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState((params.get('symbol') ?? '').toUpperCase());
  const selected = params.get('run') ? Number(params.get('run')) : null;
  const runs = useCommitteeRuns();
  const start = useStartCommitteeRun();

  function select(id: number | null) {
    const next = new URLSearchParams(params);
    if (id === null) next.delete('run');
    else next.set('run', String(id));
    setParams(next, { replace: true });
  }

  function submit() {
    const symbol = draft.trim().toUpperCase();
    if (!symbol) return;
    start.mutate(symbol, { onSuccess: (run) => select(run.id) });
  }

  const busy = Boolean(runs.data?.some((r) => r.status === 'queued' || r.status === 'running'));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="cm-banner">
        <span>Opinion only - never trades</span>
        <span className="cm-banner-sub">The AI Committee does not open, size or stop a position, and the rules engine never reads it.</span>
      </div>
      <div>
        <h1 className="page-title" style={{ margin: 0 }}>
          AI Committee
        </h1>
        <div className="text-muted" style={{ fontSize: 13, maxWidth: 760 }}>
          Analysts write reports, a bull and a bear argue, a trader leans one way, a risk panel pushes back, and a final step gives a
          five-level rating from Buy to Sell. Each run uses several calls on your AI provider (capped in Settings).
        </div>
      </div>

      <div className="card cm-run">
        <CompanyDropdown value={draft} onChange={setDraft} />
        <button className="btn" disabled={!draft.trim() || busy || start.isPending} onClick={submit}>
          {busy ? 'A run is in progress…' : 'Convene the committee'}
        </button>
        {start.error && (
          <span className="text-red" style={{ fontSize: 13 }}>
            {start.error.message}
          </span>
        )}
      </div>

      <div className="cm-layout">
        <div>{selected !== null ? <LiveRun id={selected} /> : <EmptyState>Start a run, or open one from the history.</EmptyState>}</div>
        <div className="card" style={{ overflowX: 'auto' }}>
          <div className="panel-header">
            <span className="label-mono">History</span>
          </div>
          {runs.isLoading ? (
            <LoadingSpinner />
          ) : !runs.data || runs.data.length === 0 ? (
            <div className="text-muted">No committee runs yet.</div>
          ) : (
            <table className="cm-hist">
              <thead>
                <tr>
                  <th className="label-mono">When</th>
                  <th className="label-mono">Symbol</th>
                  <th className="label-mono">Rating</th>
                  <th className="label-mono">Calls</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id} className={r.id === selected ? 'is-sel' : undefined}>
                    <td>{formatRelativeTime(r.created_at)}</td>
                    <td className="mono">{r.symbol}</td>
                    <td>
                      {r.rating ? (
                        <span className={RATING_CHIP[r.rating]}>{r.rating}</span>
                      ) : (
                        <span className="text-muted">{r.status === 'done' ? 'none' : r.status}</span>
                      )}
                    </td>
                    <td className="mono">
                      {r.llm_calls_used}/{r.llm_calls_max}
                    </td>
                    <td>
                      <button className="btn btn-secondary" onClick={() => select(r.id)} aria-label={`Open run ${r.id}`}>
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

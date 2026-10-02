import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCommitteeRun, useCommitteeRuns, useStartCommitteeRun } from '../api/hooks';
import type { CommitteeRun, CommitteeStep } from '../api/types';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from '../components/common';

const RATING_CLASS: Record<string, string> = {
  Buy: 'badge badge-green',
  Overweight: 'badge badge-green',
  Hold: 'badge badge-neutral',
  Underweight: 'badge badge-amber',
  Sell: 'badge badge-red',
};

const STATUS_LABEL: Record<CommitteeStep['status'], string> = {
  pending: 'waiting',
  running: 'writing…',
  done: 'done',
  skipped: 'skipped',
  failed: 'failed',
};
const STATUS_CLASS: Record<CommitteeStep['status'], string> = {
  pending: 'badge badge-neutral',
  running: 'badge badge-amber',
  done: 'badge badge-green',
  skipped: 'badge badge-neutral',
  failed: 'badge badge-red',
};

const STAGES: { role: CommitteeStep['role'][]; label: string }[] = [
  { role: ['analyst'], label: '1. Analysts' },
  { role: ['debate'], label: '2. Bull and bear debate' },
  { role: ['manager', 'trader'], label: '3. Research manager and trader' },
  { role: ['risk'], label: '4. Risk debate' },
  { role: ['final'], label: '5. Final rating' },
];

function StepCard({ step }: { step: CommitteeStep }) {
  const quiet = step.status === 'pending' || step.status === 'skipped';
  return (
    <div className="card" style={{ background: 'var(--card-alt)', opacity: quiet ? 0.7 : 1 }} data-testid={`committee-step-${step.key}`}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>
          {step.title}
          {step.round > 0 ? ` (round ${step.round})` : ''}
        </strong>
        <span className={STATUS_CLASS[step.status]}>{STATUS_LABEL[step.status]}</span>
        <span className="text-muted" style={{ fontSize: 12 }}>
          {step.tier === 'decision' ? 'decision model' : 'routine model'}
          {step.model ? ` · ${step.model}` : ''}
        </span>
      </div>
      {step.text && <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, marginTop: 8 }}>{step.text}</div>}
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
    </div>
  );
}

function RatingCard({ run }: { run: CommitteeRun }) {
  const active = run.status === 'queued' || run.status === 'running';
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }} data-testid="committee-rating">
      <h2 style={{ margin: 0, fontSize: 18 }}>Committee rating for {run.symbol}</h2>
      {run.rating ? (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span className={RATING_CLASS[run.rating]} style={{ fontSize: 20, padding: '4px 14px' }}>
              {run.rating}
            </span>
            {run.rating_conviction && <span className="text-muted">conviction: {run.rating_conviction}</span>}
            {run.rating_parse === 'lenient' && <span className="text-muted">(reply read leniently)</span>}
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
      <div className="text-muted" style={{ fontSize: 12 }}>
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

function LiveRun({ id }: { id: number }) {
  const run = useCommitteeRun(id);
  if (run.isLoading) return <LoadingSpinner />;
  if (run.error || !run.data) return <ErrorBanner message={run.error?.message ?? 'Could not load this run'} onRetry={() => run.refetch()} />;
  const data = run.data;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <RatingCard run={data} />
      {STAGES.map((stage) => {
        const steps = data.steps.filter((s) => stage.role.includes(s.role));
        if (steps.length === 0) return null;
        return (
          <section key={stage.label} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>{stage.label}</h3>
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h1 style={{ margin: 0 }}>AI Committee</h1>
          <div className="text-muted" style={{ fontSize: 14, maxWidth: 760 }}>
            A panel of AI roles reads one symbol: analysts write reports, a bull and a bear argue, a trader leans one way, a
            risk panel pushes back, and a final step gives a five-level rating from Buy to Sell. It is an opinion only. It never
            opens, sizes or stops a trade, and the rules engine never reads it. Each run uses several calls on your AI provider
            (capped in Settings).
          </div>
        </div>
      </div>

      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          aria-label="Symbol"
          placeholder="Ticker, e.g. NVDA"
          value={draft}
          maxLength={12}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === 'Enter' && !busy && submit()}
          style={{ width: 160 }}
        />
        <button className="btn" disabled={!draft.trim() || busy || start.isPending} onClick={submit}>
          {busy ? 'A run is in progress…' : 'Convene the committee'}
        </button>
        {start.error && (
          <span className="text-red" style={{ fontSize: 13 }}>
            {start.error.message}
          </span>
        )}
      </div>

      {selected !== null ? <LiveRun id={selected} /> : <EmptyState>Start a run, or open one from the history below.</EmptyState>}

      <div className="card" style={{ overflowX: 'auto' }}>
        <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>History</h2>
        {runs.isLoading ? (
          <LoadingSpinner />
        ) : !runs.data || runs.data.length === 0 ? (
          <div className="text-muted">No committee runs yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Symbol</th>
                <th>Rating</th>
                <th>Status</th>
                <th>AI calls</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id}>
                  <td>{formatRelativeTime(r.created_at)}</td>
                  <td>{r.symbol}</td>
                  <td>{r.rating ? <span className={RATING_CLASS[r.rating]}>{r.rating}</span> : <span className="text-muted">none</span>}</td>
                  <td>{r.status}</td>
                  <td>
                    {r.llm_calls_used} / {r.llm_calls_max}
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
  );
}

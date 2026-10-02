import { useEffect, useState } from 'react';
import { useSettings, useUpdateSettings } from '../api/hooks';
import {
  useActivateForecastModel,
  useCreateMlSleeve,
  useDeactivateForecastModel,
  useDeleteForecastModel,
  useForecastModel,
  useForecastModels,
  useForecastPredictions,
  useForecastStatus,
  useTrainForecastModel,
} from '../api/forecastHooks';
import type { ForecastMean, ForecastModelSummary } from '../api/forecastTypes';
import { EmptyState, ErrorBanner, LoadingSpinner, ToggleSwitch, formatRelativeTime } from '../components/common';

const VERDICT_TEXT: Record<string, { label: string; cls: string }> = {
  edge_out_of_sample: { label: 'Out-of-sample edge', cls: 'badge badge-green' },
  no_clear_edge: { label: 'No clear edge', cls: 'badge badge-amber' },
  inverted: { label: 'Inverted', cls: 'badge badge-red' },
  not_enough_data: { label: 'Not enough data', cls: 'badge badge-neutral' },
};

function VerdictBadge({ verdict }: { verdict: string }) {
  const v = VERDICT_TEXT[verdict] ?? VERDICT_TEXT.not_enough_data;
  return <span className={v.cls}>{v.label}</span>;
}

const fmt = (v: number | null | undefined, digits = 2) => (v == null ? 'n/a' : v.toFixed(digits));

function MeanCell({ label, m }: { label: string; m?: ForecastMean }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        {label}
      </div>
      <div style={{ fontWeight: 600 }}>
        {m ? `${fmt(m.mean)}R` : 'n/a'} <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>n={m?.n ?? 0}</span>
      </div>
      {m && m.low != null && m.high != null && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          95% interval {fmt(m.low)} to {fmt(m.high)}
        </div>
      )}
    </div>
  );
}

function ModelDetail({ id }: { id: number }) {
  const { data: model, isLoading, error } = useForecastModel(id);
  if (isLoading) return <LoadingSpinner />;
  if (error || !model) return <ErrorBanner message={error?.message ?? 'Could not load the model'} />;
  const m = model.metrics;
  const maxShap = Math.max(...model.importance.map((i) => i.mean_abs_shap), 1e-9);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }} data-testid="forecast-model-detail">
      <div className="text-muted" style={{ fontSize: 13 }}>
        {model.split.n_train} train, {model.split.n_validation} validation, {model.split.n_test} test trades (validation from{' '}
        {String(model.split.validation_start)}, test from {String(model.split.test_start)}; {model.split.purged_rows} dropped
        because their result was not yet known when the next window began). The test window was used once, after the model was
        chosen.
      </div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        <div>
          <div className="text-muted" style={{ fontSize: 12 }}>
            Rank correlation (IC) of prediction and result
          </div>
          <div style={{ fontWeight: 600 }}>
            {fmt(m.ic?.ic)} <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>n={m.ic?.n ?? 0}</span>
          </div>
          {m.ic?.low != null && m.ic?.high != null && (
            <div className="text-muted" style={{ fontSize: 12 }}>
              95% interval {fmt(m.ic.low)} to {fmt(m.ic.high)}
            </div>
          )}
        </div>
        <MeanCell label="All test trades" m={m.all_test_trades} />
        <MeanCell label="Model would keep" m={m.kept_by_model} />
        <MeanCell label="Model would stop" m={m.stopped_by_model} />
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Test error {fmt(m.test_l2, 3)} against {fmt(m.test_baseline_l2, 3)} for always guessing the training average (lower is
        better).
      </div>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>What the model leans on (mean absolute SHAP, test window)</div>
        {model.importance.map((i) => (
          <div key={i.feature} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 3 }}>
            <span style={{ width: 200 }}>{i.feature}</span>
            <div style={{ background: 'var(--accent, #3b82f6)', height: 8, borderRadius: 4, width: `${(i.mean_abs_shap / maxShap) * 200}px` }} />
            <span className="text-muted">{i.mean_abs_shap.toFixed(3)}</span>
          </div>
        ))}
      </div>
      {model.notes.map((n) => (
        <div key={n} className="text-muted" style={{ fontSize: 13 }}>
          {n}
        </div>
      ))}
    </div>
  );
}

function ModelRow({
  model,
  selected,
  onSelect,
}: {
  model: ForecastModelSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const activate = useActivateForecastModel();
  const deactivate = useDeactivateForecastModel();
  const remove = useDeleteForecastModel();
  return (
    <tr style={selected ? { background: 'var(--bg-hover, rgba(127,127,127,0.1))' } : undefined}>
      <td>#{model.id}</td>
      <td>{formatRelativeTime(model.created_at)}</td>
      <td>runs {model.run_ids.join(', ')}</td>
      <td>
        {model.n_rows} ({model.n_test} test)
      </td>
      <td>
        <VerdictBadge verdict={model.verdict} />
      </td>
      <td>{model.active ? <span className="badge badge-green">active</span> : ''}</td>
      <td style={{ display: 'flex', gap: 6 }}>
        <button type="button" className="btn" onClick={onSelect}>
          Details
        </button>
        {model.active ? (
          <button type="button" className="btn" disabled={deactivate.isPending} onClick={() => deactivate.mutate()}>
            Deactivate
          </button>
        ) : (
          <button type="button" className="btn btn-primary" disabled={activate.isPending} onClick={() => activate.mutate(model.id)}>
            Activate
          </button>
        )}
        <button type="button" className="btn" disabled={remove.isPending || model.active} onClick={() => remove.mutate(model.id)}>
          Delete
        </button>
      </td>
    </tr>
  );
}

export function ForecastLabPage() {
  const { data: status, isLoading, error } = useForecastStatus();
  const { data: models } = useForecastModels();
  const { data: predictions } = useForecastPredictions();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const train = useTrainForecastModel();
  const createSleeve = useCreateMlSleeve();
  const [picked, setPicked] = useState<number[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [limit, setLimit] = useState('');

  useEffect(() => {
    if (settings) setLimit(String(settings.ml_min_expected_r));
  }, [settings]);

  useEffect(() => {
    if (train.data) setSelected(train.data.id);
  }, [train.data]);

  if (isLoading) return <LoadingSpinner />;
  if (error || !status) return <ErrorBanner message={error?.message ?? 'Could not load the Forecast Lab'} />;

  const togglePick = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const limitNum = Number(limit);
  const limitValid = limit !== '' && limitNum >= -2 && limitNum <= 3;
  const pickedRows = status.runs.filter((r) => picked.includes(r.id)).reduce((a, r) => a + r.closed_trades, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h2 style={{ margin: 0, fontSize: 20 }}>Forecast Lab</h2>
          <div className="text-muted" style={{ fontSize: 13, maxWidth: 720 }}>
            A small gradient-boosted model learns, from finished backtest trades, which setups ended well in R. SHAP shows what
            drove each opinion. It is an opinion only: the rules still choose direction, entry, stop and size, and the model can
            only stop a trade the rules approved, in the ML sleeve. Nothing trains by itself.
          </div>
        </div>
      </div>

      {!status.extras_available && (
        <div className="card" data-testid="forecast-extras-missing">
          <h3>ML extras not installed</h3>
          <div className="text-muted" style={{ fontSize: 13 }}>
            {status.extras_message} The rest of the app works without them; training and predictions need them.
          </div>
        </div>
      )}

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>ML style</h3>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <ToggleSwitch
            checked={status.enabled}
            onChange={(checked) => update.mutate({ ml_style_enabled: checked })}
            label="ML style"
            disabled={update.isPending}
          />
          <span style={{ fontSize: 13 }}>{status.enabled ? 'On: the ML sleeve asks the active model.' : 'Off (default).'}</span>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
            Stop a trade when expected R is below
            <input type="number" min={-2} max={3} step={0.1} value={limit} onChange={(e) => setLimit(e.target.value)} />
          </label>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!limitValid || limitNum === status.min_expected_r || update.isPending}
            onClick={() => update.mutate({ ml_min_expected_r: limitNum })}
          >
            Save limit
          </button>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
          {status.ml_sleeve_key ? (
            <span>
              ML sleeve: <code>{status.ml_sleeve_key}</code>. Generate trade plans for it from the sleeve switcher; it opens
              positions only through an ordinary evaluation.
            </span>
          ) : (
            <>
              <span>No ML sleeve yet.</span>
              <button type="button" className="btn" disabled={createSleeve.isPending} onClick={() => createSleeve.mutate()}>
                Create the ML sleeve
              </button>
            </>
          )}
        </div>
        {(update.error || createSleeve.error) && (
          <span className="text-red" style={{ fontSize: 13 }}>
            {(update.error ?? createSleeve.error)?.message}
          </span>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Train from backtests</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Pick finished backtest runs. Training reads their stored trades only (no market data) and needs at least{' '}
          {status.min_rows} closed trades. Backtests are price-only, so fundamentals, news and options points are always zero in
          the training data. Results are optimistic (survivors, daily bars).
        </div>
        {status.runs.length === 0 ? (
          <EmptyState>No finished backtests yet. Run one in the Backtest Lab first.</EmptyState>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {status.runs.map((r) => (
              <label key={r.id} style={{ display: 'flex', gap: 8, fontSize: 13 }}>
                <input type="checkbox" checked={picked.includes(r.id)} onChange={() => togglePick(r.id)} />
                Run #{r.id}: {r.closed_trades} closed trades
                {r.finished_at ? `, finished ${formatRelativeTime(r.finished_at)}` : ''}
              </label>
            ))}
          </div>
        )}
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!status.extras_available || picked.length === 0 || train.isPending}
            onClick={() => train.mutate(picked)}
          >
            {train.isPending ? 'Training…' : 'Train model'}
          </button>
          {picked.length > 0 && <span className="text-muted">{pickedRows} trades selected</span>}
        </div>
        {train.error && (
          <span className="text-red" style={{ fontSize: 13 }}>
            {train.error.message}
          </span>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Models</h3>
        {!models || models.length === 0 ? (
          <EmptyState>No models yet. A new model is saved but never activated for you.</EmptyState>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Trained</th>
                <th>From</th>
                <th>Trades</th>
                <th>Out-of-sample</th>
                <th />
                <th />
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <ModelRow key={m.id} model={m} selected={selected === m.id} onSelect={() => setSelected(m.id)} />
              ))}
            </tbody>
          </table>
        )}
        {selected !== null && <ModelDetail id={selected} />}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Recent opinions</h3>
        {!predictions || predictions.length === 0 ? (
          <EmptyState>Nothing yet. Opinions are recorded when the ML sleeve evaluates a trade the rules approved.</EmptyState>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Symbol</th>
                <th>Expected R</th>
                <th>Result</th>
                <th>Main drivers (SHAP, in R)</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((p) => (
                <tr key={p.id}>
                  <td>{formatRelativeTime(p.created_at)}</td>
                  <td>{p.symbol ?? `plan ${p.plan_id}`}</td>
                  <td>{p.available ? fmt(p.expected_r) : 'n/a'}</td>
                  <td>
                    {!p.available ? (
                      <span className="badge badge-neutral">no opinion</span>
                    ) : p.stopped_trade ? (
                      <span className="badge badge-red">stopped</span>
                    ) : (
                      <span className="badge badge-green">allowed</span>
                    )}
                  </td>
                  <td style={{ fontSize: 13 }}>
                    {p.available
                      ? p.top_features.map((f) => `${f.feature}=${f.value} (${f.shap >= 0 ? '+' : ''}${f.shap.toFixed(2)})`).join(', ')
                      : (p.note ?? '')}
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

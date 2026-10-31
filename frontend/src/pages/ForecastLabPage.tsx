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
import './forecast.css';
import { EmptyState, ErrorBanner, LoadingSpinner, ToggleSwitch, formatRelativeTime } from '../components/common';

const VERDICT_TEXT: Record<string, { label: string; cls: string }> = {
  edge_out_of_sample: { label: 'Out-of-sample edge', cls: 'chip chip-green' },
  no_clear_edge: { label: 'No clear edge', cls: 'chip chip-amber' },
  inverted: { label: 'Inverted', cls: 'chip chip-red' },
  not_enough_data: { label: 'Not enough data', cls: 'chip' },
};

function VerdictBadge({ verdict }: { verdict: string }) {
  const v = VERDICT_TEXT[verdict] ?? VERDICT_TEXT.not_enough_data;
  return <span className={v.cls}>{v.label}</span>;
}

const fmt = (v: number | null | undefined, digits = 2) => (v == null ? 'n/a' : v.toFixed(digits));

function MeanCell({ label, m, tone }: { label: string; m?: ForecastMean; tone?: 'keep' | 'stop' }) {
  return (
    <div className={`fc-cohort ${tone ?? ''}`}>
      <span className="label-mono">{label}</span>
      <span className="fc-cohort-value">{m ? `${fmt(m.mean)}R` : 'n/a'}</span>
      <span className="fc-cohort-sub">
        n={m?.n ?? 0}
        {m && m.low != null && m.high != null && ` · 95% interval ${fmt(m.low)} to ${fmt(m.high)}`}
      </span>
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
    <div className="fc-body fc-detail" data-testid="forecast-model-detail">
      <div className="text-muted" style={{ fontSize: 13 }}>
        {model.split.n_train} train, {model.split.n_validation} validation, {model.split.n_test} test trades (validation from{' '}
        {String(model.split.validation_start)}, test from {String(model.split.test_start)}; {model.split.purged_rows} dropped
        because their result was not yet known when the next window began). The test window was used once, after the model was
        chosen.
      </div>
      <div className="fc-cohorts">
        <MeanCell label="All test trades" m={m.all_test_trades} />
        <MeanCell label="Model would keep" m={m.kept_by_model} tone="keep" />
        <MeanCell label="Model would stop" m={m.stopped_by_model} tone="stop" />
      </div>
      <div className="fc-row">
        <span className="label-mono">Rank correlation (IC)</span>
        <span className="mono">
          {fmt(m.ic?.ic)} n={m.ic?.n ?? 0}
          {m.ic?.low != null && m.ic?.high != null && ` · 95% interval ${fmt(m.ic.low)} to ${fmt(m.ic.high)}`}
        </span>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Test error {fmt(m.test_l2, 3)} against {fmt(m.test_baseline_l2, 3)} for always guessing the training average (lower is
        better).
      </div>
      <div>
        <div className="label-mono" style={{ marginBottom: 8 }}>What the model leans on (mean absolute SHAP, test window)</div>
        {model.importance.map((i) => (
          <div key={i.feature} className="fc-shap">
            <span>{i.feature}</span>
            <div className="fc-shap-bar" style={{ width: `${(i.mean_abs_shap / maxShap) * 100}%` }} />
            <span className="text-muted mono">{i.mean_abs_shap.toFixed(3)}</span>
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
    <tr className={selected ? 'sel' : undefined}>
      <td className="num">#{model.id}</td>
      <td>{formatRelativeTime(model.created_at)}</td>
      <td>runs {model.run_ids.join(', ')}</td>
      <td>
        {model.n_rows} ({model.n_test} test)
      </td>
      <td>
        <VerdictBadge verdict={model.verdict} />
      </td>
      <td>{model.active ? <span className="chip chip-green">active</span> : ''}</td>
      <td>
        <div className="fc-actions">
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
        </div>
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
    <div className="fc-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Forecast Lab</h1>
          <div className="fc-sub">
            A small gradient-boosted model learns, from finished backtest trades, which setups ended well in R. SHAP shows what
            drove each opinion. It is an opinion only: the rules still choose direction, entry, stop and size, and the model can
            only stop a trade the rules approved, in the ML sleeve. Nothing trains by itself.
          </div>
        </div>
      </div>

      {!status.extras_available && (
        <div className="card card-flush" data-testid="forecast-extras-missing">
          <div className="panel-header"><span className="label-mono">ML extras not installed</span></div>
          <div className="fc-body text-muted" style={{ fontSize: 13 }}>
            {status.extras_message} The rest of the app works without them; training and predictions need them.
          </div>
        </div>
      )}

      <div className="card card-flush">
        <div className="panel-header"><span className="label-mono">ML style</span></div>
        <div className="fc-body">
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
      </div>

      <div className="card card-flush">
        <div className="panel-header"><span className="label-mono">Train from backtests</span></div>
        <div className="fc-body">
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
      </div>

      <div className="card card-flush">
        <div className="panel-header"><span className="label-mono">Models</span></div>
        <div className="fc-body">
        {!models || models.length === 0 ? (
          <EmptyState>No models yet. A new model is saved but never activated for you.</EmptyState>
        ) : (
          <div className="fc-scroll"><table className="fc-table">
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
          </table></div>
        )}
        {selected !== null && <ModelDetail id={selected} />}
        </div>
      </div>

      <div className="card card-flush">
        <div className="panel-header"><span className="label-mono">Recent opinions</span></div>
        <div className="fc-body">
        {!predictions || predictions.length === 0 ? (
          <EmptyState>Nothing yet. Opinions are recorded when the ML sleeve evaluates a trade the rules approved.</EmptyState>
        ) : (
          <div className="fc-scroll"><table className="fc-table">
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
                      <span className="chip">no opinion</span>
                    ) : p.stopped_trade ? (
                      <span className="chip chip-red">stopped</span>
                    ) : (
                      <span className="chip chip-green">allowed</span>
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
          </table></div>
        )}
        </div>
      </div>
    </div>
  );
}

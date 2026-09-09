export function SignalBadge({ signal }: { signal: string }) {
  if (signal === 'potential_setup') return <span className="badge badge-green">Potential Setup</span>;
  if (signal === 'watching') return <span className="badge badge-amber">Watching</span>;
  return <span className="badge badge-neutral">No Signal</span>;
}

export function DirectionBadge({ direction }: { direction: string | null | undefined }) {
  if (direction === 'long') return <span className="badge badge-green">Long</span>;
  if (direction === 'short') return <span className="badge badge-red">Short</span>;
  return <span className="badge badge-neutral">—</span>;
}

export function TrendBadge({ trend }: { trend: string }) {
  if (trend === 'Bullish') return <span className="badge badge-green">Bullish</span>;
  if (trend === 'Bearish') return <span className="badge badge-red">Bearish</span>;
  return <span className="badge badge-neutral">Neutral</span>;
}

export function TradePlanStatusBadge({ status }: { status: string | null | undefined }) {
  if (status === 'executed') return <span className="badge badge-green">Executed</span>;
  if (status === 'pending') return <span className="badge badge-amber">Pending</span>;
  if (status === 'discarded') return <span className="badge badge-neutral">Discarded</span>;
  if (status === 'no_trade') return <span className="badge badge-neutral">No Trade</span>;
  return <span className="badge badge-neutral">—</span>;
}

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

export function PositionStatusBadge({ status }: { status: string }) {
  return status === 'open' ? (
    <span className="badge badge-amber">Open</span>
  ) : (
    <span className="badge badge-neutral">Closed</span>
  );
}

export function RatioGauge({ ratio, max = 3, size = 88, label = 'Ratio' }: { ratio: number; max?: number; size?: number; label?: string }) {
  const strokeWidth = 8;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const fraction = Math.min(Math.max(ratio / max, 0), 1);
  const dashOffset = circumference * (1 - fraction);

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--border)" strokeWidth={strokeWidth} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--indigo)"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
        />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <div className="tabular-nums" style={{ fontSize: 16, fontWeight: 700 }}>
          {ratio.toFixed(1)}:1
        </div>
        <div className="text-muted" style={{ fontSize: 10 }}>
          {label}
        </div>
      </div>
    </div>
  );
}

const VARIANTS = {
  up: { bg: 'var(--green-bg)', color: 'var(--green)', char: '↗' },
  down: { bg: 'var(--red-bg)', color: 'var(--red)', char: '↘' },
  info: { bg: 'rgba(59, 130, 246, 0.14)', color: 'var(--indigo-light)', char: '◔' },
} as const;

export function IconBadge({ variant, size = 40 }: { variant: keyof typeof VARIANTS; size?: number }) {
  const cfg = VARIANTS[variant];
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: cfg.bg,
        color: cfg.color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size * 0.45,
        flexShrink: 0,
      }}
    >
      {cfg.char}
    </div>
  );
}

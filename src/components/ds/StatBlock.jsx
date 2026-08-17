const VALUE_SIZE = { lg: 'clamp(32px, 4vw, 56px)', md: '32px' };

export default function StatBlock({ label, value, note, size = 'lg' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--fs-label)',
          letterSpacing: 'var(--ls-label)',
          textTransform: 'uppercase',
          color: 'var(--fg-3)',
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 'var(--fw-light)',
          fontSize: VALUE_SIZE[size] || VALUE_SIZE.lg,
          lineHeight: 1,
          letterSpacing: '-0.02em',
          color: 'var(--fg-1)',
          fontVariantNumeric: 'tabular-nums lining-nums',
        }}
      >
        {value}
      </span>
      {note ? (
        <span style={{ fontSize: 'var(--fs-caption)', lineHeight: 1.45, color: 'var(--fg-3)' }}>{note}</span>
      ) : null}
    </div>
  );
}

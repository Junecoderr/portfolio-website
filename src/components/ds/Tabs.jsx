export default function Tabs({ items = [], value, onChange, variant = 'pill' }) {
  return (
    <div
      style={{
        display: 'inline-flex',
        gap: 2,
        padding: 4,
        borderRadius: variant === 'pill' ? 'var(--radius-pill)' : 'var(--radius-control)',
        border: '1px solid var(--line-1)',
      }}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onChange?.(item.value)}
            style={{
              padding: '6px 14px',
              borderRadius: 'var(--radius-pill)',
              border: 'none',
              cursor: 'pointer',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--fs-micro)',
              letterSpacing: 'var(--ls-micro)',
              textTransform: 'uppercase',
              background: active ? 'var(--fg-1)' : 'transparent',
              color: active ? 'var(--fg-inverse)' : 'var(--fg-3)',
              transition: 'all var(--dur-2) var(--ease-out)',
            }}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

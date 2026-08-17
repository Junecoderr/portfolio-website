export default function Input({ label, multiline = false, rows = 3, value, onChange, style, ...rest }) {
  const Tag = multiline ? 'textarea' : 'input';
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 8, ...style }}>
      {label ? (
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
      ) : null}
      <Tag
        value={value}
        onChange={onChange}
        rows={multiline ? rows : undefined}
        style={{
          width: '100%',
          resize: multiline ? 'vertical' : undefined,
          background: 'var(--surface-page)',
          border: '1px solid var(--line-1)',
          borderRadius: 'var(--radius-control)',
          padding: '12px 14px',
          color: 'var(--fg-1)',
          fontFamily: 'var(--font-text)',
          fontSize: 'var(--fs-body-s)',
        }}
        {...rest}
      />
    </label>
  );
}

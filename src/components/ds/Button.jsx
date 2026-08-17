const SIZE = {
  sm: { padding: '8px 16px', fontSize: 'var(--fs-micro)' },
  md: { padding: '11px 20px', fontSize: 'var(--fs-label)' },
  lg: { padding: '15px 26px', fontSize: 'var(--fs-label)' },
};

export default function Button({ variant = 'primary', size = 'md', as = 'button', iconRight, children, style, ...rest }) {
  const Tag = as;
  const base = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    fontFamily: 'var(--font-mono)',
    letterSpacing: 'var(--ls-label)',
    textTransform: 'uppercase',
    borderRadius: 'var(--radius-cta)',
    cursor: 'pointer',
    border: '1px solid transparent',
    transition: 'border-color var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out)',
    ...SIZE[size],
  };

  const variants = {
    primary: { background: 'var(--brand-gradient)', color: '#fff' },
    secondary: { background: 'rgba(255,255,255,.94)', color: 'var(--brand-ink)' },
    ghost: { background: 'transparent', color: 'var(--fg-1)', borderColor: 'var(--line-2)' },
  };

  return (
    <Tag style={{ ...base, ...variants[variant], ...style }} {...rest}>
      {children}
      {iconRight === 'arrow-up-right' ? <span aria-hidden="true">&#8599;</span> : null}
    </Tag>
  );
}

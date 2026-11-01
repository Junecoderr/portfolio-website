// Milestone badges, drawn in code so there are no image files. Colours come from the active palette.
const SHAPES = {
  sprout: (
    <>
      <path d="M32 50V30" stroke="var(--follicular)" strokeWidth="3" strokeLinecap="round" />
      <path d="M32 34c-8 0-13-5-13-12 8 0 13 5 13 12z" fill="var(--follicular)" />
      <path d="M32 30c0-8 5-13 13-13 0 8-5 13-13 13z" fill="var(--follicular)" opacity=".7" />
    </>
  ),
  leaf: (
    <>
      <path d="M18 46C18 28 30 18 48 16c0 18-10 30-28 30z" fill="var(--follicular)" />
      <path d="M20 44 42 22" stroke="var(--surface)" strokeWidth="2" strokeLinecap="round" />
    </>
  ),
  bloom: (
    <>
      {[0, 72, 144, 216, 288].map((deg) => (
        <ellipse key={deg} cx="32" cy="21" rx="6" ry="10" fill="var(--menstrual)" transform={`rotate(${deg} 32 32)`} />
      ))}
      <circle cx="32" cy="32" r="6" fill="var(--ovulatory)" />
    </>
  ),
  moon: (
    <>
      <circle cx="32" cy="32" r="15" fill="var(--luteal)" />
      <circle cx="39" cy="27" r="13" fill="var(--surface)" />
    </>
  ),
  circle: (
    <>
      {['menstrual', 'follicular', 'ovulatory', 'luteal'].map((p, i) => (
        <path
          key={p}
          d="M32 14a18 18 0 0 1 18 18"
          stroke={`var(--${p})`}
          strokeWidth="6"
          strokeLinecap="round"
          fill="none"
          transform={`rotate(${i * 90} 32 32)`}
        />
      ))}
    </>
  ),
};

export default function Badge({ shape, locked = false, size = 64 }) {
  return (
    <svg
      className={`badge${locked ? ' badge--locked' : ''}`}
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden="true"
    >
      <circle cx="32" cy="32" r="30" fill="var(--surface)" stroke="var(--line)" strokeWidth="1.5" />
      {SHAPES[shape]}
    </svg>
  );
}

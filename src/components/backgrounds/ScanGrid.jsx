const ACCENTS = { violet: '#a56bff', magenta: '#ff5fd0', cyan: '#00d9ff' };

/** Animated SVG grid of pulsing cells. */
export default function ScanGrid({ opacity = 0.86, accent = 'violet', style, ...rest }) {
  const c = ACCENTS[accent] || ACCENTS.violet;

  const cells = Array.from({ length: 40 }).map((_, i) => ({
    i,
    x: 180 + (i % 8) * 112,
    y: 82 + Math.floor(i / 8) * 74,
    d: Math.floor(i / 8) * 0.25 + (i % 8) * 0.08,
  }));

  return (
    <div aria-hidden="true" style={{ width: '100%', height: '100%', opacity, ...style }} {...rest}>
      <svg
        viewBox="0 0 1200 520"
        preserveAspectRatio="none"
        style={{ width: '100%', height: '100%' }}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {Array.from({ length: 8 }).map((_, i) => (
          <line key={`v${i}`} x1={170 + i * 112} y1="70" x2={170 + i * 112} y2="450" stroke="#1c1728" strokeWidth="1" opacity="0.7" />
        ))}
        {Array.from({ length: 6 }).map((_, i) => (
          <line key={`h${i}`} x1="170" y1={70 + i * 74} x2="1060" y2={70 + i * 74} stroke="#1c1728" strokeWidth="1" opacity="0.7" />
        ))}
        {cells.map((cell) => (
          <rect key={cell.i} x={cell.x} y={cell.y} width="92" height="54" rx="2" fill={cell.i % 7 === 0 ? '#ff5fd0' : c}>
            <animate
              attributeName="opacity"
              values="0.06;0.3;0.08;0.44;0.06"
              dur="4.8s"
              repeatCount="indefinite"
              begin={`${-cell.d}s`}
            />
          </rect>
        ))}
      </svg>
    </div>
  );
}

import { useRef, useState } from 'react';

/** Cursor-following radial gradient bloom, confined to its wrapper. */
export default function PointerBloom({ radius = 420, intensity = 0.18, accent = 'violet', style, ...rest }) {
  const wrapRef = useRef(null);
  const [p, setP] = useState({ x: 50, y: 50, on: false });

  const a = accent === 'cyan' ? '0,217,255' : accent === 'magenta' ? '255,47,190' : '165,107,255';

  return (
    <div
      ref={wrapRef}
      aria-hidden="true"
      onPointerMove={(e) => {
        const r = wrapRef.current.getBoundingClientRect();
        setP({
          x: ((e.clientX - r.left) / r.width) * 100,
          y: ((e.clientY - r.top) / r.height) * 100,
          on: true,
        });
      }}
      onPointerLeave={() => setP((s) => ({ ...s, on: false }))}
      style={{ position: 'absolute', inset: 0, pointerEvents: 'auto', ...style }}
      {...rest}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: p.on ? 1 : 0,
          transition: 'opacity var(--dur-hover) var(--ease-quant)',
          background: `radial-gradient(${radius}px circle at ${p.x}% ${p.y}%,rgba(${a},${intensity}),rgba(255,47,190,${intensity * 0.5}) 42%,transparent 68%)`,
        }}
      />
    </div>
  );
}

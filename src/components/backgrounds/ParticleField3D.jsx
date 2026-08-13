import { useEffect, useRef } from 'react';

/**
 * Perspective-projected point cloud on a 2D canvas. Points stream downward;
 * the camera yaws and pitches toward the pointer.
 */
export default function ParticleField3D({
  count = 900,
  speed = 0.55,
  parallax = true,
  palette = ['#a56bff', '#ff5fd0', '#00d9ff'],
  style,
  ...rest
}) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const aim = useRef({ x: 0, y: 0, cx: 0, cy: 0 });

  useEffect(() => {
    const cv = canvasRef.current;
    const ctx = cv.getContext('2d');
    let raf;
    let w = 0;
    let h = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const pts = Array.from({ length: count }).map(() => ({
      x: (Math.random() - 0.5) * 2000,
      y: (Math.random() - 0.5) * 2000,
      z: Math.random() * 1800 + 200,
      c: palette[Math.floor(Math.random() * palette.length)],
    }));

    const size = () => {
      const r = wrapRef.current.getBoundingClientRect();
      w = r.width;
      h = r.height;
      cv.width = w * dpr;
      cv.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();

    const ro = new ResizeObserver(size);
    ro.observe(wrapRef.current);

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const draw = () => {
      aim.current.cx += (aim.current.x - aim.current.cx) * 0.045;
      aim.current.cy += (aim.current.y - aim.current.cy) * 0.045;
      ctx.clearRect(0, 0, w, h);
      const f = 520;
      const ox = w / 2 + aim.current.cx * 46;
      const oy = h / 2 + aim.current.cy * 30;

      for (const p of pts) {
        if (!reduce) {
          p.y -= speed * 2.4;
          p.z -= speed * 0.9;
        }
        if (p.y < -1000) p.y = 1000;
        if (p.z < 120) {
          p.z = 2000;
          p.x = (Math.random() - 0.5) * 2000;
          p.y = (Math.random() - 0.5) * 2000;
        }

        const yaw = aim.current.cx * 0.16;
        const pitch = aim.current.cy * 0.1;
        const rx = p.x * Math.cos(yaw) - p.z * Math.sin(yaw);
        const rz = p.x * Math.sin(yaw) + p.z * Math.cos(yaw);
        const ry = p.y * Math.cos(pitch) - rz * Math.sin(pitch);
        const rz2 = p.y * Math.sin(pitch) + rz * Math.cos(pitch);
        if (rz2 < 80) continue;

        const s = f / rz2;
        const sx = ox + rx * s;
        const sy = oy + ry * s;
        if (sx < -40 || sx > w + 40 || sy < -40 || sy > h + 40) continue;

        const r = Math.max(0.35, 1.9 * s);
        ctx.globalAlpha = Math.min(0.85, Math.max(0.05, (1 - rz2 / 2000) * 0.9));
        ctx.fillStyle = p.c;
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, 6.283);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };
    draw();

    const move = (e) => {
      const r = wrapRef.current.getBoundingClientRect();
      aim.current.x = ((e.clientX - r.left) / r.width - 0.5) * 2;
      aim.current.y = ((e.clientY - r.top) / r.height - 0.5) * 2;
    };
    if (parallax) window.addEventListener('pointermove', move, { passive: true });

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      if (parallax) window.removeEventListener('pointermove', move);
    };
  }, [count, speed, parallax, palette]);

  return (
    <div
      ref={wrapRef}
      aria-hidden="true"
      style={{ position: 'absolute', inset: 0, overflow: 'hidden', ...style }}
      {...rest}
    >
      <canvas ref={canvasRef} style={{ display: 'block', width: '100%', height: '100%' }} />
      <div style={{ position: 'absolute', inset: 0, background: 'var(--scrim-hero)', pointerEvents: 'none' }} />
    </div>
  );
}

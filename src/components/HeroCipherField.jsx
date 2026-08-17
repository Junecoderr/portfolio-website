import { useEffect, useRef } from 'react';

const PHRASE = 'THE QUIET WORK OF KEEPING THINGS SECRET IS MOSTLY BOOKKEEPING ';
const GLYPHS = '0123456789ABCDEF/\\|<>{}[]#$%&*+=~^';

/**
 * Canvas glyph field behind the hero: a hidden phrase resolves near the
 * pointer, the rest of the grid cycles random hex/symbol glyphs.
 */
export default function HeroCipherField({ density = 15, style, ...rest }) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);

  useEffect(() => {
    const cv = canvasRef.current;
    const wrap = wrapRef.current;
    if (!cv || !wrap) return undefined;

    const ctx = cv.getContext('2d');
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const state = { cols: 0, rows: 0, cell: 0, chars: [], hold: [], mx: -9999, my: -9999, tx: -9999, ty: -9999, w: 0, h: 0 };

    const build = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = wrap.getBoundingClientRect();
      state.w = r.width;
      state.h = r.height;
      cv.width = Math.floor(r.width * dpr);
      cv.height = Math.floor(r.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      state.cell = density;
      state.cols = Math.ceil(r.width / state.cell);
      state.rows = Math.ceil(r.height / state.cell);
      const n = state.cols * state.rows;
      state.chars = new Array(n);
      state.hold = new Array(n);
      for (let i = 0; i < n; i += 1) {
        state.chars[i] = GLYPHS[(Math.random() * GLYPHS.length) | 0];
        state.hold[i] = (Math.random() * 40) | 0;
      }
    };

    const onResize = () => build();
    const onMouse = (e) => {
      const r = wrap.getBoundingClientRect();
      state.tx = e.clientX - r.left;
      state.ty = e.clientY - r.top;
    };
    window.addEventListener('resize', onResize);
    window.addEventListener('pointermove', onMouse);
    build();

    const radius = 132;
    let raf;
    const draw = () => {
      state.mx += (state.tx - state.mx) * 0.12;
      state.my += (state.ty - state.my) * 0.12;
      ctx.clearRect(0, 0, state.w, state.h);
      ctx.font = `500 ${state.cell - 3}px 'JetBrains Mono', monospace`;
      ctx.textBaseline = 'top';
      for (let row = 0; row < state.rows; row += 1) {
        for (let col = 0; col < state.cols; col += 1) {
          const i = row * state.cols + col;
          const x = col * state.cell;
          const y = row * state.cell;
          const dx = x - state.mx;
          const dy = y - state.my;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d < radius) {
            const t = 1 - d / radius;
            const ch = PHRASE[(col + row * 7) % PHRASE.length];
            if (t > 0.55) {
              ctx.fillStyle = `rgba(245,245,247,${(0.35 + t * 0.65).toFixed(3)})`;
              ctx.fillText(ch, x, y);
            } else {
              const mix = (t - 0.2) / 0.35;
              ctx.fillStyle = mix > 0 ? `rgba(238,0,233,${(0.25 + mix * 0.5).toFixed(3)})` : 'rgba(122,18,245,0.30)';
              ctx.fillText(Math.random() > 0.4 ? ch : state.chars[i], x, y);
            }
          } else {
            if (!reduced) {
              state.hold[i] -= 1;
              if (state.hold[i] < 0) {
                state.chars[i] = GLYPHS[(Math.random() * GLYPHS.length) | 0];
                state.hold[i] = 20 + ((Math.random() * 90) | 0);
              }
            }
            const fade = Math.min(1, Math.max(0, (state.h - y) / state.h));
            ctx.fillStyle = `rgba(69,69,90,${(0.2 + fade * 0.34).toFixed(3)})`;
            ctx.fillText(state.chars[i], x, y);
          }
        }
      }
      raf = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('pointermove', onMouse);
    };
  }, [density]);

  return (
    <div ref={wrapRef} style={{ position: 'absolute', inset: 0, overflow: 'hidden', ...style }} {...rest}>
      <canvas ref={canvasRef} style={{ display: 'block', width: '100%', height: '100%' }} />
    </div>
  );
}

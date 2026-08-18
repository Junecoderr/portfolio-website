import { useEffect, useRef, useState } from 'react';

const EXIT_MS = 1100;
const REDUCED_EXIT_MS = 300;

/**
 * Gates visibility of `children` behind a pulsing brand-gradient orb and a
 * percent numeral. Fires `onEnter` as the wipe starts so the header can
 * drop in while the preloader is still clearing. Real progress: fonts.ready
 * (30%) plus the lattice scene's first-rendered-frame signal (70%).
 */
export default function Preloader({ ready, onEnter, children }) {
  const [percent, setPercent] = useState(0);
  const [done, setDone] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [hidden, setHidden] = useState(false);
  const rootRef = useRef(null);
  const target = useRef(0);
  const fontsReady = useRef(false);
  const exitStarted = useRef(false);

  useEffect(() => {
    document.fonts?.ready.then(() => {
      fontsReady.current = true;
    });
  }, []);

  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let raf;
    let current = 0;

    const tick = () => {
      const fontsPct = fontsReady.current ? 30 : Math.min(28, current);
      const scenePct = ready ? 70 : 0;
      target.current = fontsPct + scenePct;

      current += (target.current - current) * (reduce ? 1 : 0.12);
      if (target.current >= 100 && current > 99.3) current = 100;
      setPercent(Math.round(current));

      if (current >= 100) {
        setDone(true);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [ready]);

  useEffect(() => {
    if (!done || exitStarted.current) return undefined;
    exitStarted.current = true;

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setExiting(true);
    onEnter?.();

    const t = setTimeout(() => setHidden(true), reduce ? REDUCED_EXIT_MS : EXIT_MS);
    return () => clearTimeout(t);
  }, [done, onEnter]);

  return (
    <>
      <div
        ref={rootRef}
        className={`preloader${exiting ? ' is-exiting' : ''}`}
        aria-hidden={done ? 'true' : 'false'}
        style={hidden ? { display: 'none' } : undefined}
      >
        <div className="preloader-content">
          <span className="preloader-mark" aria-hidden="true" />
          <span className="preloader-numeral">{percent}%</span>
        </div>
      </div>
      {children}
    </>
  );
}

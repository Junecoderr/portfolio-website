import { useEffect, useRef, useState } from 'react';

const EXIT_MS = 1100;
const REDUCED_EXIT_MS = 300;

/**
 * Gates visibility of `children` behind a percent-counter + four-piece
 * spinner (two bracket shapes + two arc fragments), then fires `onEnter` as
 * the curtain wipe starts so the header can drop in while the preloader is
 * still clearing. Real progress: fonts.ready (30%) plus the lattice scene's
 * first-rendered-frame signal (70%).
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
        <svg className="preloader-spinner" viewBox="0 0 140 140" width="140" height="140">
          <path
            className="preloader-bracket preloader-bracket-l"
            d="M52 30 L28 30 L28 70 L52 70"
            fill="none"
            stroke="var(--brand-blue)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            className="preloader-bracket preloader-bracket-r"
            d="M88 30 L112 30 L112 70 L88 70"
            fill="none"
            stroke="var(--brand-magenta)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            className="preloader-arc preloader-arc-l"
            d="M70 15 A55 55 0 0 0 15 70"
            fill="none"
            stroke="url(#preloader-gradient-a)"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <path
            className="preloader-arc preloader-arc-r"
            d="M70 125 A55 55 0 0 0 125 70"
            fill="none"
            stroke="url(#preloader-gradient-b)"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <defs>
            <linearGradient id="preloader-gradient-a" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#0018FD" />
              <stop offset="100%" stopColor="#7A12F5" />
            </linearGradient>
            <linearGradient id="preloader-gradient-b" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#7A12F5" />
              <stop offset="100%" stopColor="#EE00E9" />
            </linearGradient>
          </defs>
        </svg>
        <span className="preloader-numeral">{percent}%</span>
      </div>
      {children}
    </>
  );
}

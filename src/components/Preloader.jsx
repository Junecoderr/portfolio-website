import { useEffect, useState } from 'react';

const KEY = 'tb_preloader';
const EXIT_AT = 1700;
const REMOVE_AT = 2300;

/** Name splash shown once per session, then fades out. */
export default function Preloader() {
  const [phase, setPhase] = useState(() => {
    try {
      return sessionStorage.getItem(KEY) === '1' ? 'done' : 'show';
    } catch {
      return 'show';
    }
  });

  useEffect(() => {
    if (phase === 'done') return undefined;
    const t1 = setTimeout(() => setPhase('exit'), EXIT_AT);
    const t2 = setTimeout(() => {
      setPhase('done');
      try {
        sessionStorage.setItem(KEY, '1');
      } catch {
        /* storage blocked */
      }
    }, REMOVE_AT);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [phase]);

  if (phase === 'done') return null;

  return (
    <div className="preloader" style={{ opacity: phase === 'exit' ? 0 : 1 }}>
      <div className="preloader-glow" />
      <h1 className="preloader-title">
        <span className="preloader-word">Tanisha</span>
        <span className="preloader-slash">/</span>
        <span className="preloader-word">Brahma</span>
      </h1>
    </div>
  );
}

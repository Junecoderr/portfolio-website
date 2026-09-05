import { useEffect, useState } from 'react';

const KEY = 'tb_preloader_at';
const DAY = 24 * 60 * 60 * 1000;
const EXIT_AT = 900;
const REMOVE_AT = 1300;

function seenToday() {
  try {
    const at = Number(localStorage.getItem(KEY) || 0);
    return Date.now() - at < DAY;
  } catch {
    return false;
  }
}

/** Name splash shown at most once a day, skipped entirely on deep links. */
export default function Preloader({ skip = false }) {
  const [phase, setPhase] = useState(() => (skip || seenToday() ? 'done' : 'show'));

  useEffect(() => {
    if (phase === 'done') return undefined;
    const t1 = setTimeout(() => setPhase('exit'), EXIT_AT);
    const t2 = setTimeout(() => {
      setPhase('done');
      try {
        localStorage.setItem(KEY, String(Date.now()));
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

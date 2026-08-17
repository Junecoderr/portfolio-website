import { useEffect, useState } from 'react';

const pad = (n) => String(n).padStart(2, '0');

/** Live HH:MM:SS clock string, updated every second. */
export default function useClock(suffix = 'IST') {
  const [clock, setClock] = useState('');

  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setClock(`${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${suffix}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [suffix]);

  return clock;
}

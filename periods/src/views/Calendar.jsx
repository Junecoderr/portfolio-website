import { useState } from 'react';
import { cycleInfo } from '../lib/cycle.js';
import { formatDate } from '../lib/format.js';
import { ROUTINES } from '../data/routines.js';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const pad = (n) => String(n).padStart(2, '0');

function Day({ state, today, keyDate }) {
  const info = cycleInfo(keyDate, state, today);
  const future = keyDate > today;
  const day = Number(keyDate.slice(8));
  const total = info ? ROUTINES[info.phase].items.length : 0;
  const done = info ? ROUTINES[info.phase].items.filter((i) => state.ticks[keyDate]?.includes(i.id)).length : 0;
  const period = info?.phase === 'menstrual' && !info.late;
  const classes = ['cal__day'];
  if (info) classes.push(`cal__day--${info.phase}`);
  if (info?.predicted) classes.push('cal__day--predicted');
  if (period) classes.push(info.predicted ? 'cal__day--period-predicted' : 'cal__day--period');
  if (keyDate === today) classes.push('cal__day--today');

  const label = [
    formatDate(keyDate, { weekday: 'long', day: 'numeric', month: 'long' }),
    info && `${ROUTINES[info.phase].name}${info.predicted ? ' (predicted)' : ''}, day ${info.cycleDay}`,
    period && (info.predicted ? 'predicted period' : 'period'),
    info && !future && `${done} of ${total} done`,
  ]
    .filter(Boolean)
    .join(', ');

  const body = (
    <>
      <span className="cal__num">{day}</span>
      {info && !future && (
        <span className="cal__dot" style={{ '--pct': total ? done / total : 0 }} data-empty={done === 0 || undefined} />
      )}
    </>
  );
  return (
    <li>
      {info && !future ? (
        <a className={classes.join(' ')} href={`#/day/${keyDate}`} aria-label={label}>
          {body}
        </a>
      ) : (
        <span className={classes.join(' ')} aria-label={label}>
          {body}
        </span>
      )}
    </li>
  );
}

export default function Calendar({ state, today }) {
  const [month, setMonth] = useState(() => ({ y: +today.slice(0, 4), m: +today.slice(5, 7) }));
  const { y, m } = month;
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7; // Monday-first offset
  const days = new Date(y, m, 0).getDate();
  const keys = Array.from({ length: days }, (_, i) => `${y}-${pad(m)}-${pad(i + 1)}`);
  const shift = (n) =>
    setMonth(({ y, m }) => {
      const d = new Date(y, m - 1 + n, 1);
      return { y: d.getFullYear(), m: d.getMonth() + 1 };
    });
  const title = new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

  return (
    <section className="calendar">
      <div className="cal__head">
        <button type="button" className="icon-button" onClick={() => shift(-1)} aria-label="Previous month">
          ‹
        </button>
        <h1 tabIndex={-1} aria-live="polite">
          {title}
        </h1>
        <button type="button" className="icon-button" onClick={() => shift(1)} aria-label="Next month">
          ›
        </button>
      </div>
      <div className="card">
        <ol className="cal__grid cal__weekdays" aria-hidden="true">
          {WEEKDAYS.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ol>
        <ol className="cal__grid">
          {Array.from({ length: lead }, (_, i) => (
            <li key={`lead-${i}`} aria-hidden="true" />
          ))}
          {keys.map((k) => (
            <Day key={k} state={state} today={today} keyDate={k} />
          ))}
        </ol>
      </div>
      <ul className="legend">
        {Object.entries(ROUTINES).map(([phase, r]) => (
          <li key={phase}>
            <span className={`swatch swatch--${phase}`} aria-hidden="true" />
            {r.name}
          </li>
        ))}
        <li>
          <span className="swatch swatch--period" aria-hidden="true" />
          Period
        </li>
        <li>
          <span className="swatch swatch--predicted" aria-hidden="true" />
          Predicted
        </li>
      </ul>
      <p className="muted center">Tap a day to see or change its checklist.</p>
    </section>
  );
}

import { useState } from 'react';
import { addDays, cycleInfo, isDateKey, nextPredictedStart } from '../lib/cycle.js';
import { formatDate } from '../lib/format.js';
import { LATE_NOTE, ROUTINES } from '../data/routines.js';
import Checklist from '../components/Checklist.jsx';

function LogStart({ today, starts, onAdd }) {
  const [date, setDate] = useState(today);
  const [message, setMessage] = useState('');
  const min = addDays(today, -60);
  return (
    <details className="log-start card">
      <summary>My period started</summary>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (!isDateKey(date) || date > today || date < min) return setMessage('Pick a date in the last 60 days.');
          if (starts.includes(date)) return setMessage('That start is already logged.');
          onAdd(date);
          setMessage(`Logged ${formatDate(date, { day: 'numeric', month: 'long' })}. Take it easy.`);
        }}
      >
        <label htmlFor="log-date" className="visually-hidden">
          Start date
        </label>
        <input id="log-date" type="date" value={date} min={min} max={today} onChange={(e) => setDate(e.target.value)} />
        <button type="submit" className="button">
          Log start
        </button>
      </form>
      <p className="help" role="status">
        {message}
      </p>
    </details>
  );
}

export default function DayView({ state, today, actions, date }) {
  const isToday = date === today;
  const info = cycleInfo(date, state, today);
  const done = state.ticks[date] ?? [];
  const nextStart = nextPredictedStart(state);

  if (!info) {
    return (
      <section className="day">
        <p className="eyebrow">{formatDate(date)}</p>
        <h1 tabIndex={-1}>Before your first logged period</h1>
        <p className="muted">There is no checklist for this day. Pick a later date in the calendar.</p>
        <a className="button button--quiet" href="#/calendar">
          Back to calendar
        </a>
      </section>
    );
  }

  const routine = ROUTINES[info.phase];
  const count = routine.items.filter((i) => done.includes(i.id)).length;

  return (
    <section className="day" style={{ '--phase': `var(--${info.phase})` }}>
      {!isToday && (
        <a className="back" href="#/calendar">
          ← Calendar
        </a>
      )}
      <header className="phase-head card">
        <p className="eyebrow">
          {isToday ? 'Today' : 'Looking back'} · {formatDate(date)}
        </p>
        <h1 tabIndex={-1}>
          <span className="phase-dot" aria-hidden="true" />
          {routine.name} <span className="muted-inline">· day {info.cycleDay}</span>
        </h1>
        <p className="season">{routine.season}</p>
        <p>{routine.description}</p>
        {info.late && isToday && <p className="late">{LATE_NOTE}</p>}
        <div className="progress" aria-hidden="true">
          <span style={{ width: `${(count / routine.items.length) * 100}%` }} />
        </div>
        <p className="muted" aria-live="polite">
          {count === 0
            ? 'Pick anything that feels right.'
            : `${count} of ${routine.items.length} done${count === routine.items.length ? ', all of them. Lovely.' : '.'}`}
        </p>
      </header>

      <Checklist items={routine.items} done={done} onToggle={(id) => actions.toggleTick(date, id)} />

      {isToday && (
        <>
          <LogStart today={today} starts={state.periodStarts} onAdd={actions.addStart} />
          {!info.late && nextStart && nextStart > today && (
            <p className="muted center">
              Next period expected around {formatDate(nextStart, { day: 'numeric', month: 'long' })}.
            </p>
          )}
        </>
      )}
    </section>
  );
}

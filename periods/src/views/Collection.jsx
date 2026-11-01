import { computeStreaks, completedCycles, insights } from '../lib/streaks.js';
import { formatDate } from '../lib/format.js';
import { MILESTONES } from '../data/rewards.js';
import { ROUTINES } from '../data/routines.js';
import Badge from '../components/Badge.jsx';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export default function Collection({ state, today }) {
  const { current, longest } = computeStreaks(state.ticks, state.periodStarts, today);
  const cycles = completedCycles(state.periodStarts);
  const unlocked = MILESTONES.filter((m) => state.unlocked[m.id]);
  const showInsights = unlocked.some((m) => m.insights);
  const data = showInsights ? insights(state, today) : null;

  return (
    <section className="collection">
      <h1 tabIndex={-1}>Your collection</h1>

      <div className="stats">
        <div className="card stat">
          <span className="stat__value">{current}</span>
          <span className="stat__label">{current === 1 ? 'day' : 'days'} in your current streak</span>
        </div>
        <div className="card stat">
          <span className="stat__value">{longest}</span>
          <span className="stat__label">longest streak</span>
        </div>
        <div className="card stat">
          <span className="stat__value">{cycles}</span>
          <span className="stat__label">{cycles === 1 ? 'cycle' : 'cycles'} logged</span>
        </div>
      </div>
      <p className="muted">
        A day counts when you tick at least one thing. Two rest days each cycle never break a streak.
      </p>

      <h2>Badges</h2>
      <ul className="badges">
        {MILESTONES.map((m) => {
          const got = state.unlocked[m.id];
          return (
            <li key={m.id} className="card badge-card">
              <Badge shape={m.badge} locked={!got} />
              <span className="badge-card__title">{m.title}</span>
              <span className="muted small">
                {got ? `Unlocked ${formatDate(got, { day: 'numeric', month: 'short' })}` : m.hint}
              </span>
            </li>
          );
        })}
      </ul>

      <h2>Notes for you</h2>
      {unlocked.length ? (
        <ul className="notes">
          {unlocked.map((m) => (
            <li key={m.id} className="card">
              <p className="eyebrow">{m.title}</p>
              <p>{m.note}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Notes appear here as you reach milestones. Your first one is 7 days away.</p>
      )}

      <h2>Insights</h2>
      {data ? (
        <div className="card insights">
          <p>
            Your cycle averages <strong>{data.avgCycle ? plural(data.avgCycle, 'day') : 'not enough data yet'}</strong>{' '}
            across {plural(data.cyclesLogged, 'cycle')}, with periods of about {plural(data.periodLength, 'day')}.
          </p>
          {Object.entries(data.topByPhase).map(([phase, top]) => (
            <div key={phase} className="insight-phase" style={{ '--phase': `var(--${phase})` }}>
              <h3>
                <span className="phase-dot" aria-hidden="true" />
                {ROUTINES[phase].name}: what you reach for most
              </h3>
              {top.length ? (
                <ol>
                  {top.map((t) => (
                    <li key={t.id}>
                      {t.title} <span className="muted small">({plural(t.count, 'time')})</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted small">Nothing ticked in this phase yet.</p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">Log 3 full cycles to see your own patterns here.</p>
      )}
    </section>
  );
}

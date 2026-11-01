import { addDays, cycleInfo, cycleLengths, sortStarts, validCycleLengths } from './cycle.js';
import { MILESTONES } from '../data/rewards.js';
import { ROUTINES } from '../data/routines.js';

export const GRACE_PER_CYCLE = 2;

const ticked = (ticks, key) => (ticks[key]?.length ?? 0) > 0;

function cycleKeyFor(starts, key) {
  let found = 'before-first';
  for (const s of starts) if (s <= key) found = s;
  return found;
}

/**
 * Gentle streaks. A day counts when at least one item is ticked; up to GRACE_PER_CYCLE missed days per cycle
 * keep a streak alive without adding to it. Today only counts once ticked, so an untouched today is not "missed".
 * Returns { current, longest } in counted days.
 */
export function computeStreaks(ticks, periodStarts, today) {
  const days = Object.keys(ticks)
    .filter((k) => ticked(ticks, k) && k <= today)
    .sort();
  if (!days.length) return { current: 0, longest: 0 };
  const starts = sortStarts(periodStarts);

  let run = 0;
  let longest = 0;
  let grace = {};
  for (let key = days[0]; key <= today; key = addDays(key, 1)) {
    if (ticked(ticks, key)) {
      run++;
      longest = Math.max(longest, run);
    } else if (key === today) {
      break;
    } else if (run > 0) {
      const cycle = cycleKeyFor(starts, key);
      if ((grace[cycle] ?? 0) < GRACE_PER_CYCLE) {
        grace[cycle] = (grace[cycle] ?? 0) + 1;
      } else {
        run = 0;
        grace = {};
      }
    }
  }
  return { current: run, longest };
}

export function completedCycles(periodStarts) {
  return cycleLengths(periodStarts).length;
}

/** Ids of every milestone the data has reached. */
export function reachedMilestones({ ticks, periodStarts }, today) {
  const { longest } = computeStreaks(ticks, periodStarts, today);
  const cycles = completedCycles(periodStarts);
  return MILESTONES.filter((m) => (m.kind === 'streak' ? longest : cycles) >= m.value).map((m) => m.id);
}

/** Pattern summary for the Insights card. */
export function insights(state, today) {
  const lengths = validCycleLengths(state.periodStarts);
  const avgCycle = lengths.length ? Math.round(lengths.reduce((a, b) => a + b, 0) / lengths.length) : null;
  const counts = { menstrual: {}, follicular: {}, ovulatory: {}, luteal: {} };
  for (const [key, ids] of Object.entries(state.ticks)) {
    const info = key <= today ? cycleInfo(key, state, today) : null;
    if (!info) continue;
    for (const id of ids) counts[info.phase][id] = (counts[info.phase][id] ?? 0) + 1;
  }
  const topByPhase = Object.fromEntries(
    Object.entries(counts).map(([phase, byId]) => {
      const titles = Object.fromEntries(ROUTINES[phase].items.map((i) => [i.id, i.title]));
      const top = Object.entries(byId)
        .filter(([id]) => titles[id])
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([id, count]) => ({ id, title: titles[id], count }));
      return [phase, top];
    })
  );
  return { avgCycle, cyclesLogged: lengths.length, periodLength: state.setup.periodLength, topByPhase };
}

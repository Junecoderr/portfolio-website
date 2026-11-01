// Pure cycle math. Dates are local calendar days as 'YYYY-MM-DD' strings; internally each day maps to an
// integer day number via Date.UTC, so neither time zones nor DST can shift a date by one.

export const CYCLE_MIN = 21;
export const CYCLE_MAX = 45;
export const PERIOD_MIN = 2;
export const PERIOD_MAX = 10;
export const DEFAULT_SETUP = { cycleLength: 28, periodLength: 5 };
export const PHASES = ['menstrual', 'follicular', 'ovulatory', 'luteal'];

const DAY_MS = 86400000;
const KEY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function isDateKey(key) {
  const m = typeof key === 'string' && KEY_RE.exec(key);
  if (!m) return false;
  return toKey(fromDayNumber(toDayNumber(key))) === key; // rejects 2026-02-30 and friends
}

function toDayNumber(key) {
  const [, y, m, d] = KEY_RE.exec(key);
  return Date.UTC(+y, +m - 1, +d) / DAY_MS;
}

function fromDayNumber(n) {
  const dt = new Date(n * DAY_MS);
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

function toKey({ y, m, d }) {
  return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

/** Today's key in the device's local time zone. */
export function todayKey(now = new Date()) {
  return toKey({ y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() });
}

export function addDays(key, n) {
  return toKey(fromDayNumber(toDayNumber(key) + n));
}

/** Whole days from a to b (b - a). */
export function daysBetween(a, b) {
  return toDayNumber(b) - toDayNumber(a);
}

export function sortStarts(starts) {
  return [...new Set(starts.filter(isDateKey))].sort();
}

/** Gaps in days between consecutive logged period starts. */
export function cycleLengths(starts) {
  const s = sortStarts(starts);
  return s.slice(1).map((key, i) => daysBetween(s[i], key));
}

/** Gaps inside the plausible range; outliers (missed logs, double taps) are ignored. */
export function validCycleLengths(starts) {
  return cycleLengths(starts).filter((n) => n >= CYCLE_MIN && n <= CYCLE_MAX);
}

/** Average of the last 6 valid cycles; the setup value until 2 real cycles are logged. */
export function predictedCycleLength(starts, fallback = DEFAULT_SETUP.cycleLength) {
  const valid = validCycleLengths(starts).slice(-6);
  if (valid.length < 2) return fallback;
  return Math.round(valid.reduce((a, b) => a + b, 0) / valid.length);
}

/** Phase for cycle day d (1-based) in a cycle of length C with period length P. */
export function phaseForDay(d, C, P) {
  if (d <= P) return 'menstrual';
  const ovulation = C - 14;
  if (d >= ovulation - 1 && d <= ovulation + 1) return 'ovulatory';
  if (d < ovulation - 1) return 'follicular';
  return 'luteal';
}

/**
 * Where a date sits in the cycle.
 * Returns null before the first logged start, or for future dates while the period is late.
 * { cycleDay, cycleLength, phase, late, predicted } — predicted marks dates projected past today.
 */
export function cycleInfo(dateKey, { periodStarts = [], setup = DEFAULT_SETUP } = {}, today = todayKey()) {
  const starts = sortStarts(periodStarts);
  const P = setup.periodLength;
  const predictedC = predictedCycleLength(starts, setup.cycleLength);
  let i = -1;
  for (let k = starts.length - 1; k >= 0; k--) {
    if (starts[k] <= dateKey) {
      i = k;
      break;
    }
  }
  if (i === -1) return null;

  const start = starts[i];
  const next = starts[i + 1];
  // A finished cycle uses its real length so past days keep the phases they actually had.
  if (next) {
    const C = daysBetween(start, next);
    const cycleDay = daysBetween(start, dateKey) + 1;
    return { cycleDay, cycleLength: C, phase: phaseForDay(cycleDay, C, P), late: false, predicted: false };
  }

  const C = predictedC;
  const todayDay = daysBetween(start, today) + 1;
  const isLateNow = todayDay > C;
  if (dateKey <= today) {
    const cycleDay = daysBetween(start, dateKey) + 1;
    const late = cycleDay > C;
    return { cycleDay, cycleLength: C, phase: late ? 'luteal' : phaseForDay(cycleDay, C, P), late, predicted: false };
  }
  if (isLateNow) return null; // can't project while waiting on a late period
  const cycleDay = (daysBetween(start, dateKey) % C) + 1;
  return { cycleDay, cycleLength: C, phase: phaseForDay(cycleDay, C, P), late: false, predicted: true };
}

/** Next predicted period start, or null with no history. */
export function nextPredictedStart({ periodStarts = [], setup = DEFAULT_SETUP } = {}) {
  const starts = sortStarts(periodStarts);
  if (!starts.length) return null;
  return addDays(starts[starts.length - 1], predictedCycleLength(starts, setup.cycleLength));
}

export function isValidSetup(setup) {
  const { cycleLength: c, periodLength: p } = setup ?? {};
  return (
    Number.isInteger(c) &&
    Number.isInteger(p) &&
    c >= CYCLE_MIN &&
    c <= CYCLE_MAX &&
    p >= PERIOD_MIN &&
    p <= PERIOD_MAX &&
    p < c
  );
}

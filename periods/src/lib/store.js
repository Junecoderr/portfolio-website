// Everything lives in one localStorage key. Every access is wrapped: private mode or blocked storage
// must never stop the app from rendering with defaults.
import { isDateKey, isValidSetup, sortStarts } from './cycle.js';
import { DEFAULT_APPEARANCE, FONTS, isValidColors } from './theme.js';
import { PALETTES } from '../data/palettes.js';
import { MILESTONES } from '../data/rewards.js';

export const STORAGE_KEY = 'periods:v1';
export const SCHEMA_VERSION = 1;

export function defaultState() {
  return {
    schemaVersion: SCHEMA_VERSION,
    setup: null, // null until onboarding is done
    periodStarts: [],
    ticks: {},
    unlocked: {},
    seen: [],
    appearance: { ...DEFAULT_APPEARANCE },
  };
}

const isObject = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const milestoneIds = new Set(MILESTONES.map((m) => m.id));

function parseAppearance(a) {
  if (a === undefined) return { ...DEFAULT_APPEARANCE };
  if (!isObject(a)) throw new Error('Appearance settings are not readable.');
  const custom = a.custom ?? null;
  if (custom !== null && !(isValidColors(custom.light) && isValidColors(custom.dark))) {
    throw new Error('Custom colours must be #rrggbb values for both light and dark.');
  }
  return {
    palette: PALETTES.some((p) => p.id === a.palette) ? a.palette : DEFAULT_APPEARANCE.palette,
    custom,
    mode: ['light', 'dark', 'system'].includes(a.mode) ? a.mode : DEFAULT_APPEARANCE.mode,
    font: FONTS.some((f) => f.id === a.font) ? a.font : DEFAULT_APPEARANCE.font,
    density: ['compact', 'roomy'].includes(a.density) ? a.density : DEFAULT_APPEARANCE.density,
  };
}

/** Validates untrusted data (storage or an imported file). Throws an Error with a readable message. */
export function parseState(raw) {
  if (!isObject(raw)) throw new Error('This file is not a Cycle care backup.');
  if (raw.schemaVersion !== SCHEMA_VERSION) throw new Error('This backup is from an unknown version of Cycle care.');
  if (raw.setup !== null && !isValidSetup(raw.setup)) throw new Error('Cycle settings are out of range.');
  if (!Array.isArray(raw.periodStarts) || !raw.periodStarts.every(isDateKey)) {
    throw new Error('Period start dates are not valid dates.');
  }
  if (!isObject(raw.ticks)) throw new Error('Checklist history is not readable.');
  for (const [k, v] of Object.entries(raw.ticks)) {
    if (!isDateKey(k) || !Array.isArray(v) || !v.every((id) => typeof id === 'string')) {
      throw new Error(`Checklist history for "${k}" is not readable.`);
    }
  }
  const unlocked = isObject(raw.unlocked) ? raw.unlocked : {};
  const seen = Array.isArray(raw.seen) ? raw.seen : [];
  return {
    schemaVersion: SCHEMA_VERSION,
    setup: raw.setup && { cycleLength: raw.setup.cycleLength, periodLength: raw.setup.periodLength },
    periodStarts: sortStarts(raw.periodStarts),
    ticks: Object.fromEntries(Object.entries(raw.ticks).map(([k, v]) => [k, [...new Set(v)]])),
    unlocked: Object.fromEntries(Object.entries(unlocked).filter(([id, d]) => milestoneIds.has(id) && isDateKey(d))),
    seen: seen.filter((id) => milestoneIds.has(id)),
    appearance: parseAppearance(raw.appearance),
  };
}

export function loadState() {
  let text;
  try {
    text = localStorage.getItem(STORAGE_KEY);
  } catch {
    return defaultState();
  }
  if (!text) return defaultState();
  try {
    return parseState(JSON.parse(text));
  } catch {
    // Keep unreadable data aside rather than overwriting it on the next save.
    try {
      localStorage.setItem(`${STORAGE_KEY}:unreadable`, text);
    } catch {
      /* storage unavailable: nothing more to do */
    }
    return defaultState();
  }
}

/** Returns false when storage is unavailable or full. */
export function saveState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}

export function clearState() {
  try {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(`${STORAGE_KEY}:unreadable`);
  } catch {
    /* storage unavailable */
  }
}

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addDays } from '../src/lib/cycle.js';
import { computeStreaks, insights, reachedMilestones } from '../src/lib/streaks.js';
import { checkContrast, contrast } from '../src/lib/theme.js';
import { parseState, defaultState } from '../src/lib/store.js';
import { PALETTES } from '../src/data/palettes.js';
import { MILESTONES } from '../src/data/rewards.js';

const tickRange = (from, n, skip = []) => {
  const ticks = {};
  for (let i = 0; i < n; i++) if (!skip.includes(i)) ticks[addDays(from, i)] = ['x'];
  return ticks;
};

test('streak counts ticked days; untouched today is not a miss', () => {
  const ticks = tickRange('2026-10-01', 5);
  assert.deepEqual(computeStreaks(ticks, ['2026-10-01'], '2026-10-06'), { current: 5, longest: 5 });
});

test('two missed days per cycle keep the streak alive', () => {
  const ticks = tickRange('2026-10-01', 10, [3, 6]); // 8 ticked, 2 missed
  assert.deepEqual(computeStreaks(ticks, ['2026-10-01'], '2026-10-10'), { current: 8, longest: 8 });
});

test('a third miss in the same cycle starts a new streak, longest is kept', () => {
  const ticks = tickRange('2026-10-01', 12, [2, 4, 6]);
  assert.deepEqual(computeStreaks(ticks, ['2026-10-01'], '2026-10-10'), { current: 3, longest: 4 });
});

test('grace resets with a new cycle', () => {
  const ticks = tickRange('2026-10-01', 10, [1, 2, 6, 7]); // misses split across two cycles
  assert.deepEqual(computeStreaks(ticks, ['2026-09-20', '2026-10-05'], '2026-10-10'), { current: 6, longest: 6 });
});

test('empty history', () => {
  assert.deepEqual(computeStreaks({}, [], '2026-10-10'), { current: 0, longest: 0 });
});

test('milestones from streaks and cycles', () => {
  const data = { ticks: tickRange('2026-10-01', 7), periodStarts: ['2026-09-03', '2026-10-01'] };
  assert.deepEqual(reachedMilestones(data, '2026-10-08'), ['streak-7', 'cycle-1']);
});

test('insights rank the most-ticked items per phase', () => {
  const state = {
    setup: { cycleLength: 28, periodLength: 5 },
    periodStarts: ['2026-10-01'],
    ticks: { '2026-10-01': ['m-warmth', 'm-water'], '2026-10-02': ['m-warmth'], '2026-10-20': ['l-bath'] },
  };
  const res = insights(state, '2026-10-21');
  assert.deepEqual(res.topByPhase.menstrual[0], { id: 'm-warmth', title: 'Rest a heat pack on your belly', count: 2 });
  assert.equal(res.topByPhase.luteal[0].id, 'l-bath');
  assert.equal(res.avgCycle, null);
});

test('every preset palette is readable in light and dark', () => {
  for (const p of PALETTES) {
    for (const mode of ['light', 'dark']) {
      assert.ok(checkContrast(p[mode]).ok, `${p.id} ${mode}`);
      // accent fills buttons and focus rings, so it must stand out from the background
      assert.ok(contrast(p[mode].accent, p[mode].bg) >= 3, `${p.id} ${mode} accent`);
    }
  }
});

test('every unlockable palette points at a real milestone', () => {
  const ids = new Set(MILESTONES.map((m) => m.id));
  for (const p of PALETTES) if (p.unlock) assert.ok(ids.has(p.unlock), p.id);
});

test('import validation accepts a round trip and rejects bad files', () => {
  const s = { ...defaultState(), setup: { cycleLength: 30, periodLength: 4 }, periodStarts: ['2026-10-01'] };
  assert.deepEqual(parseState(JSON.parse(JSON.stringify(s))), s);
  assert.throws(() => parseState([]), /not a Cycle care backup/);
  assert.throws(() => parseState({ ...s, schemaVersion: 9 }), /unknown version/);
  assert.throws(() => parseState({ ...s, periodStarts: ['2026-13-01'] }), /not valid dates/);
  assert.throws(() => parseState({ ...s, setup: { cycleLength: 90, periodLength: 4 } }), /out of range/);
  const bad = { ...s, appearance: { ...s.appearance, custom: { light: { bg: 'red' }, dark: {} } } };
  assert.throws(() => parseState(bad), /#rrggbb/);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  addDays,
  cycleInfo,
  daysBetween,
  isDateKey,
  isValidSetup,
  nextPredictedStart,
  phaseForDay,
  predictedCycleLength,
  todayKey,
  validCycleLengths,
} from '../src/lib/cycle.js';

const setup = { cycleLength: 28, periodLength: 5 };

test('phase boundaries for a 28-day cycle with a 5-day period', () => {
  const expected = { 1: 'menstrual', 5: 'menstrual', 6: 'follicular', 12: 'follicular', 13: 'ovulatory' };
  Object.assign(expected, { 15: 'ovulatory', 16: 'luteal', 28: 'luteal' });
  for (const [d, phase] of Object.entries(expected)) assert.equal(phaseForDay(+d, 28, 5), phase, `day ${d}`);
});

test('short cycle: menstrual wins, ovulation window shifts early', () => {
  assert.equal(phaseForDay(5, 21, 5), 'menstrual');
  assert.equal(phaseForDay(6, 21, 5), 'ovulatory');
  assert.equal(phaseForDay(8, 21, 5), 'ovulatory');
  assert.equal(phaseForDay(9, 21, 5), 'luteal');
});

test('cycleInfo walks a current cycle', () => {
  const data = { periodStarts: ['2026-10-01'], setup };
  const today = '2026-10-20';
  assert.deepEqual(cycleInfo('2026-10-01', data, today), {
    cycleDay: 1,
    cycleLength: 28,
    phase: 'menstrual',
    late: false,
    predicted: false,
  });
  assert.equal(cycleInfo('2026-10-06', data, today).phase, 'follicular');
  assert.equal(cycleInfo('2026-10-14', data, today).phase, 'ovulatory');
  assert.equal(cycleInfo('2026-10-20', data, today).phase, 'luteal');
  assert.equal(cycleInfo('2026-09-30', data, today), null);
});

test('future dates are projected and flagged as predicted', () => {
  const data = { periodStarts: ['2026-10-01'], setup };
  const info = cycleInfo('2026-10-29', data, '2026-10-05');
  assert.equal(info.cycleDay, 1);
  assert.equal(info.phase, 'menstrual');
  assert.equal(info.predicted, true);
  assert.equal(nextPredictedStart(data), '2026-10-29');
});

test('a finished cycle uses its real length', () => {
  const data = { periodStarts: ['2026-09-01', '2026-10-01'], setup };
  const info = cycleInfo('2026-09-17', data, '2026-10-05'); // 30-day cycle: ovulation day 16
  assert.equal(info.cycleLength, 30);
  assert.equal(info.cycleDay, 17);
  assert.equal(info.phase, 'ovulatory');
});

test('late period: luteal checklist, gentle late flag, no projection', () => {
  const data = { periodStarts: ['2026-10-01'], setup };
  const info = cycleInfo('2026-10-30', data, '2026-10-30');
  assert.equal(info.cycleDay, 30);
  assert.equal(info.late, true);
  assert.equal(info.phase, 'luteal');
  assert.equal(cycleInfo('2026-11-02', data, '2026-10-30'), null);
});

test('averaging: setup value until 2 real cycles, then the last 6 valid ones', () => {
  assert.equal(predictedCycleLength([], 30), 30);
  assert.equal(predictedCycleLength(['2026-01-01', '2026-01-29'], 30), 30); // only 1 cycle
  assert.equal(predictedCycleLength(['2026-01-01', '2026-01-29', '2026-02-28'], 30), 29); // 28, 30
  // 8 cycles of 30 then... last 6 only: 30,30,30,30,26,26 -> 28.67 -> 29
  const starts = ['2025-01-01'];
  for (const gap of [20, 20, 30, 30, 30, 30, 26, 26]) starts.push(addDays(starts.at(-1), gap));
  assert.deepEqual(validCycleLengths(starts), [30, 30, 30, 30, 26, 26]);
  assert.equal(predictedCycleLength(starts, 28), 29);
});

test('outliers outside 21-45 days are ignored', () => {
  const starts = ['2026-01-01', '2026-01-11', '2026-03-12', '2026-04-09', '2026-05-07']; // 10, 60, 28, 28
  assert.deepEqual(validCycleLengths(starts), [28, 28]);
  assert.equal(predictedCycleLength(starts, 35), 28);
});

test('unsorted and duplicate starts are cleaned up', () => {
  const starts = ['2026-02-28', '2026-01-01', '2026-01-29', '2026-01-29', 'nonsense'];
  assert.deepEqual(validCycleLengths(starts), [28, 30]);
});

test('leap year', () => {
  assert.equal(addDays('2028-02-28', 1), '2028-02-29');
  assert.equal(addDays('2027-02-28', 1), '2027-03-01');
  const data = { periodStarts: ['2028-02-27'], setup };
  assert.equal(cycleInfo('2028-03-01', data, '2028-03-01').cycleDay, 4);
  assert.equal(isDateKey('2028-02-29'), true);
  assert.equal(isDateKey('2027-02-29'), false);
});

test('month and year rollover', () => {
  assert.equal(addDays('2026-12-30', 3), '2027-01-02');
  assert.equal(daysBetween('2026-12-30', '2027-01-03'), 4);
  const data = { periodStarts: ['2026-12-30'], setup };
  assert.equal(cycleInfo('2027-01-03', data, '2027-01-03').cycleDay, 5);
  assert.equal(nextPredictedStart(data), '2027-01-27');
});

test('first-time user with no history', () => {
  assert.equal(cycleInfo('2026-11-01', { periodStarts: [], setup }, '2026-11-01'), null);
  assert.equal(nextPredictedStart({ periodStarts: [], setup }), null);
});

test('todayKey uses local calendar fields, not UTC', () => {
  assert.equal(todayKey(new Date(2026, 0, 1, 0, 5)), '2026-01-01');
  assert.equal(todayKey(new Date(2026, 11, 31, 23, 55)), '2026-12-31');
});

test('setup validation', () => {
  assert.equal(isValidSetup(setup), true);
  assert.equal(isValidSetup({ cycleLength: 20, periodLength: 5 }), false);
  assert.equal(isValidSetup({ cycleLength: 28, periodLength: 11 }), false);
  assert.equal(isValidSetup({ cycleLength: 28.5, periodLength: 5 }), false);
  assert.equal(isValidSetup(null), false);
});

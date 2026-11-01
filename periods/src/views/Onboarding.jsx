import { useState } from 'react';
import {
  CYCLE_MAX,
  CYCLE_MIN,
  DEFAULT_SETUP,
  PERIOD_MAX,
  PERIOD_MIN,
  addDays,
  isDateKey,
  isValidSetup,
} from '../lib/cycle.js';

export default function Onboarding({ today, onDone }) {
  const [step, setStep] = useState(0);
  const [start, setStart] = useState('');
  const [cycle, setCycle] = useState(String(DEFAULT_SETUP.cycleLength));
  const [period, setPeriod] = useState(String(DEFAULT_SETUP.periodLength));
  const [error, setError] = useState('');
  const earliest = addDays(today, -90);

  function next(e) {
    e.preventDefault();
    setError('');
    if (step === 0) {
      if (!isDateKey(start) || start > today || start < earliest) {
        return setError('Pick a date in the last 90 days, up to today.');
      }
    } else if (step === 1) {
      const c = Number(cycle);
      if (!Number.isInteger(c) || c < CYCLE_MIN || c > CYCLE_MAX) {
        return setError(`Enter a whole number from ${CYCLE_MIN} to ${CYCLE_MAX}.`);
      }
    } else {
      const setup = { cycleLength: Number(cycle), periodLength: Number(period) };
      if (!isValidSetup(setup)) return setError(`Enter a whole number from ${PERIOD_MIN} to ${PERIOD_MAX}.`);
      return onDone(setup, start);
    }
    setStep(step + 1);
  }

  const field = [
    {
      id: 'start',
      label: 'When did your last period start?',
      input: (
        <input
          id="start"
          type="date"
          value={start}
          min={earliest}
          max={today}
          onChange={(e) => setStart(e.target.value)}
          required
        />
      ),
      help: 'The first day of bleeding. Your best guess is fine.',
    },
    {
      id: 'cycle',
      label: 'How long is your cycle, usually?',
      input: (
        <span className="with-unit">
          <input
            id="cycle"
            type="number"
            inputMode="numeric"
            min={CYCLE_MIN}
            max={CYCLE_MAX}
            value={cycle}
            onChange={(e) => setCycle(e.target.value)}
            required
          />
          <span>days</span>
        </span>
      ),
      help: 'From one period start to the next. Not sure? 28 is a fine place to start; the app learns as you log.',
    },
    {
      id: 'period',
      label: 'How many days does your period usually last?',
      input: (
        <span className="with-unit">
          <input
            id="period"
            type="number"
            inputMode="numeric"
            min={PERIOD_MIN}
            max={PERIOD_MAX}
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            required
          />
          <span>days</span>
        </span>
      ),
      help: 'You can change any of this later in Settings.',
    },
  ][step];

  return (
    <form className="onboarding card" onSubmit={next} noValidate>
      <p className="eyebrow">Step {step + 1} of 3</p>
      {step === 0 && (
        <>
          <h1 tabIndex={-1}>A little care, every day of your cycle</h1>
          <p className="muted">
            Each day you get a short self-care checklist that fits the phase you are in. Tick what you manage; skip what
            you don’t.
          </p>
          <p className="privacy">Your data never leaves this device.</p>
        </>
      )}
      <div className="field">
        <label htmlFor={field.id}>{field.label}</label>
        {field.input}
        <p className="help" id={`${field.id}-help`}>
          {field.help}
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="row row--end">
        {step > 0 && (
          <button type="button" className="button button--quiet" onClick={() => (setError(''), setStep(step - 1))}>
            Back
          </button>
        )}
        <button type="submit" className="button">
          {step === 2 ? 'Start' : 'Next'}
        </button>
      </div>
    </form>
  );
}

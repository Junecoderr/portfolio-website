import { useRef, useState } from 'react';
import { CYCLE_MAX, CYCLE_MIN, PERIOD_MAX, PERIOD_MIN, isValidSetup, predictedCycleLength } from '../lib/cycle.js';
import { formatDate } from '../lib/format.js';
import { parseState } from '../lib/store.js';
import { DEFAULT_APPEARANCE, FONTS, checkContrast, resolveColors, resolveMode } from '../lib/theme.js';
import { COLOR_KEYS, COLOR_LABELS, PALETTES } from '../data/palettes.js';
import { milestoneById } from '../data/rewards.js';
import { ConfirmDialog } from '../components/Modal.jsx';

function CycleSettings({ state, actions }) {
  const [cycle, setCycle] = useState(String(state.setup.cycleLength));
  const [period, setPeriod] = useState(String(state.setup.periodLength));
  const [message, setMessage] = useState('');
  const recent = state.periodStarts.slice(-6).reverse();
  const learned = predictedCycleLength(state.periodStarts, state.setup.cycleLength);

  return (
    <section className="card settings-section" aria-labelledby="cycle-h">
      <h2 id="cycle-h">Your cycle</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const setup = { cycleLength: Number(cycle), periodLength: Number(period) };
          if (!isValidSetup(setup)) {
            return setMessage(`Cycle: ${CYCLE_MIN}–${CYCLE_MAX} days. Period: ${PERIOD_MIN}–${PERIOD_MAX} days.`);
          }
          actions.setSetup(setup);
          setMessage('Saved.');
        }}
      >
        <div className="field-row">
          <div className="field">
            <label htmlFor="s-cycle">Usual cycle length</label>
            <span className="with-unit">
              <input
                id="s-cycle"
                type="number"
                min={CYCLE_MIN}
                max={CYCLE_MAX}
                value={cycle}
                onChange={(e) => setCycle(e.target.value)}
              />
              <span>days</span>
            </span>
          </div>
          <div className="field">
            <label htmlFor="s-period">Usual period length</label>
            <span className="with-unit">
              <input
                id="s-period"
                type="number"
                min={PERIOD_MIN}
                max={PERIOD_MAX}
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
              />
              <span>days</span>
            </span>
          </div>
        </div>
        <p className="help">
          Predictions currently use {learned} days, learned from your logged cycles once there are two.
        </p>
        <div className="row">
          <button type="submit" className="button">
            Save
          </button>
          <span role="status" className="help">
            {message}
          </span>
        </div>
      </form>
      <h3>Recent period starts</h3>
      <ul className="starts">
        {recent.map((d) => (
          <li key={d}>
            <span>{formatDate(d, { day: 'numeric', month: 'long', year: 'numeric' })}</span>
            {state.periodStarts.length > 1 && (
              <button
                type="button"
                className="button button--quiet button--small"
                onClick={() => actions.removeStart(d)}
                aria-label={`Remove period start ${formatDate(d, { day: 'numeric', month: 'long' })}`}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ColorEditor({ appearance, mode, actions }) {
  const [draft, setDraft] = useState(() => resolveColors(appearance, mode));
  const { ok, pairs } = checkContrast(draft);
  const preview = Object.fromEntries(COLOR_KEYS.map((k) => [`--${k}`, draft[k]]));

  function save() {
    const other = mode === 'light' ? 'dark' : 'light';
    actions.setAppearance({ custom: { [mode]: draft, [other]: resolveColors(appearance, other) } });
  }

  return (
    <div className="color-editor">
      <p className="help">Editing colours for {mode} mode. Switch mode above to edit the other set.</p>
      <div className="color-grid">
        {COLOR_KEYS.map((k) => (
          <label key={k} className="color-field">
            <input type="color" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
            <span>{COLOR_LABELS[k]}</span>
          </label>
        ))}
      </div>
      <div className="preview" style={preview}>
        <div className="preview__card">
          <strong>Preview</strong>
          <p>This is how your checklist text will read.</p>
          <div className="preview__phases">
            {['menstrual', 'follicular', 'ovulatory', 'luteal'].map((p) => (
              <span key={p} style={{ background: `var(--${p})` }} />
            ))}
          </div>
        </div>
      </div>
      <ul className="contrast">
        {pairs.map((p) => (
          <li key={p.label} className={p.ratio >= 4.5 ? 'pass' : 'fail'}>
            {p.label}: {p.ratio.toFixed(1)}:1 {p.ratio >= 4.5 ? '✓ readable' : '✗ too faint (needs 4.5:1)'}
          </li>
        ))}
      </ul>
      {!ok && (
        <p className="error" role="alert">
          Some text would be hard to read. Adjust the colours or reset before saving.
        </p>
      )}
      <div className="row">
        <button type="button" className="button" onClick={save} disabled={!ok}>
          Save colours
        </button>
        <button
          type="button"
          className="button button--quiet"
          onClick={() => setDraft(resolveColors({ ...appearance, custom: null }, mode))}
        >
          Reset to palette
        </button>
      </div>
    </div>
  );
}

function Appearance({ state, actions, systemDark }) {
  const a = state.appearance;
  const mode = resolveMode(a.mode, systemDark);
  const radio = (name, value, current, onPick) => ({
    type: 'radio',
    name,
    value,
    checked: current === value,
    onChange: () => onPick(value),
  });

  return (
    <section className="card settings-section" aria-labelledby="look-h">
      <h2 id="look-h">Look and feel</h2>

      <fieldset>
        <legend>Palette</legend>
        <div className="palettes">
          {PALETTES.map((p) => {
            const locked = p.unlock && !state.unlocked[p.unlock];
            const colors = p[mode];
            return (
              <label key={p.id} className={`palette${locked ? ' palette--locked' : ''}`}>
                <input
                  {...radio('palette', p.id, a.custom ? null : a.palette, (id) =>
                    actions.setAppearance({ palette: id, custom: null })
                  )}
                  disabled={!!locked}
                />
                <span className="palette__swatches" aria-hidden="true" style={{ background: colors.bg }}>
                  {['menstrual', 'follicular', 'ovulatory', 'luteal'].map((k) => (
                    <span key={k} style={{ background: colors[k] }} />
                  ))}
                </span>
                <span className="palette__name">{p.name}</span>
                {locked && <span className="small muted">Unlocks with “{milestoneById(p.unlock).title}”</span>}
              </label>
            );
          })}
        </div>
        {a.custom && <p className="help">You are using custom colours. Pick a palette to go back.</p>}
      </fieldset>

      <details className="custom-colors">
        <summary>Customize colours</summary>
        <ColorEditor key={`${mode}-${a.palette}-${!!a.custom}`} appearance={a} mode={mode} actions={actions} />
      </details>

      <fieldset>
        <legend>Mode</legend>
        <div className="segmented">
          {['light', 'dark', 'system'].map((m) => (
            <label key={m}>
              <input {...radio('mode', m, a.mode, (v) => actions.setAppearance({ mode: v }))} />
              <span>{m[0].toUpperCase() + m.slice(1)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>Fonts</legend>
        <div className="fonts">
          {FONTS.map((f) => (
            <label key={f.id} className="font-option">
              <input {...radio('font', f.id, a.font, (v) => actions.setAppearance({ font: v }))} />
              <span style={{ fontFamily: f.heading }}>{f.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>Spacing</legend>
        <div className="segmented">
          {['roomy', 'compact'].map((d) => (
            <label key={d}>
              <input {...radio('density', d, a.density, (v) => actions.setAppearance({ density: v }))} />
              <span>{d[0].toUpperCase() + d.slice(1)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <button
        type="button"
        className="button button--quiet"
        onClick={() => actions.setAppearance({ ...DEFAULT_APPEARANCE })}
      >
        Reset look to default
      </button>
    </section>
  );
}

function DataControls({ state, today, actions }) {
  const fileRef = useRef(null);
  const [pendingImport, setPendingImport] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [message, setMessage] = useState({ text: '', error: false });

  function exportData() {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cycle-care-backup-${today}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage({ text: 'Backup downloaded.', error: false });
  }

  async function readFile(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    try {
      if (file.size > 2_000_000) throw new Error('That file is too large to be a Cycle care backup.');
      let raw;
      try {
        raw = JSON.parse(await file.text());
      } catch {
        throw new Error('That file is not valid JSON.');
      }
      setPendingImport(parseState(raw));
      setMessage({ text: '', error: false });
    } catch (err) {
      setMessage({ text: `Import failed: ${err.message}`, error: true });
    }
  }

  return (
    <section className="card settings-section" aria-labelledby="data-h">
      <h2 id="data-h">Your data</h2>
      <p className="muted">Everything is stored only in this browser. Export a backup to move it to another device.</p>
      <div className="row row--wrap">
        <button type="button" className="button button--quiet" onClick={exportData}>
          Export backup
        </button>
        <button type="button" className="button button--quiet" onClick={() => fileRef.current.click()}>
          Import backup
        </button>
        <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={readFile} />
        <button type="button" className="button button--danger" onClick={() => setConfirmDelete(true)}>
          Delete all my data
        </button>
      </div>
      <p className={message.error ? 'error' : 'help'} role={message.error ? 'alert' : 'status'}>
        {message.text}
      </p>
      {pendingImport && (
        <ConfirmDialog
          title="Replace your data?"
          body="Everything on this device will be replaced with the backup. This can’t be undone."
          confirmLabel="Replace"
          danger
          onCancel={() => setPendingImport(null)}
          onConfirm={() => {
            actions.replaceAll(pendingImport);
            setPendingImport(null);
            setMessage({ text: 'Backup imported.', error: false });
          }}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title="Delete all your data?"
          body="Your cycle history, checklists, rewards and settings will be erased from this device. This can’t be undone."
          confirmLabel="Delete everything"
          danger
          onCancel={() => setConfirmDelete(false)}
          onConfirm={actions.deleteAll}
        />
      )}
    </section>
  );
}

export default function Settings(props) {
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  return (
    <section className="settings">
      <h1 tabIndex={-1}>Settings</h1>
      <CycleSettings {...props} />
      <Appearance {...props} systemDark={systemDark} />
      <DataControls {...props} />
    </section>
  );
}

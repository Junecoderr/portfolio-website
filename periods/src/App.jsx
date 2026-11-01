import { useEffect, useMemo, useRef, useState } from 'react';
import { isDateKey, sortStarts, todayKey } from './lib/cycle.js';
import { clearState, defaultState, loadState, saveState } from './lib/store.js';
import { reachedMilestones } from './lib/streaks.js';
import { applyTheme } from './lib/theme.js';
import Nav from './components/Nav.jsx';
import Footer from './components/Footer.jsx';
import UnlockMoment from './components/UnlockMoment.jsx';
import Onboarding from './views/Onboarding.jsx';
import DayView from './views/DayView.jsx';
import Calendar from './views/Calendar.jsx';
import Collection from './views/Collection.jsx';
import Settings from './views/Settings.jsx';

function useHash() {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return hash;
}

// Re-reads the date when the tab comes back or a minute passes, so an app left open overnight rolls over.
function useToday() {
  const [today, setToday] = useState(todayKey);
  useEffect(() => {
    const on = () => setToday(todayKey());
    const id = setInterval(on, 60000);
    document.addEventListener('visibilitychange', on);
    window.addEventListener('focus', on);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', on);
      window.removeEventListener('focus', on);
    };
  }, []);
  return today;
}

function useSystemDark() {
  const query = '(prefers-color-scheme: dark)';
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setDark(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return dark;
}

function parseRoute(hash) {
  const [, view = 'today', arg] = hash.split('/');
  if (view === 'day' && isDateKey(arg)) return { view: 'day', date: arg };
  return { view: ['calendar', 'collection', 'settings'].includes(view) ? view : 'today' };
}

export default function App() {
  const [state, setState] = useState(loadState);
  const [saveFailed, setSaveFailed] = useState(false);
  const today = useToday();
  const systemDark = useSystemDark();
  const hash = useHash();
  const route = parseRoute(hash);
  const firstRoute = useRef(true);

  useEffect(() => {
    if (state.setup) setSaveFailed(!saveState(state));
  }, [state]);

  useEffect(() => applyTheme(state.appearance, systemDark), [state.appearance, systemDark]);

  useEffect(() => {
    if (!state.setup) return;
    const fresh = reachedMilestones(state, today).filter((id) => !state.unlocked[id]);
    if (fresh.length) {
      setState((s) => ({ ...s, unlocked: { ...s.unlocked, ...Object.fromEntries(fresh.map((id) => [id, today])) } }));
    }
  }, [state, today]);

  // Move focus to the new page heading on navigation, for keyboard and screen reader users.
  useEffect(() => {
    if (firstRoute.current) {
      firstRoute.current = false;
      return;
    }
    document.querySelector('main h1')?.focus();
    window.scrollTo(0, 0);
  }, [hash]);

  const actions = useMemo(
    () => ({
      toggleTick(date, id) {
        setState((s) => {
          const current = s.ticks[date] ?? [];
          const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
          const ticks = { ...s.ticks, [date]: next };
          if (!next.length) delete ticks[date];
          return { ...s, ticks };
        });
      },
      addStart: (date) => setState((s) => ({ ...s, periodStarts: sortStarts([...s.periodStarts, date]) })),
      removeStart: (date) => setState((s) => ({ ...s, periodStarts: s.periodStarts.filter((d) => d !== date) })),
      setSetup: (setup) => setState((s) => ({ ...s, setup })),
      setAppearance: (patch) => setState((s) => ({ ...s, appearance: { ...s.appearance, ...patch } })),
      markSeen: (id) => setState((s) => ({ ...s, seen: [...new Set([...s.seen, id])] })),
      finishOnboarding: (setup, start) => setState((s) => ({ ...s, setup, periodStarts: sortStarts([start]) })),
      replaceAll: (next) => setState(next),
      deleteAll() {
        clearState();
        setState(defaultState());
        window.location.hash = '';
      },
    }),
    []
  );

  if (!state.setup) {
    return (
      <div className="app app--onboarding">
        <main id="main">
          <Onboarding today={today} onDone={actions.finishOnboarding} />
        </main>
        <Footer />
      </div>
    );
  }

  const pendingUnlock = Object.keys(state.unlocked).find((id) => !state.seen.includes(id));
  const props = { state, today, actions };

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <a className="brand" href="#/today">
          Cycle care
        </a>
        <Nav current={route.view === 'day' ? 'calendar' : route.view} />
      </header>
      {saveFailed && (
        <p className="notice" role="status">
          This browser is not letting the app save. Your changes will last until you close the tab.
        </p>
      )}
      <main id="main" tabIndex={-1}>
        {route.view === 'today' && <DayView {...props} date={today} />}
        {route.view === 'day' && <DayView {...props} date={route.date > today ? today : route.date} />}
        {route.view === 'calendar' && <Calendar {...props} />}
        {route.view === 'collection' && <Collection {...props} />}
        {route.view === 'settings' && <Settings {...props} />}
      </main>
      <Footer />
      {pendingUnlock && <UnlockMoment id={pendingUnlock} onClose={() => actions.markSeen(pendingUnlock)} />}
    </div>
  );
}

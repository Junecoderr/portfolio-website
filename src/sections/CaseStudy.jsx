import { useEffect, useRef, useState } from 'react';
import Button from '../components/ds/Button.jsx';
import StatBlock from '../components/ds/StatBlock.jsx';
import { PROJECTS } from '../data/content.js';

export default function CaseStudy({ activeId, onOpenCase, onNavigate }) {
  const active = PROJECTS.find((p) => p.id === activeId) || PROJECTS[1];
  const total = active.timeline.length;
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timerRef = useRef(null);

  const stop = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    setPlaying(false);
  };

  const start = () => {
    stop();
    setStep(1);
    setPlaying(true);
    timerRef.current = setInterval(() => {
      setStep((s) => {
        if (s >= total) {
          stop();
          return total;
        }
        return s + 1;
      });
    }, 900);
  };

  useEffect(() => {
    start();
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  const nextIdx = (PROJECTS.findIndex((p) => p.id === active.id) + 1) % PROJECTS.length;
  const next = PROJECTS[nextIdx];

  return (
    <main className="page-fade route-main">
      <section className="case-header">
        <div className="container">
          <a
            href="#work"
            className="mono-link"
            onClick={(e) => {
              e.preventDefault();
              onNavigate('work');
            }}
          >
            ← Index
          </a>
          <div className="case-header-grid">
            <div>
              <span className="mono-label accent">{active.discipline}</span>
              <h1 className="route-title">{active.title}</h1>
              <p className="route-lead wide">{active.summary}</p>
            </div>
            <div className="fact-list">
              {active.facts.map((f) => (
                <div key={f.k} className="fact-row">
                  <span className="mono-label">{f.k}</span>
                  <span className="fact-value">{f.v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="case-metrics">
        <div className="container">
          <div className="metrics-grid">
            {active.metrics.map((m) => (
              <div key={m.label} className="metric-cell">
                <StatBlock label={m.label} value={m.value} size="md" />
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section-pad">
        <div className="container case-body-grid">
          <div className="case-body-col">
            {active.body.map((b) => (
              <div key={b.k} data-reveal="1" className="case-body-block">
                <span className="mono-label">{b.k}</span>
                <h3 className="h3-title">{b.h}</h3>
                <p className="body-text">{b.p}</p>
              </div>
            ))}
          </div>

          <div data-reveal="1" className="timeline-card">
            <div className="timeline-head">
              <span className="mono-label">{active.timelineTitle}</span>
              <Button variant="ghost" size="sm" onClick={playing ? stop : start}>
                {playing ? 'Playing' : step >= total ? 'Replay' : 'Play'}
              </Button>
            </div>
            <div className="timeline-body">
              <div className="timeline-events">
                {active.timeline.map((e, i) => {
                  const on = i < step;
                  return (
                    <div key={e.t} className="timeline-event" style={{ opacity: on ? 1 : 0.28 }}>
                      <span className="mono-micro timeline-t">{e.t}</span>
                      <div className="timeline-event-row">
                        <span className="timeline-dot-col">
                          <span
                            className="timeline-dot"
                            style={{
                              background: on ? (i === step - 1 ? 'var(--brand-magenta)' : 'var(--fg-1)') : 'var(--ink-600)',
                              boxShadow: i === step - 1 && on ? '0 0 0 6px rgba(238,0,233,.14)' : 'none',
                            }}
                          />
                          {i < active.timeline.length - 1 && (
                            <span
                              className="timeline-connector"
                              style={{ background: i + 1 < step ? 'var(--brand-gradient-line)' : 'var(--line-1)' }}
                            />
                          )}
                        </span>
                        <div className="timeline-event-copy">
                          <span className="timeline-event-title">{e.title}</span>
                          <span className="timeline-event-detail">{e.detail}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="section-pad-b">
        <div className="container next-case-row">
          <span className="mono-label">Next</span>
          <a
            href="#case"
            className="next-case-link"
            onClick={(e) => {
              e.preventDefault();
              onOpenCase(next.id);
            }}
          >
            {next.title} ↗
          </a>
        </div>
      </section>
    </main>
  );
}

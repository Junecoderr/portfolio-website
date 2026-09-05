import { useEffect, useRef } from 'react';
import { CloseIcon } from './Icons.jsx';
import { SITE_URL } from '../data/content.js';

const FOCUSABLE = 'a[href], button:not([disabled]), input, textarea, select, [tabindex]:not([tabindex="-1"])';

export default function CaseDialog({ project, onClose }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    if (!project) return undefined;
    const opener = document.activeElement;
    closeRef.current?.focus();

    const onKey = (e) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !dialogRef.current) return;
      const nodes = dialogRef.current.querySelectorAll(FOCUSABLE);
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (opener && typeof opener.focus === 'function') opener.focus();
    };
  }, [project, onClose]);

  if (!project) return null;

  return (
    <div className="dialog-scrim" onClick={onClose}>
      <div ref={dialogRef} className="dialog" role="dialog" aria-modal="true" aria-labelledby="case-title" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <div>
            <span className="eyebrow">{project.discipline} · {project.year}</span>
            <h3 id="case-title" className="dialog-title">{project.title}</h3>
          </div>
          <button ref={closeRef} type="button" className="dialog-close" onClick={onClose} aria-label="Close case study">
            <CloseIcon />
          </button>
        </div>
        <p className="dialog-summary">{project.summary}</p>
        <div className="dialog-facts">
          {project.facts.map((f) => (
            <div key={f.k} className="dialog-fact">
              <span className="dialog-fact-k">{f.k}</span>
              <span className="dialog-fact-v">{f.v}</span>
            </div>
          ))}
        </div>
        <div className="dialog-metrics">
          {project.metrics.map((m) => (
            <div key={m.label} className="dialog-metric">
              <span className="dialog-metric-v">{m.value}</span>
              <span className="dialog-metric-l">{m.label}</span>
            </div>
          ))}
        </div>
        <div className="dialog-timeline">
          <span className="eyebrow sm">{project.timelineTitle}</span>
          <ol className="timeline-track" aria-hidden="true">
            {project.timeline.map((e, i) => (
              <li key={e.t + i} style={{ left: `${(i / Math.max(1, project.timeline.length - 1)) * 100}%` }}>
                <span className="timeline-track-dot" />
                <span className="timeline-track-t">{e.t}</span>
              </li>
            ))}
          </ol>
          <div className="timeline">
            {project.timeline.map((e) => (
              <div key={e.t + e.title} className="timeline-row">
                <span className="timeline-t">{e.t}</span>
                <div>
                  <span className="timeline-title">{e.title}</span>
                  <span className="timeline-detail">{e.detail}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="dialog-foot">
          <span className="dialog-link-hint">Link to this case: <code>{`${SITE_URL}/work/${project.id}`}</code></span>
        </div>
      </div>
    </div>
  );
}

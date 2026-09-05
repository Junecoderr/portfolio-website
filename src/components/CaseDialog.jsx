import { useEffect } from 'react';
import { CloseIcon } from './Icons.jsx';

export default function CaseDialog({ project, onClose }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!project) return null;

  return (
    <div className="dialog-scrim" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="case-title" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <div>
            <span className="eyebrow">{project.discipline} · {project.year}</span>
            <h3 id="case-title" className="dialog-title">{project.title}</h3>
          </div>
          <button type="button" className="dialog-close" onClick={onClose} aria-label="Close case study">
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
      </div>
    </div>
  );
}

import { useState } from 'react';
import { PROJECTS } from '../data/content.js';

const FRAMES = ['Frame 01 — architecture', 'Frame 02 — detection surface', 'Frame 03 — telemetry', 'Frame 04 — outcome'];

export default function Work({ onOpenCase }) {
  const [hover, setHover] = useState(null);

  const onMove = (p) => (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const f = Math.min(3, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * 4)));
    const w = window.innerWidth;
    const h = window.innerHeight;
    const off = e.clientX > w * 0.55 ? -220 : 220;
    const hx = Math.min(w - 180, Math.max(180, e.clientX + off));
    const hy = Math.min(h - 132, Math.max(132, e.clientY));
    setHover({ id: p.id, hx, hy, frame: f });
  };

  const hovered = hover ? PROJECTS.find((p) => p.id === hover.id) : null;

  return (
    <main className="page-fade route-main">
      <section className="section route-hero">
        <div className="container">
          <span className="mono-label">Index — seven projects</span>
          <h1 className="route-title">Work</h1>
          <p className="route-lead">
            Detection engineering, incident response, and applied cryptography. Hover a row to scrub its frames.
          </p>
        </div>
      </section>

      <section className="section-pad-b">
        <div className="container">
          <div className="work-index">
            {PROJECTS.map((p) => (
              <div
                key={p.id}
                className="work-index-row"
                onMouseMove={onMove(p)}
                onMouseLeave={() => setHover(null)}
                onClick={() => onOpenCase(p.id)}
              >
                <span className="mono-index">{p.index}</span>
                <div className="work-index-copy">
                  <span className="work-index-title">{p.title}</span>
                  <span className="work-index-tagline">{p.tagline}</span>
                </div>
                <span className="mono-label">{p.discipline}</span>
                <span className="mono-label">{p.year}</span>
                <span className="work-index-arrow">↗</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {hover && hovered ? (
        <div className="work-preview" style={{ left: hover.hx, top: hover.hy }}>
          <div className="work-preview-card">
            <div className="work-preview-frame">
              <span className="mono-label">{FRAMES[hover.frame]} · image placeholder</span>
              <div className="work-preview-bar" style={{ width: `${((hover.frame + 1) / 4) * 100}%` }} />
            </div>
            <div className="work-preview-footer">
              <span className="mono-label">{hovered.title}</span>
              <span className="mono-micro">0{hover.frame + 1} / 04</span>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

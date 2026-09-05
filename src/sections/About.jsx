import { useState } from 'react';
import SectionHeading from '../components/SectionHeading.jsx';
import { ABOUT, STATS, PORTRAIT_SRC, PORTRAIT_FALLBACK } from '../data/content.js';

export default function About() {
  const [tab, setTab] = useState('stats');
  const [hasPhoto, setHasPhoto] = useState(true);

  return (
    <section id="about" className="section">
      <div className="container">
        <SectionHeading number="03" title="About" lead="SOC analyst turned detection lead, now independent, writing production cryptography from Durgapur." />
        <div className="about-grid">
          <div className="about-card" data-reveal="1">
            <div className="about-card-tint" />
            <div className="about-card-fade" />
            <div className="about-card-head">
              <span className="eyebrow">// Brief</span>
              <span className="about-handle">{ABOUT.handle}</span>
            </div>
            <h3 className="about-brief">{ABOUT.brief}</h3>
            <div className="about-tabs" role="tablist" aria-label="More about Tanisha">
              {ABOUT.tabs.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.key}
                  className={`pill${tab === t.key ? ' is-active' : ''}`}
                  onClick={() => setTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="about-card-body" role="tabpanel">
              {tab === 'stats' ? (
                <div key="stats" className="about-slide about-stats">
                  <div className="stat-grid">
                    {STATS.map((s) => (
                      <div key={s.label} className="stat">
                        <span className="stat-value">{s.value}</span>
                        <span className="stat-label">{s.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {tab === 'path' ? (
                <div key="path" className="about-slide about-path">
                  <h4 className="about-path-title">{ABOUT.path.heading}</h4>
                  <p className="about-path-body">{ABOUT.path.body}</p>
                  <div className="about-path-meta">
                    <span className="about-since">{ABOUT.path.since}</span>
                    <span>•</span>
                    <span>{ABOUT.path.where}</span>
                  </div>
                </div>
              ) : null}
              {tab === 'quote' ? (
                <div key="quote" className="about-slide about-quote">
                  <blockquote>&ldquo;{ABOUT.quote}&rdquo;</blockquote>
                  <span className="about-quote-by">— Tanisha Brahma</span>
                </div>
              ) : null}
            </div>
          </div>

          <div className={`portrait-card${hasPhoto ? ' has-photo' : ''}`} data-reveal="1">
            <div className="portrait-frame">
              {hasPhoto ? (
                <img src={PORTRAIT_SRC} alt="Tanisha Brahma" loading="lazy" decoding="async" onError={() => setHasPhoto(false)} />
              ) : (
                <img src={PORTRAIT_FALLBACK} alt="" aria-hidden="true" loading="lazy" decoding="async" />
              )}
            </div>
            <div className="portrait-scanlines" />
            <span className="portrait-corner tl">┌</span>
            <span className="portrait-corner tr">┐</span>
            <span className="portrait-corner bl">└</span>
            <span className="portrait-corner br">┘</span>
            {!hasPhoto ? <span className="portrait-caption">Tanisha Brahma</span> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

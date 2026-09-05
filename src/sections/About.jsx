import { useEffect, useState } from 'react';
import SectionHeading from '../components/SectionHeading.jsx';
import { ABOUT, STATS } from '../data/content.js';

const SLIDES = 4;

export default function About({ paused }) {
  const [slide, setSlide] = useState(0);
  const [hover, setHover] = useState(false);

  useEffect(() => {
    const id = setInterval(() => {
      if (hover || paused) return;
      setSlide((s) => (s + 1) % SLIDES);
    }, 4500);
    return () => clearInterval(id);
  }, [hover, paused]);

  return (
    <section id="about" className="section">
      <div className="container">
        <SectionHeading number="02" title="About" />
        <div className="about-grid" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
          <div className="about-card">
            <div className="about-card-tint" />
            <div className="about-card-fade" />
            <div className="about-card-head">
              <span className="about-counter">{`0${slide + 1} / 04`}</span>
            </div>
            <div className="about-card-body">
              {slide === 0 ? (
                <div key="s0" className="about-slide">
                  <h3 className="about-brief">{ABOUT.brief}</h3>
                </div>
              ) : null}
              {slide === 1 ? (
                <div key="s1" className="about-slide about-path">
                  <span className="eyebrow">// Path &amp; Background</span>
                  <h3 className="about-path-title">{ABOUT.path.heading}</h3>
                  <p className="about-path-body">{ABOUT.path.body}</p>
                  <div className="about-path-meta">
                    <span className="about-since">{ABOUT.path.since}</span>
                    <span>•</span>
                    <span>{ABOUT.path.where}</span>
                  </div>
                </div>
              ) : null}
              {slide === 2 ? (
                <div key="s2" className="about-slide about-stats">
                  <div className="about-stats-head">
                    <span className="eyebrow">// Field Metrics</span>
                    <span className="about-handle">{ABOUT.handle}</span>
                  </div>
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
              {slide === 3 ? (
                <div key="s3" className="about-slide about-quote">
                  <span className="eyebrow">// Core Philosophy</span>
                  <blockquote>&ldquo;{ABOUT.quote}&rdquo;</blockquote>
                  <span className="about-quote-by">— Tanisha Brahma</span>
                </div>
              ) : null}
            </div>
            <div className="about-pills">
              {ABOUT.pills.map((label, i) => (
                <button
                  key={label}
                  type="button"
                  className={`pill${i === slide ? ' is-active' : ''}`}
                  onClick={() => setSlide(i)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="portrait-card">
            <div className="portrait-frame">
              <img src="/tanisha.png" alt="Tanisha Brahma" />
            </div>
            <div className="portrait-scanlines" />
            <span className="portrait-corner tl">┌</span>
            <span className="portrait-corner tr">┐</span>
            <span className="portrait-corner bl">└</span>
            <span className="portrait-corner br">┘</span>
          </div>
        </div>
      </div>
    </section>
  );
}

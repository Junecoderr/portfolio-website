import SectionHeading from '../components/SectionHeading.jsx';
import { ABOUT, STATS, SKILL_GROUPS } from '../data/content.js';

// Resolved at build time: a photo at public/portrait.jpg (or .png/.webp) shows here; otherwise nothing.
const PHOTOS = import.meta.glob('../../public/portrait.{jpg,jpeg,png,webp}', { eager: true, query: '?url', import: 'default' });
const PORTRAIT_SRC = Object.values(PHOTOS)[0] || null;

export default function About() {
  return (
    <section id="about" className="section">
      <div className="container">
        <SectionHeading title="About" lead="SOC analyst turned detection lead, now independent, writing production cryptography from Durgapur." />
        <div className="about-grid">
          <div>
            <p className="about-brief" data-reveal="1">{ABOUT.brief}</p>
            <p className="about-body" data-reveal="1">{ABOUT.path.heading}. {ABOUT.path.body} {ABOUT.path.since}, {ABOUT.path.where}.</p>
            <div className="stat-row" data-reveal="1">
              {STATS.map((s) => (
                <div key={s.label}>
                  <span className="stat-value">{s.value}</span>
                  <span className="stat-label">{s.label}</span>
                </div>
              ))}
            </div>
            <dl className="skills-list" data-reveal="1">
              {SKILL_GROUPS.map((g) => (
                <div key={g.title}>
                  <dt>{g.title}</dt>
                  <dd>{g.items.map((it) => it.t).join(', ')}</dd>
                </div>
              ))}
            </dl>
            <blockquote className="about-quote" data-reveal="1">{ABOUT.quote}</blockquote>
          </div>
          {PORTRAIT_SRC ? <img className="portrait" src={PORTRAIT_SRC} alt="Tanisha Brahma" loading="lazy" decoding="async" data-reveal="1" /> : null}
        </div>
      </div>
    </section>
  );
}

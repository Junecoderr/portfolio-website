import { onNav } from '../components/Header.jsx';

const POSTS = [
  {
    meta: '2026 · SOC',
    title: 'Why your SIEM is loud and your SOC is blind',
    desc: 'Alert volume is a design choice. Here is how I cut a queue by two thirds without losing coverage.',
  },
  {
    meta: '2026 · Crypto',
    title: 'Rotating keys without taking the service down',
    desc: 'Dual-key windows, versioned envelopes, and the rollback plan nobody writes until they need it.',
  },
  {
    meta: '2025 · Detection',
    title: 'Detection as code, reviewed like software',
    desc: 'Version control, unit tests against replayed telemetry, and a pull request for every rule.',
  },
];

export default function Writing() {
  return (
    <section id="writing" className="section">
      <div className="section-content">
        <div className="section-kicker reveal">
          <span>04 — Writing</span>
          <a href="#writing" onClick={onNav} className="writing-all-link">
            All notes ↗
          </a>
        </div>
        <h2 className="section-title reveal">Notes from the console.</h2>
        <ul className="writing-list">
          {POSTS.map((post) => (
            <li key={post.title} className="writing-row reveal">
              <a href="#writing" onClick={onNav} className="writing-row-link">
                <span className="writing-meta">{post.meta}</span>
                <span className="writing-title">{post.title}</span>
                <span className="writing-desc">{post.desc}</span>
                <span className="writing-cta">Read ↗</span>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

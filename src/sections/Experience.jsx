import SectionHeading from '../components/SectionHeading.jsx';
import { EXPERIENCE } from '../data/content.js';
import { ExternalIcon, CalendarIcon } from '../components/Icons.jsx';

export default function Experience() {
  return (
    <section id="experience" className="section">
      <div className="container">
        <SectionHeading number="04" title="Experience" />
        <div className="xp-rail">
          {EXPERIENCE.map((x) => (
            <article key={x.org} className="xp-card" data-reveal="1">
              <span className="xp-year" aria-hidden="true">{x.year}</span>
              <div className="xp-head">
                <div className="xp-org">
                  {x.href ? (
                    <a href={x.href} className="xp-name">
                      <span>{x.org}</span>
                      <ExternalIcon stroke="#5ED2F2" style={{ opacity: 0.7 }} />
                    </a>
                  ) : (
                    <span className="xp-name">{x.org}</span>
                  )}
                  <p className="xp-role">{x.role}</p>
                </div>
                <span className="xp-period">
                  <CalendarIcon size={14} stroke="#5ED2F2" />
                  <span>{x.period}</span>
                </span>
              </div>
              <ul className="xp-points">
                {x.points.map((p) => <li key={p}>{p}</li>)}
              </ul>
              <div className="tag-row">
                {x.tags.map((t) => <span key={t} className="tag">{t}</span>)}
              </div>
            </article>
          ))}
          <span className="xp-year xp-year-end" aria-hidden="true">Now</span>
        </div>
      </div>
    </section>
  );
}

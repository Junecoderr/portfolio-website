import SectionHeading from '../components/SectionHeading.jsx';
import { EXPERIENCE, RECOGNITION } from '../data/content.js';
import { ArrowUpRight } from '../components/Icons.jsx';

export default function Experience() {
  return (
    <section id="experience" className="section">
      <div className="container">
        <SectionHeading title="Experience" lead="Six years across an enterprise SOC and independent practice." />
        <div className="xp-list">
          {EXPERIENCE.map((x) => (
            <article key={x.org} className="xp-item" data-reveal="1">
              <span className="xp-period">{x.period}</span>
              <div>
                {x.href ? (
                  <a href={x.href} className="xp-name">{x.org} <ArrowUpRight size={14} /></a>
                ) : (
                  <h3 className="xp-name">{x.org}</h3>
                )}
                <p className="xp-role">{x.role}</p>
                <ul className="xp-points">
                  {x.points.map((p) => <li key={p}>{p}</li>)}
                </ul>
                <p className="xp-tags">{x.tags.join(' · ')}</p>
              </div>
            </article>
          ))}
        </div>
        <div className="rec-block" id="recognition" data-reveal="1">
          <div className="rec-grid">
            {RECOGNITION.map((g) => (
              <div key={g.title}>
                <h3 className="rec-title">{g.title}</h3>
                <ul className="rec-list">
                  {g.items.map((it) => (
                    <li key={it.detail + it.name} className="rec-row">
                      <span className="rec-detail">{it.detail}</span>
                      <span className="rec-name">{it.name}</span>
                      <span className="rec-meta">{it.meta}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

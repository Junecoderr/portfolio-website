import SectionHeading from '../components/SectionHeading.jsx';
import { WORK_CARDS, PROJECTS } from '../data/content.js';
import { ArrowUpRight } from '../components/Icons.jsx';

export default function Work({ onOpenCase }) {
  return (
    <section id="work" className="section">
      <div className="container">
        <SectionHeading title="Work" lead="Four case studies: detection engineering, incident response, post-quantum migration and a custodial wallet audit." />
        <ul className="work-list">
          {WORK_CARDS.map((card) => {
            const p = PROJECTS.find((x) => x.id === card.id);
            return (
              <li key={card.id} className="work-card" data-reveal="1">
                <button type="button" className="work-cta" onClick={() => onOpenCase(card.id)} aria-label={`Open case study: ${p.title}`}>
                  <span className="work-number">{card.number}</span>
                  <span>
                    <span className="work-title">{p.title}</span>
                    <span className="work-tagline">{p.tagline}</span>
                  </span>
                  <span className="work-meta">
                    <span>{p.discipline} · {p.year}</span>
                    <ArrowUpRight size={14} className="work-arrow" />
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

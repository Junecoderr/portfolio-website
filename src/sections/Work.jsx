import SectionHeading from '../components/SectionHeading.jsx';
import { WORK_CARDS, PROJECTS } from '../data/content.js';

const NOTCH = 'M 0 32 C 0 14, 14 0, 32 0 L 175 0 C 190 0, 198 10, 205 22 C 212 34, 220 40, 235 40 L 368 40 C 386 40, 400 54, 400 72 L 400 340 L 0 340 Z';

export default function Work({ onOpenCase }) {
  return (
    <section id="work" className="section">
      <div className="container">
        <SectionHeading number="05" title="Work" align="right" />
        <div className="work-grid">
          {WORK_CARDS.map((card) => {
            const project = PROJECTS.find((p) => p.id === card.id);
            return (
              <article key={card.id} className="work-card" data-reveal="1">
                <div className="work-cover">
                  <div className="work-cover-gradient" style={{ background: card.gradient }} />
                  <div className="work-cover-noise" />
                </div>
                <div className="work-body">
                  <div className="work-notch" aria-hidden="true">
                    <svg viewBox="0 0 400 340" preserveAspectRatio="none"><path d={NOTCH} /></svg>
                  </div>
                  <div className="work-content">
                    <div className="work-title-row"><h3 className="work-title">{project.title}</h3></div>
                    <div className="work-copy">
                      <p>{card.blurb}</p>
                      <div className="tag-row">
                        {card.tags.map((t) => <span key={t} className="tag">{t}</span>)}
                      </div>
                    </div>
                    <div className="work-foot">
                      <div className="work-rule" />
                      <div className="work-foot-row">
                        <span className="work-number">{card.number}</span>
                        <button type="button" className="work-cta" onClick={() => onOpenCase(card.id)}>Case study</button>
                      </div>
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

import SectionHeading from '../components/SectionHeading.jsx';
import { RECOGNITION } from '../data/content.js';

export default function Recognition() {
  return (
    <section id="recognition" className="section">
      <div className="container">
        <SectionHeading number="05" title="Recognition" lead="Certifications, credited CVEs, talks and papers." />
        <div className="rec-grid">
          {RECOGNITION.map((group) => (
            <div key={group.title} className="rec-col" data-reveal="1">
              <h3 className="rec-title">{group.title}</h3>
              <ul className="rec-list">
                {group.items.map((it) => (
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
    </section>
  );
}

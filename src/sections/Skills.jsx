import { useEffect, useRef, useState } from 'react';
import SectionHeading from '../components/SectionHeading.jsx';
import { SKILL_GROUPS, SKILL_MARQUEE } from '../data/content.js';

const GROUPS = [0, 1, 2];

function Chip({ chip }) {
  return (
    <div className="chip">
      {chip.img ? <span className="chip-icon" style={{ backgroundImage: `url('${chip.img}')` }} /> : null}
      {chip.g ? <span className="chip-glyph">{chip.g}</span> : null}
      <span className="chip-text">{chip.t}</span>
    </div>
  );
}

export default function Skills() {
  const rowRef = useRef(null);
  const [onScreen, setOnScreen] = useState(false);

  useEffect(() => {
    const el = rowRef.current;
    if (!el) return undefined;
    const io = new IntersectionObserver(([e]) => setOnScreen(e.isIntersecting), { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <section id="skills" className="section skills">
      <div className="container skills-head">
        <SectionHeading number="06" title="Skills" align="right" />
      </div>
      <div className="container">
        <div className="skill-cols" data-reveal="1">
          {SKILL_GROUPS.map((g) => (
            <div key={g.title} className="skill-col">
              <h3 className="skill-col-title">{g.title}</h3>
              <ul className="skill-list">
                {g.items.map((it) => (
                  <li key={it.t} className={`skill-item${it.core ? ' is-core' : ''}`}>
                    <span className="skill-item-dot" aria-hidden="true" />
                    <span>{it.t}</span>
                    {it.core ? <span className="skill-core-tag">core</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p className="skill-legend" data-reveal="1"><span className="skill-core-tag">core</span> used in production in the last twelve months.</p>
      </div>
      <div ref={rowRef} className={`marquee-row marquee-single${onScreen ? '' : ' is-paused'}`} aria-hidden="true" data-reveal="1">
        {GROUPS.map((g) => (
          <div key={g} className="marquee-group" style={{ animationDuration: '52s' }}>
            {SKILL_MARQUEE.map((chip) => <Chip key={chip.t} chip={chip} />)}
          </div>
        ))}
      </div>
    </section>
  );
}

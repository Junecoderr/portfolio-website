import SectionHeading from '../components/SectionHeading.jsx';
import { SKILL_ROWS } from '../data/content.js';

const GROUPS = [0, 1, 2, 3];

function Chip({ chip }) {
  return (
    <div className="chip">
      {chip.img ? <span className="chip-icon" style={{ backgroundImage: `url('${chip.img}')` }} /> : null}
      {chip.g ? <span className="chip-glyph" style={{ color: chip.c }}>{chip.g}</span> : null}
      <span className="chip-text">{chip.t}</span>
    </div>
  );
}

export default function Skills() {
  return (
    <section id="skills" className="section skills">
      <div className="container skills-head">
        <SectionHeading number="03" title="Skills" align="right" />
      </div>
      <div className="marquee-stack">
        {SKILL_ROWS.map((row, i) => (
          <div key={i} className="marquee-row" data-reveal="1">
            {GROUPS.map((g) => (
              <div
                key={g}
                className="marquee-group"
                style={{ animationDuration: row.duration, animationDirection: row.reverse ? 'reverse' : 'normal' }}
                aria-hidden={g > 0}
              >
                {row.chips.map((chip) => <Chip key={chip.t} chip={chip} />)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

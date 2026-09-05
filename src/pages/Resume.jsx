import PageShell from './PageShell.jsx';
import { HERO, EXPERIENCE, RECOGNITION, SKILL_GROUPS, STATS, EMAIL, LOCATION, SOCIALS, SITE_URL } from '../data/content.js';

export default function Resume() {
  return (
    <PageShell title="Tanisha Brahma" lead={HERO.role} wide>
      <div className="resume">
        <div className="resume-head">
          <div className="resume-contact">
            <a href={`mailto:${EMAIL}`}>{EMAIL}</a>
            <span>{LOCATION.label}</span>
            <a href={SITE_URL}>{SITE_URL.replace(/^https?:\/\//, '')}</a>
            {SOCIALS.filter((s) => s.href.startsWith('http')).map((s) => (
              <a key={s.label} href={s.href}>{s.href.replace(/^https?:\/\/(www\.)?/, '')}</a>
            ))}
          </div>
          <button type="button" className="resume-print" onClick={() => window.print()}>Print / Save as PDF</button>
        </div>

        <ul className="resume-stats">
          {STATS.map((s) => <li key={s.label}><strong>{s.value}</strong> {s.label}</li>)}
        </ul>

        <h2>Experience</h2>
        {EXPERIENCE.map((x) => (
          <section key={x.org} className="resume-xp">
            <div className="resume-xp-head">
              <h3>{x.org}</h3>
              <span>{x.period}</span>
            </div>
            <p className="resume-role">{x.role}</p>
            <ul>{x.points.map((p) => <li key={p}>{p}</li>)}</ul>
            <p className="resume-tags">{x.tags.join(' · ')}</p>
          </section>
        ))}

        <h2>Skills</h2>
        <dl className="resume-skills">
          {SKILL_GROUPS.map((g) => (
            <div key={g.title}>
              <dt>{g.title}</dt>
              <dd>{g.items.map((it) => it.t).join(', ')}</dd>
            </div>
          ))}
        </dl>

        {RECOGNITION.map((g) => (
          <section key={g.title}>
            <h2>{g.title}</h2>
            <ul className="resume-list">
              {g.items.map((it) => (
                <li key={it.detail + it.name}><strong>{it.detail}</strong> — {it.name} <span>({it.meta})</span></li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </PageShell>
  );
}

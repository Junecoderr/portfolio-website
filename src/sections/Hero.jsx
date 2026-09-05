import { HERO, RESUME_URL } from '../data/content.js';
import { ArrowUpRight } from '../components/Icons.jsx';

export default function Hero() {
  return (
    <section id="hero" className="hero">
      <div className="container">
        <a href="#contact" className="hero-status" data-reveal="1">
          <span className="status-dot" />
          <span>{HERO.status} · {HERO.where}</span>
        </a>
        <h1 className="hero-title" data-reveal="1">Tanisha Brahma</h1>
        <p className="hero-role" data-reveal="1">{HERO.role}</p>
        <ul className="hero-proof" data-reveal="1">
          {HERO.proof.map((p) => <li key={p}>{p}</li>)}
        </ul>
        <div className="hero-links" data-reveal="1">
          <a href="#work" className="hero-link">See the work <ArrowUpRight size={14} /></a>
          {RESUME_URL ? (
            <a href={RESUME_URL} className="hero-link" target="_blank" rel="noopener noreferrer">Resume <ArrowUpRight size={14} /></a>
          ) : (
            <a href="#contact" className="hero-link">Contact</a>
          )}
        </div>
      </div>
    </section>
  );
}

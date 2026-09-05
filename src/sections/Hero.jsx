import { useEffect, useState } from 'react';
import { HERO, HERO_WORDS, RESUME_URL } from '../data/content.js';
import { ArrowUpRight } from '../components/Icons.jsx';

export default function Hero() {
  const [word, setWord] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setWord((w) => (w + 1) % HERO_WORDS.length), 2500);
    return () => clearInterval(id);
  }, []);

  return (
    <section id="hero" className="hero">
      <div className="hero-status" data-reveal="1">
        <span className="status-dot" />
        <span>{HERO.status}</span>
        <span className="hero-status-sep">·</span>
        <span>{HERO.where}</span>
      </div>
      <h1 className="hero-title" data-reveal="1">Tanisha Brahma</h1>
      <p className="hero-role" data-reveal="1">{HERO.role}</p>
      <div className="hero-links" data-reveal="1">
        <a href="#contact" className="hero-link">Contact Me</a>
        <span className="hero-sep">|</span>
        {RESUME_URL ? (
          <a href={RESUME_URL} className="hero-link" target="_blank" rel="noopener noreferrer">View Resume <ArrowUpRight size={14} /></a>
        ) : (
          <a href="#work" className="hero-link">See the work <ArrowUpRight size={14} /></a>
        )}
      </div>
      <div className="hero-rotator" aria-live="polite">
        <span className="hero-rotator-stay">Stay</span>
        <span className="hero-rotator-slot">
          <span key={word} className="hero-rotator-word">{HERO_WORDS[word]}</span>
        </span>
      </div>
      <a href="#about" className="scroll-cue" aria-label="Scroll to About">
        <span className="scroll-cue-line" />
      </a>
    </section>
  );
}

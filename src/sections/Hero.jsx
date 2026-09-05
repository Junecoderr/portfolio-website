import { useEffect, useState } from 'react';
import { HERO_WORDS } from '../data/content.js';
import { ArrowUpRight } from '../components/Icons.jsx';

export default function Hero() {
  const [word, setWord] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setWord((w) => (w + 1) % HERO_WORDS.length), 2500);
    return () => clearInterval(id);
  }, []);

  return (
    <section id="hero" className="hero">
      <h1 className="hero-title">Tanisha Brahma</h1>
      <div className="hero-links">
        <a href="#contact" className="hero-link">Contact Me</a>
        <span className="hero-sep">|</span>
        <a href="#" className="hero-link">View Resume <ArrowUpRight size={14} /></a>
      </div>
      <div className="hero-rotator" aria-live="polite">
        <span className="hero-rotator-stay">Stay</span>
        <span className="hero-rotator-slot">
          <span key={word} className="hero-rotator-word">{HERO_WORDS[word]}</span>
        </span>
      </div>
    </section>
  );
}

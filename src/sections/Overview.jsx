import { onNav } from '../components/Header.jsx';

export default function Overview() {
  return (
    <section id="overview" className="section-hero">
      <div className="section-inner">
        <div className="eyebrow-row reveal">
          <span className="eyebrow-dot" />
          <span>00 — Overview</span>
        </div>
        <h1 className="hero-title reveal">I make attacks visible, expensive, and short-lived.</h1>
        <p className="hero-lead reveal">
          I work both sides of the console: the analyst seat where alerts get a verdict, and the engineering seat
          where the detections, pipelines, and cryptographic plumbing behind them are built. Blue team by trade,
          cryptography by training.
        </p>
        <div className="hero-actions reveal">
          <a href="#contact" onClick={onNav} className="cta-primary">
            Book a call
          </a>
          <a href="#writing" onClick={onNav} className="cta-secondary">
            Read my writing
          </a>
          <a href="#why" onClick={onNav} className="cta-quiet">
            explore ↓
          </a>
        </div>
      </div>
      <div className="image-band reveal" aria-hidden="true">
        <span className="image-band-label">band image — packet-flow abstract · 2400 × 520</span>
      </div>
    </section>
  );
}

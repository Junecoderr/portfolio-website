import { FOOTER_NAV, SOCIALS } from '../data/content.js';
import { ArrowUp } from './Icons.jsx';

const scrollTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });

export default function Footer() {
  return (
    <footer className="footer">
      <div className="footer-inner">
        <div className="footer-grid">
          <div className="footer-brand">
            <button type="button" className="footer-top" onClick={scrollTop} aria-label="Scroll to top">
              <ArrowUp size={14} />
            </button>
            <svg className="footer-word footer-word-first" viewBox="0 0 400 130" preserveAspectRatio="none" aria-hidden="true">
              <text x="0" y="110" textLength="400" lengthAdjust="spacingAndGlyphs" fontSize="130">Tanisha</text>
            </svg>
          </div>
          <div className="footer-col">
            <span className="footer-label">Navigation</span>
            <div className="footer-links">
              {FOOTER_NAV.map((n) => <a key={n.label} href={n.href}>{n.label}</a>)}
            </div>
          </div>
          <div className="footer-divider" />
          <div className="footer-col">
            <span className="footer-label">Social</span>
            <div className="footer-links">
              {SOCIALS.map((s) => (
                <a key={s.label} href={s.href} target={s.href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">{s.label}</a>
              ))}
            </div>
          </div>
          <div className="footer-divider" />
          <div className="footer-col footer-connect">
            <div>
              <span className="footer-label">Let's connect ✦</span>
              <p>I'm always open to discussing new projects, creative ideas or opportunities to be part of your visions.</p>
            </div>
            <a href="#contact" className="footer-hello">Say hello</a>
          </div>
        </div>
        <svg className="footer-word footer-word-last" viewBox="0 0 1000 220" preserveAspectRatio="none" aria-hidden="true">
          <text x="0" y="180" textLength="1000" lengthAdjust="spacingAndGlyphs" fontSize="220">Brahma</text>
        </svg>
        <div className="signature-rule strong">
          <span className="signature">Tanisha Brahma</span>
        </div>
        <div className="footer-legal">
          <span>© 2026 Tanisha Brahma. Design adapted from aarab.me (MIT), used with attribution.</span>
          <a href="https://www.aarab.me/license" target="_blank" rel="noopener noreferrer">LICENSE</a>
        </div>
      </div>
    </footer>
  );
}

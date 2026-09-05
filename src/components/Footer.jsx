import { FOOTER_NAV, SOCIALS, FOOTER_BLURB } from '../data/content.js';
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
            <span className="footer-word footer-word-first" aria-hidden="true">Tanisha</span>
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
              <p>{FOOTER_BLURB}</p>
            </div>
            <a href="#contact" className="footer-hello">Say hello</a>
          </div>
        </div>
        <span className="footer-word footer-word-last" aria-hidden="true">Brahma</span>
        <div className="signature-rule strong">
          <span className="signature">Tanisha Brahma</span>
        </div>
        <div className="footer-legal">
          <span>© 2026 Tanisha Brahma.</span>
          <span>Every claim on this site is one I can defend in an interview.</span>
        </div>
      </div>
    </footer>
  );
}

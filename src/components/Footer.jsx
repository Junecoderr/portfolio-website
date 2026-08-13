import { onNav } from './Header.jsx';

const EXPLORE_LINKS = [
  { href: '#overview', label: 'Overview' },
  { href: '#why', label: 'Why me' },
  { href: '#process', label: 'Process' },
  { href: '#writing', label: 'Writing' },
];

export default function Footer() {
  return (
    <footer id="contact" className="site-footer">
      <div className="footer-inner">
        <div className="section-kicker reveal" style={{ borderTop: 'none', paddingTop: 0 }}>
          Last — Contact
        </div>
        <h2 className="footer-title reveal">Tell me what is firing.</h2>
        <p className="footer-lead reveal">
          Open to SOC, detection engineering, and cryptography engineering roles, and to a conversation about an
          environment that has gone quiet in the wrong way.
        </p>
        <div className="footer-links reveal">
          <a href="mailto:you@example.com" className="footer-link">
            you@example.com
          </a>
          <a href="#contact" onClick={onNav} className="footer-link">
            LinkedIn ↗
          </a>
          <a href="#contact" onClick={onNav} className="footer-link">
            GitHub ↗
          </a>
          <a href="#contact" onClick={onNav} className="footer-link">
            PGP key
          </a>
        </div>
        <div className="footer-grid">
          <div>
            <div className="footer-col-label">Explore</div>
            <div className="footer-col-links">
              {EXPLORE_LINKS.map((link) => (
                <a key={link.href} href={link.href} onClick={onNav}>
                  {link.label}
                </a>
              ))}
            </div>
          </div>
          <div>
            <div className="footer-col-label">Focus</div>
            <div className="footer-col-body">
              <span>Security operations</span>
              <span>Detection engineering</span>
              <span>Applied cryptography</span>
            </div>
          </div>
          <div>
            <div className="footer-col-label">Availability</div>
            <div className="footer-col-body footer-availability">
              <span className="footer-availability-dot" />
              <span>Open to roles · remote or on-site</span>
            </div>
          </div>
        </div>
        <div className="footer-bottom">
          <span>© 2026 Your Name</span>
          <span>SOC · Cryptography · Detection</span>
        </div>
      </div>
    </footer>
  );
}

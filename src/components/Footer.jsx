import useClock from '../hooks/useClock.js';

export default function Footer() {
  const clock = useClock('IST');

  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <span className="footer-meta">© 2026 Tanisha Brahma</span>
        <span className="footer-meta">All project names, figures and CVE identifiers on this site are illustrative</span>
        <span className="footer-meta">{clock}</span>
      </div>
    </footer>
  );
}

import useClock from '../hooks/useClock.js';

export default function Footer() {
  const clock = useClock('IST');

  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <span className="footer-meta">© 2026 Tanisha Brahma</span>
        <span className="footer-meta">Every claim on this site is one I can defend in an interview</span>
        <span className="footer-meta">{clock}</span>
      </div>
    </footer>
  );
}

import { SOCIALS, EMAIL } from '../data/content.js';

export default function Footer() {
  return (
    <footer className="footer">
      <div className="container footer-row">
        <span>© 2026 Tanisha Brahma</span>
        <div className="footer-links">
          <a href={`mailto:${EMAIL}`}>{EMAIL}</a>
          {SOCIALS.filter((s) => s.href.startsWith('http')).map((s) => (
            <a key={s.label} href={s.href} target="_blank" rel="noopener noreferrer">{s.label}</a>
          ))}
        </div>
      </div>
    </footer>
  );
}

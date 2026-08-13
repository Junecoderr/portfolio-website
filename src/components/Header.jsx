const NAV_LINKS = [
  { href: '#overview', label: 'Overview' },
  { href: '#why', label: 'Why me' },
  { href: '#process', label: 'Process' },
  { href: '#writing', label: 'Writing' },
];

export function onNav(e) {
  const href = e.currentTarget.getAttribute('href') || '';
  if (href.charAt(0) !== '#') return;
  e.preventDefault();
  const el = document.getElementById(href.slice(1));
  if (!el) return;
  const y = el.getBoundingClientRect().top + (window.scrollY || 0) - 84;
  window.scrollTo({ top: y, behavior: 'smooth' });
}

export default function Header() {
  return (
    <header className="site-header" data-nav>
      <div className="header-inner">
        <a href="#overview" onClick={onNav} className="brand">
          <span className="brand-name">Tanisha Brahma</span>
          <span className="brand-tag">SOC · Cryptography</span>
        </a>
        <nav className="site-nav">
          {NAV_LINKS.map((link) => (
            <a key={link.href} href={link.href} data-navlink onClick={onNav} className="nav-link">
              {link.label}
            </a>
          ))}
        </nav>
        <a href="#contact" onClick={onNav} className="cta-button">
          Book a call
        </a>
      </div>
    </header>
  );
}

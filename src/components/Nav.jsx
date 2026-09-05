import { useEffect, useRef, useState } from 'react';
import { NAV_SECTIONS, NAV_PAGES, SOCIALS } from '../data/content.js';
import { MenuIcon, CloseIcon } from './Icons.jsx';

export default function Nav({ menuOpen, onToggle, onClose, activeId }) {
  const [hidden, setHidden] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [progress, setProgress] = useState(0);
  const lastY = useRef(0);

  useEffect(() => {
    const onScroll = () => {
      const y = window.scrollY || 0;
      const delta = y - lastY.current;
      if (y <= 10) setHidden(false);
      else if (Math.abs(delta) > 5) setHidden(delta > 0);
      setScrolled(y > 10);
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(1, y / max) : 0);
      lastY.current = y;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <>
      <nav className={`nav${hidden ? ' is-hidden' : ''}`}>
        <div className={`nav-bar${scrolled ? ' is-scrolled' : ''}`}>
          <a href="#hero" className="brand">
            <span className="brand-mark">TB</span>
            <span className="brand-name">Tanisha Brahma.</span>
          </a>
          <div className="nav-inline">
            {NAV_SECTIONS.filter((n) => ['Work', 'About', 'Experience', 'Contact'].includes(n.label)).map((n) => (
              <a key={n.href} href={n.href} className={`nav-inline-link${activeId === n.id ? ' is-active' : ''}`} aria-current={activeId === n.id ? 'true' : undefined}>{n.label}</a>
            ))}
          </div>
          <button type="button" className="nav-menu-btn" onClick={onToggle} aria-expanded={menuOpen}>
            <span>{menuOpen ? 'Close' : 'Menu'}</span>
            {menuOpen ? <CloseIcon stroke="#E5203F" /> : <MenuIcon />}
          </button>
          <span className="nav-progress" style={{ transform: `scaleX(${progress})` }} aria-hidden="true" />
        </div>
      </nav>

      {menuOpen ? <div className="drawer-scrim" onClick={onClose} /> : null}
      <aside className={`drawer${menuOpen ? ' is-open' : ''}`} {...(menuOpen ? {} : { inert: '' })}>
        <div className="drawer-head">
          <span className="eyebrow">Navigation</span>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close menu">
            <CloseIcon />
          </button>
        </div>
        <div className="drawer-body">
          <div className="drawer-group">
            <span className="eyebrow soft">Sections</span>
            {NAV_SECTIONS.map((n) => (
              <a key={n.href} href={n.href} className={`drawer-link${activeId === n.id ? ' is-active' : ''}`} onClick={onClose} aria-current={activeId === n.id ? 'true' : undefined}>
                <span className="drawer-link-label">{n.label}</span>
                <span className="drawer-link-index">{n.index}</span>
              </a>
            ))}
          </div>
          {NAV_PAGES.length ? (
          <div className="drawer-group is-pages">
            <span className="eyebrow soft">Pages</span>
            {NAV_PAGES.map((n) => (
              <a key={n.label} href={n.href} className="drawer-link" onClick={onClose}>
                <span className="drawer-link-label sm">{n.label}</span>
                <span className="drawer-link-index">{n.index}</span>
              </a>
            ))}
          </div>
          ) : null}
        </div>
        <div className="drawer-foot">
          <span className="eyebrow soft">Socials</span>
          <div className="drawer-socials">
            {SOCIALS.map((s) => (
              <a key={s.label} href={s.href} target={s.href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">
                {s.label}
              </a>
            ))}
          </div>
        </div>
      </aside>
    </>
  );
}

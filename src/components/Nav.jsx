import { NAV_SECTIONS, SOCIALS } from '../data/content.js';
import { MenuIcon, CloseIcon } from './Icons.jsx';

export default function Nav({ menuOpen, onToggle, onClose, activeId }) {
  return (
    <>
      <header className="nav">
        <div className="container nav-bar">
          <a href="#hero" className="brand">Tanisha Brahma</a>
          <nav className="nav-links" aria-label="Sections">
            {NAV_SECTIONS.map((n) => (
              <a key={n.id} href={n.href} className={`nav-link${activeId === n.id ? ' is-active' : ''}`} aria-current={activeId === n.id ? 'true' : undefined}>{n.label}</a>
            ))}
          </nav>
          <button type="button" className="nav-menu-btn" onClick={onToggle} aria-expanded={menuOpen} aria-label="Open menu">
            <MenuIcon />
          </button>
        </div>
      </header>

      {menuOpen ? <div className="drawer-scrim" onClick={onClose} /> : null}
      <aside className={`drawer${menuOpen ? ' is-open' : ''}`} aria-hidden={!menuOpen}>
        <div className="drawer-head">
          <span className="label">Menu</span>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close menu"><CloseIcon /></button>
        </div>
        {NAV_SECTIONS.map((n) => (
          <a key={n.id} href={n.href} className={`drawer-link${activeId === n.id ? ' is-active' : ''}`} onClick={onClose}>{n.label}</a>
        ))}
        <div className="drawer-foot">
          {SOCIALS.map((s) => (
            <a key={s.label} href={s.href} target={s.href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">{s.label}</a>
          ))}
        </div>
      </aside>
    </>
  );
}

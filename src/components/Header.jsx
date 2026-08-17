const NAV_ITEMS = [
  { key: 'home', label: 'Index', href: '#top' },
  { key: 'work', label: 'Work', href: '#work' },
  { key: 'about', label: 'About', href: '#about' },
];

const LEVELS = ['low', 'med', 'high'];

export default function Header({ route, onNavigate, entered, sceneOn, sceneLevel, onToggleScene, onSetLevel }) {
  return (
    <header className={`site-header${entered ? ' is-entered' : ''}`}>
      <div className="header-inner">
        <a
          href="#top"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            onNavigate('home');
          }}
        >
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Tanisha Brahma</span>
        </a>

        <div className="field-control">
          <span className="field-label">Field</span>
          <button type="button" className="field-toggle" onClick={onToggleScene}>
            {sceneOn ? 'On' : 'Off'}
          </button>
          {sceneOn ? (
            <div className="field-levels">
              {LEVELS.map((l) => (
                <button
                  key={l}
                  type="button"
                  className={`field-level${l === sceneLevel ? ' is-active' : ''}`}
                  onClick={() => onSetLevel(l)}
                >
                  {l}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <nav className="site-nav">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.key}
              href={item.href}
              className={`nav-link${route === item.key ? ' is-active' : ''}`}
              onClick={(e) => {
                e.preventDefault();
                onNavigate(item.key);
              }}
            >
              {route === item.key ? <span className="nav-dot" /> : null}
              {item.label}
            </a>
          ))}
          <a
            href="#contact"
            className="nav-contact"
            onClick={(e) => {
              e.preventDefault();
              onNavigate('contact');
            }}
          >
            Contact
          </a>
        </nav>
      </div>
    </header>
  );
}

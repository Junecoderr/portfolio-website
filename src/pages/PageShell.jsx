import Footer from '../components/Footer.jsx';

/** Minimal shell for secondary pages: brand link, content, footer. */
export default function PageShell({ title, lead, children, wide = false }) {
  return (
    <div className="page subpage">
      <a href="#main" className="skip-link">Skip to content</a>
      <header className="subpage-nav">
        <div className="container subpage-nav-inner">
          <a href="/" className="brand"><span className="brand-mark">TB</span><span className="brand-name">Tanisha Brahma.</span></a>
          <a href="/" className="subpage-back">← Back to site</a>
        </div>
      </header>
      <main id="main" className={`subpage-main${wide ? ' is-wide' : ''}`}>
        <div className="container">
          <h1 className="subpage-title">{title}</h1>
          {lead ? <p className="subpage-lead">{lead}</p> : null}
          {children}
        </div>
      </main>
      <Footer />
    </div>
  );
}

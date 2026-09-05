import { useCallback, useEffect, useRef, useState } from 'react';
import Background from './components/Background.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import Preloader from './components/Preloader.jsx';
import Nav from './components/Nav.jsx';
import Footer from './components/Footer.jsx';
import CaseDialog from './components/CaseDialog.jsx';
import Hero from './sections/Hero.jsx';
import Work from './sections/Work.jsx';
import About from './sections/About.jsx';
import Experience from './sections/Experience.jsx';
import Recognition from './sections/Recognition.jsx';
import Writing from './sections/Writing.jsx';
import Skills from './sections/Skills.jsx';
import Contact from './sections/Contact.jsx';
import { PROJECTS, NAV_SECTIONS } from './data/content.js';
import { caseIdFromPath } from './seo.js';
import useScrollReveal from './hooks/useScrollReveal.js';

const HASH_RE = /^#work\/([a-z0-9-]+)$/;

/** Case id from the current URL: /work/<id> (prerendered pages) or the legacy #work/<id> hash. */
function caseFromLocation() {
  const byPath = caseIdFromPath(window.location.pathname);
  if (byPath) return byPath;
  const m = HASH_RE.exec(window.location.hash);
  return m ? caseIdFromPath(`/work/${m[1]}`) : null;
}

export default function App({ initialCase = null }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [caseId, setCaseId] = useState(initialCase);
  const [activeId, setActiveId] = useState('hero');
  const rootRef = useRef(null);
  const skipPreloader = Boolean(initialCase);
  useScrollReveal(rootRef);

  // Body scroll lock while a case is open.
  useEffect(() => {
    document.body.style.overflow = caseId ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [caseId]);

  // Back/forward and manual URL edits drive the dialog.
  useEffect(() => {
    const sync = () => setCaseId(caseFromLocation());
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => {
      window.removeEventListener('popstate', sync);
      window.removeEventListener('hashchange', sync);
    };
  }, []);

  // Active section for the nav.
  useEffect(() => {
    const els = NAV_SECTIONS.map((n) => document.getElementById(n.id)).filter(Boolean);
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      { rootMargin: '-40% 0px -50% 0px', threshold: [0, 0.1, 0.25, 0.5] }
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  const openCase = useCallback((id) => {
    window.history.pushState({ case: id }, '', `/work/${id}`);
    setCaseId(id);
  }, []);

  const closeCase = useCallback(() => {
    if (window.history.state && window.history.state.case) {
      window.history.back();
    } else {
      window.history.replaceState(null, '', '/#work');
      setCaseId(null);
    }
  }, []);

  const project = caseId ? PROJECTS.find((p) => p.id === caseId) : null;

  return (
    <div className="page" ref={rootRef}>
      <a href="#main" className="skip-link">Skip to content</a>
      <ErrorBoundary fallback={null}>
        <Background motion />
      </ErrorBoundary>
      <Preloader skip={skipPreloader} />
      <Nav menuOpen={menuOpen} onToggle={() => setMenuOpen((v) => !v)} onClose={() => setMenuOpen(false)} activeId={activeId} />
      <main id="main" className="main">
        <Hero />
        <div className="content-band">
          <Work onOpenCase={openCase} />
          <About />
          <Experience />
          <Recognition />
          <Writing />
          <Skills />
          <Contact />
        </div>
        <Footer />
      </main>
      <CaseDialog project={project} onClose={closeCase} />
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from 'react';
import Background from './components/Background.jsx';
import Preloader from './components/Preloader.jsx';
import Nav from './components/Nav.jsx';
import Footer from './components/Footer.jsx';
import CaseDialog from './components/CaseDialog.jsx';
import Hero from './sections/Hero.jsx';
import About from './sections/About.jsx';
import Skills from './sections/Skills.jsx';
import Experience from './sections/Experience.jsx';
import Work from './sections/Work.jsx';
import Contact from './sections/Contact.jsx';
import { PROJECTS } from './data/content.js';
import useScrollReveal from './hooks/useScrollReveal.js';

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [caseId, setCaseId] = useState(null);
  const rootRef = useRef(null);
  useScrollReveal(rootRef);

  useEffect(() => {
    document.body.style.overflow = caseId ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [caseId]);

  const openCase = useCallback((id) => setCaseId(id), []);
  const closeCase = useCallback(() => setCaseId(null), []);
  const project = caseId ? PROJECTS.find((p) => p.id === caseId) : null;

  return (
    <div className="page" ref={rootRef}>
      <Background motion />
      <Preloader />
      <Nav menuOpen={menuOpen} onToggle={() => setMenuOpen((v) => !v)} onClose={() => setMenuOpen(false)} />
      <main className="main">
        <Hero />
        <div className="content-band">
          <About paused={Boolean(caseId)} />
          <Skills />
          <Experience />
          <Work onOpenCase={openCase} />
          <Contact />
        </div>
        <Footer />
      </main>
      <CaseDialog project={project} onClose={closeCase} />
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from 'react';
import Header from './components/Header.jsx';
import Footer from './components/Footer.jsx';
import Preloader from './components/Preloader.jsx';
import LatticeScene from './components/backgrounds/LatticeScene.jsx';
import Home from './sections/Home.jsx';
import Work from './sections/Work.jsx';
import CaseStudy from './sections/CaseStudy.jsx';
import About from './sections/About.jsx';
import Contact from './sections/Contact.jsx';
import useScrollReveal from './hooks/useScrollReveal.js';

const LEVELS = { low: 0.4, med: 0.68, high: 0.95 };

export default function App() {
  const rootRef = useRef(null);
  const [route, setRoute] = useState('home');
  const [activeId, setActiveId] = useState('blackout');
  const [sceneOn, setSceneOn] = useState(true);
  const [sceneLevel, setSceneLevel] = useState('high');
  const [sceneReady, setSceneReady] = useState(false);
  const [entered, setEntered] = useState(false);

  useScrollReveal(rootRef, route);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [route]);

  const navigate = useCallback((next) => setRoute(next), []);
  const openCase = useCallback((id) => {
    setActiveId(id);
    setRoute('case');
  }, []);

  const handleSceneReady = useCallback(() => setSceneReady(true), []);
  const handleEnter = useCallback(() => setEntered(true), []);

  return (
    <Preloader ready={sceneReady} onEnter={handleEnter}>
      <div ref={rootRef} className="page">
        {sceneOn ? (
          <LatticeScene config={route} intensity={LEVELS[sceneLevel]} onReady={handleSceneReady} />
        ) : null}

        <Header
          route={route}
          onNavigate={navigate}
          entered={entered}
          sceneOn={sceneOn}
          sceneLevel={sceneLevel}
          onToggleScene={() => setSceneOn((v) => !v)}
          onSetLevel={setSceneLevel}
        />

        {route === 'home' && <Home onOpenCase={openCase} onNavigate={navigate} />}
        {route === 'work' && <Work onOpenCase={openCase} />}
        {route === 'case' && <CaseStudy activeId={activeId} onOpenCase={openCase} onNavigate={navigate} />}
        {route === 'about' && <About />}
        {route === 'contact' && <Contact />}

        <Footer />
      </div>
    </Preloader>
  );
}

import { useRef } from 'react';
import Header from './components/Header.jsx';
import Footer from './components/Footer.jsx';
import ParticleField3D from './components/backgrounds/ParticleField3D.jsx';
import Overview from './sections/Overview.jsx';
import WhyMe from './sections/WhyMe.jsx';
import Tooling from './sections/Tooling.jsx';
import Quote from './sections/Quote.jsx';
import Process from './sections/Process.jsx';
import Writing from './sections/Writing.jsx';
import useScrollReveal from './hooks/useScrollReveal.js';
import useScrollSpy from './hooks/useScrollSpy.js';

export default function App() {
  const rootRef = useRef(null);

  useScrollReveal(rootRef);
  useScrollSpy(rootRef);

  return (
    <div ref={rootRef} className="page">
      <div className="bg-fixed">
        <ParticleField3D />
      </div>
      <div className="bg-grid" aria-hidden="true" />

      <Header />

      <main style={{ position: 'relative', zIndex: 1 }}>
        <Overview />
        <WhyMe />
        <Tooling />
        <Quote />
        <Process />
        <Writing />
      </main>

      <Footer />
    </div>
  );
}

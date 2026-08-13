import { useEffect } from 'react';

const SECTION_IDS = ['overview', 'why', 'process', 'writing'];

/**
 * Drives the header scrolled-state, the process rail fill/counter, and the
 * active nav link — all off a single passive scroll listener, mirroring the
 * source design's onScroll handler.
 */
export default function useScrollSpy(containerRef) {
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return undefined;

    const header = root.querySelector('[data-nav]');
    const rail = root.querySelector('[data-rail]');
    const counter = root.querySelector('[data-step-count]');
    const steps = Array.from(root.querySelectorAll('[data-step]'));
    const links = Array.from(root.querySelectorAll('[data-navlink]'));

    const onScroll = () => {
      const y = window.scrollY || 0;

      if (header) {
        const on = y > 24;
        header.classList.toggle('is-scrolled', on);
      }

      let reached = 0;
      steps.forEach((el, i) => {
        const r = el.getBoundingClientRect();
        const active = r.top < window.innerHeight * 0.62 && r.bottom > 80;
        const passed = r.top < window.innerHeight * 0.62;
        if (passed) reached = i + 1;
        el.classList.toggle('is-active', active || passed);
      });
      if (rail) rail.style.width = `${(reached / steps.length) * 100}%`;
      if (counter) counter.textContent = `${Math.max(reached, 1)} / ${steps.length}`;

      let active = SECTION_IDS[0];
      SECTION_IDS.forEach((id) => {
        const s = document.getElementById(id);
        if (s && s.getBoundingClientRect().top < window.innerHeight * 0.42) active = id;
      });
      links.forEach((a) => {
        const on = a.getAttribute('href') === `#${active}`;
        a.classList.toggle('is-active', on);
      });
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    onScroll();

    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, [containerRef]);
}

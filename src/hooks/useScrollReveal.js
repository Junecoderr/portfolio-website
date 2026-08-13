import { useEffect } from 'react';

/**
 * Reveals every `.reveal` element inside `containerRef` once it enters the
 * viewport: fade + rise, staggered by sibling index (max 5 x 80ms). Fires
 * once per element and is a no-op under prefers-reduced-motion.
 */
export default function useScrollReveal(containerRef) {
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return undefined;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const revealables = Array.from(root.querySelectorAll('.reveal'));
    if (reduced || !('IntersectionObserver' in window)) {
      revealables.forEach((el) => el.classList.add('is-visible'));
      return undefined;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const el = entry.target;
          const siblings = el.parentElement
            ? Array.from(el.parentElement.children).filter((n) => n.classList.contains('reveal'))
            : [el];
          const delay = Math.min(siblings.indexOf(el), 5) * 80;
          el.style.transition = `opacity 620ms cubic-bezier(0,0,.58,1) ${delay}ms, transform 720ms cubic-bezier(.165,.84,.44,1) ${delay}ms`;
          el.classList.add('is-visible');
          io.unobserve(el);
        });
      },
      { rootMargin: '0px 0px -26% 0px', threshold: 0.01 }
    );
    revealables.forEach((el) => io.observe(el));

    return () => io.disconnect();
  }, [containerRef]);
}

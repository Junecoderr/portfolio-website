import { useEffect } from 'react';

/**
 * Reveals every `[data-reveal]` element inside `containerRef`: fade + rise,
 * staggered in groups of 4 x 70ms, elements already on-screen animate in on
 * a double-RAF, elements below the fold animate via IntersectionObserver.
 * Re-arms on `route` change. No-op under prefers-reduced-motion.
 */
export default function useScrollReveal(containerRef, route) {
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return undefined;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) return undefined;

    let seq = 0;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.style.opacity = '1';
          entry.target.style.transform = 'translateY(0)';
          io.unobserve(entry.target);
        });
      },
      { rootMargin: '0px 0px -10% 0px', threshold: 0.04 }
    );

    const prep = () => {
      const els = root.querySelectorAll('[data-reveal]:not([data-revealed])');
      els.forEach((el) => {
        el.setAttribute('data-revealed', '');
        const box = el.getBoundingClientRect();
        const onscreen = box.top < window.innerHeight * 0.92;
        el.style.transition = 'opacity 900ms cubic-bezier(.16,1,.3,1), transform 900ms cubic-bezier(.16,1,.3,1)';
        el.style.transitionDelay = `${(seq++ % 4) * 70}ms`;
        if (onscreen) {
          el.style.opacity = '0';
          el.style.transform = 'translateY(24px)';
          requestAnimationFrame(() => requestAnimationFrame(() => {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
          }));
        } else {
          el.style.opacity = '0';
          el.style.transform = 'translateY(28px)';
          io.observe(el);
        }
      });
    };
    const timers = [0, 60, 200, 500, 1000].map((ms) => setTimeout(prep, ms));

    return () => {
      timers.forEach(clearTimeout);
      io.disconnect();
    };
  }, [containerRef, route]);
}

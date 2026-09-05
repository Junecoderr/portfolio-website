import { useEffect } from 'react';

/**
 * Reveals every `[data-reveal]` element inside `containerRef`: fade + rise,
 * staggered in groups of four. Elements already on screen animate on a
 * double-RAF; the rest wait for IntersectionObserver. No-op under
 * prefers-reduced-motion.
 */
export default function useScrollReveal(containerRef) {
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return undefined;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;

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
      root.querySelectorAll('[data-reveal]:not([data-revealed])').forEach((el) => {
        el.setAttribute('data-revealed', '');
        const onscreen = el.getBoundingClientRect().top < window.innerHeight * 0.92;
        el.style.transition = 'opacity 900ms cubic-bezier(.16,1,.3,1), transform 900ms cubic-bezier(.16,1,.3,1)';
        el.style.transitionDelay = `${(seq++ % 4) * 70}ms`;
        el.style.opacity = '0';
        el.style.transform = onscreen ? 'translateY(24px)' : 'translateY(28px)';
        if (onscreen) {
          requestAnimationFrame(() => requestAnimationFrame(() => {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
          }));
        } else {
          io.observe(el);
        }
      });
    };
    const timers = [0, 60, 200, 500, 1000, 2400].map((ms) => setTimeout(prep, ms));

    return () => {
      timers.forEach(clearTimeout);
      io.disconnect();
    };
  }, [containerRef]);
}

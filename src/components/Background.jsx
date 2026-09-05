import { useEffect, useState } from 'react';

/** Cherry palette for the grain gradient. Low intensity, slow drift. */
const PALETTE = ['hsl(348, 78%, 44%)', 'hsl(352, 70%, 28%)', 'hsl(345, 85%, 56%)'];

/**
 * Fixed backdrop: a dynamic grain gradient loaded after first paint, with a CSS
 * blob fallback underneath and a dark veil on top so text stays quiet.
 */
export default function Background({ motion = true }) {
  const [shaderOn, setShaderOn] = useState(false);
  const [webgl, setWebgl] = useState(false);
  const [active, setActive] = useState(true);
  const [Shader, setShader] = useState(null);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setActive(!mq.matches && !document.hidden);
    update();
    mq.addEventListener('change', update);
    document.addEventListener('visibilitychange', update);
    return () => {
      mq.removeEventListener('change', update);
      document.removeEventListener('visibilitychange', update);
    };
  }, []);

  useEffect(() => {
    try {
      const c = document.createElement('canvas');
      setWebgl(Boolean(c.getContext('webgl2') || c.getContext('webgl')));
    } catch {
      setWebgl(false);
    }
  }, []);

  useEffect(() => {
    if (!webgl) return undefined;
    let cancelled = false;
    const load = () => import('@paper-design/shaders-react').then((m) => {
      if (cancelled) return;
      setShader(() => m.GrainGradient);
      setTimeout(() => setShaderOn(true), 400);
    }).catch(() => {});
    const idle = window.requestIdleCallback ? window.requestIdleCallback(load, { timeout: 1500 }) : setTimeout(load, 300);
    return () => {
      cancelled = true;
      if (window.cancelIdleCallback) window.cancelIdleCallback(idle);
      else clearTimeout(idle);
    };
  }, [webgl]);

  const moving = motion && active;

  return (
    <div className="bg" aria-hidden="true">
      {webgl && Shader ? (
        <div className="bg-shader">
          <Shader
            style={{ width: '100%', height: '100%' }}
            colorBack="hsl(0, 0%, 0%)"
            softness={0.75}
            intensity={0.32}
            noise={0.15}
            shape="corners"
            offsetX={0}
            offsetY={0}
            scale={1.1}
            rotation={0}
            speed={moving ? 0.5 : 0}
            colors={PALETTE}
          />
        </div>
      ) : null}
      <div className="bg-fallback" style={{ opacity: shaderOn ? 0 : 1 }}>
        <div className={`bg-blob bg-blob-a${moving ? ' is-animated' : ''}`} />
        <div className={`bg-blob bg-blob-b${moving ? ' is-animated' : ''}`} />
      </div>
      <div className="bg-veil" />
    </div>
  );
}

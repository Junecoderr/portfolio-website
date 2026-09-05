import { useEffect, useState } from 'react';

const PALETTE = ['hsl(348, 78%, 44%)', 'hsl(352, 70%, 28%)', 'hsl(345, 85%, 56%)'];

/**
 * Fixed full-page backdrop. Grain-gradient shader on top of a CSS blob
 * fallback that stays visible until the shader reports its first frame,
 * so a WebGL failure still leaves the page lit.
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

  // Load the shader after first paint so it never blocks the initial render.
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

  return (
    <div className="bg" aria-hidden="true">
      {webgl && Shader ? (
        <div className="bg-shader">
          <Shader
            style={{ width: '100%', height: '100%' }}
            colorBack="hsl(0, 0%, 0%)"
            softness={0.5}
            intensity={0.28}
            noise={0}
            shape="corners"
            offsetX={0}
            offsetY={0}
            scale={1}
            rotation={0}
            speed={motion && active ? 1 : 0}
            colors={PALETTE}
          />
        </div>
      ) : null}
      <div className="bg-fallback" style={{ opacity: shaderOn ? 0 : 1 }}>
        <div className={`bg-blob bg-blob-a${motion && active ? ' is-animated' : ''}`} />
        <div className={`bg-blob bg-blob-b${motion && active ? ' is-animated' : ''}`} />
        <div className="bg-blob bg-blob-c" />
        <div className="bg-noise" />
      </div>
    </div>
  );
}

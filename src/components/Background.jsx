import { useEffect, useState } from 'react';
import { GrainGradient } from '@paper-design/shaders-react';

const PALETTE = ['hsl(193, 85%, 66%)', 'hsl(196, 100%, 83%)', 'hsl(195, 100%, 50%)'];

/**
 * Fixed full-page backdrop. Grain-gradient shader on top of a CSS blob
 * fallback that stays visible until the shader reports its first frame,
 * so a WebGL failure still leaves the page lit.
 */
export default function Background({ motion = true }) {
  const [shaderOn, setShaderOn] = useState(false);
  const [webgl, setWebgl] = useState(true);
  const [active, setActive] = useState(true);

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
    const t = setTimeout(() => setShaderOn(true), 400);
    return () => clearTimeout(t);
  }, [webgl]);

  return (
    <div className="bg" aria-hidden="true">
      {webgl ? (
        <div className="bg-shader">
          <GrainGradient
            style={{ width: '100%', height: '100%' }}
            colorBack="hsl(0, 0%, 0%)"
            softness={0.5}
            intensity={0.3}
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

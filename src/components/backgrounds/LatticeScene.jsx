import { useEffect, useRef } from 'react';
import * as THREE from 'three';

const rng = (seed) => () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);

function buildConfig(name, n) {
  const pts = [];
  if (name === 'work') {
    const r = rng(21);
    const cols = Math.ceil(Math.sqrt(n * 1.6));
    const rows = Math.ceil(n / cols);
    for (let i = 0; i < n; i += 1) {
      const c = i % cols;
      const w = Math.floor(i / cols);
      pts.push([
        -6.1 + (c / (cols - 1)) * 12.2 + (r() - 0.5) * 0.18,
        3.0 - (w / Math.max(1, rows - 1)) * 6.0 + (r() - 0.5) * 0.18,
        (r() - 0.5) * 1.1,
      ]);
    }
  } else if (name === 'case') {
    const r = rng(77);
    for (let i = 0; i < n; i += 1) {
      const y = 1 - (i / (n - 1)) * 2;
      const rad = Math.sqrt(Math.max(0, 1 - y * y));
      const th = i * 2.399963;
      const k = 0.55 + r() * 1.05;
      pts.push([Math.cos(th) * rad * k * 1.6, y * k * 1.6, Math.sin(th) * rad * k * 1.6]);
    }
  } else if (name === 'about') {
    const r = rng(404);
    for (let i = 0; i < n; i += 1) pts.push([(r() - 0.5) * 11.4, (r() - 0.5) * 6.4, (r() - 0.5) * 5.8]);
  } else if (name === 'contact') {
    const r = rng(909);
    for (let i = 0; i < n; i += 1) {
      const th = r() * Math.PI * 2;
      const ph = Math.acos(2 * r() - 1);
      const rad = 0.24 + Math.cbrt(r()) * 0.72;
      pts.push([Math.sin(ph) * Math.cos(th) * rad, Math.sin(ph) * Math.sin(th) * rad, Math.cos(ph) * rad]);
    }
  } else {
    const r = rng(7);
    const nx = 7;
    const ny = 5;
    const nz = Math.max(2, Math.ceil(n / (nx * ny)));
    for (let i = 0; i < n; i += 1) {
      const x = i % nx;
      const y = Math.floor(i / nx) % ny;
      const z = Math.floor(i / (nx * ny));
      pts.push([
        (x - (nx - 1) / 2) * 0.98 + (r() - 0.5) * 0.09,
        (y - (ny - 1) / 2) * 0.98 + (r() - 0.5) * 0.09,
        (z - (nz - 1) / 2) * 0.98 + (r() - 0.5) * 0.09,
      ]);
    }
  }
  return pts;
}

function buildPairs(pts, maxLines) {
  const out = [];
  const cand = [];
  for (let i = 0; i < pts.length; i += 1) {
    for (let j = i + 1; j < pts.length; j += 1) {
      const dx = pts[i][0] - pts[j][0];
      const dy = pts[i][1] - pts[j][1];
      const dz = pts[i][2] - pts[j][2];
      const d = dx * dx + dy * dy + dz * dz;
      if (d < 6.5) cand.push([i, j, d]);
    }
  }
  cand.sort((a, b) => a[2] - b[2]);
  const deg = new Array(pts.length).fill(0);
  for (const c of cand) {
    if (out.length >= maxLines) break;
    if (deg[c[0]] >= 4 || deg[c[1]] >= 4) continue;
    out.push([c[0], c[1]]);
    deg[c[0]] += 1;
    deg[c[1]] += 1;
  }
  return out;
}

function envCanvas() {
  const c = document.createElement('canvas');
  c.width = 512;
  c.height = 256;
  const g = c.getContext('2d');
  const base = g.createLinearGradient(0, 0, 0, 256);
  base.addColorStop(0, '#14131f');
  base.addColorStop(0.55, '#08070e');
  base.addColorStop(1, '#04040a');
  g.fillStyle = base;
  g.fillRect(0, 0, 512, 256);
  const blue = g.createRadialGradient(140, 78, 0, 140, 78, 165);
  blue.addColorStop(0, 'rgba(0,24,253,0.95)');
  blue.addColorStop(1, 'rgba(0,24,253,0)');
  g.fillStyle = blue;
  g.fillRect(0, 0, 512, 256);
  const mag = g.createRadialGradient(392, 176, 0, 392, 176, 175);
  mag.addColorStop(0, 'rgba(238,0,233,0.9)');
  mag.addColorStop(1, 'rgba(238,0,233,0)');
  g.fillStyle = mag;
  g.fillRect(0, 0, 512, 256);
  const key = g.createRadialGradient(268, 30, 0, 268, 30, 110);
  key.addColorStop(0, 'rgba(255,255,255,0.85)');
  key.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = key;
  g.fillRect(0, 0, 512, 256);
  return c;
}

function backdropCanvas() {
  const c = document.createElement('canvas');
  c.width = 1024;
  c.height = 640;
  const g = c.getContext('2d');
  g.fillStyle = '#08070E';
  g.fillRect(0, 0, 1024, 640);
  g.strokeStyle = 'rgba(255,255,255,0.05)';
  g.lineWidth = 1;
  for (let x = 0; x <= 1024; x += 32) {
    g.beginPath();
    g.moveTo(x, 0);
    g.lineTo(x, 640);
    g.stroke();
  }
  for (let y = 0; y <= 640; y += 32) {
    g.beginPath();
    g.moveTo(0, y);
    g.lineTo(1024, y);
    g.stroke();
  }
  const blue = g.createRadialGradient(300, 180, 0, 300, 180, 470);
  blue.addColorStop(0, 'rgba(0,24,253,0.15)');
  blue.addColorStop(1, 'rgba(0,24,253,0)');
  g.fillStyle = blue;
  g.fillRect(0, 0, 1024, 640);
  const mag = g.createRadialGradient(740, 470, 0, 740, 470, 470);
  mag.addColorStop(0, 'rgba(238,0,233,0.12)');
  mag.addColorStop(1, 'rgba(238,0,233,0)');
  g.fillStyle = mag;
  g.fillRect(0, 0, 1024, 640);
  const vio = g.createRadialGradient(512, 320, 0, 512, 320, 330);
  vio.addColorStop(0, 'rgba(122,18,245,0.07)');
  vio.addColorStop(1, 'rgba(122,18,245,0)');
  g.fillStyle = vio;
  g.fillRect(0, 0, 1024, 640);
  return c;
}

function hasWebGL() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch {
    return false;
  }
}

/**
 * Persistent glass-node lattice background. `config` picks the point-cloud
 * layout (home|work|case|about|contact), `intensity` (0..1) fades the canvas.
 * Falls back to a static blurred gradient under prefers-reduced-motion or
 * when WebGL is unavailable.
 */
export default function LatticeScene({ config = 'home', intensity = 1, onReady }) {
  const hostRef = useRef(null);
  const configRef = useRef(config);
  const applyConfigRef = useRef(null);

  useEffect(() => {
    configRef.current = config;
    applyConfigRef.current?.(config);
  }, [config]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || !hasWebGL()) {
      host.innerHTML = '';
      const d = document.createElement('div');
      d.style.cssText =
        'position:absolute;inset:0;background:' +
        'radial-gradient(46% 40% at 28% 22%, rgba(0,24,253,.40) 0%, rgba(0,24,253,0) 70%),' +
        'radial-gradient(50% 44% at 74% 76%, rgba(238,0,233,.34) 0%, rgba(238,0,233,0) 72%),' +
        '#08070E;filter:blur(58px);';
      host.appendChild(d);
      onReady?.();
      return undefined;
    }

    const lite = window.innerWidth < 760 || (window.matchMedia('(pointer: coarse)').matches && window.innerWidth < 1100);
    const N = lite ? 64 : 140;
    const maxLines = lite ? 190 : 420;
    const nodeR = lite ? 0.09 : 0.072;

    const renderer = new THREE.WebGLRenderer({ antialias: !lite, alpha: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, lite ? 1.4 : 1.75));
    renderer.setClearColor(0x000000, 0);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    const cv = renderer.domElement;
    cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;transition:opacity 560ms cubic-bezier(.16,1,.3,1);';
    cv.style.opacity = String(intensity);
    host.innerHTML = '';
    host.appendChild(cv);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 100);
    camera.position.set(0, 0, 9.2);

    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = new THREE.CanvasTexture(envCanvas());
    envTex.mapping = THREE.EquirectangularReflectionMapping;
    envTex.colorSpace = THREE.SRGBColorSpace;
    const envRT = pmrem.fromEquirectangular(envTex);
    scene.environment = envRT.texture;
    envTex.dispose();
    pmrem.dispose();

    const bdTex = new THREE.CanvasTexture(backdropCanvas());
    bdTex.colorSpace = THREE.SRGBColorSpace;
    const backdrop = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: bdTex, depthWrite: false }));
    backdrop.position.z = -7;
    backdrop.renderOrder = -1;
    scene.add(backdrop);

    scene.add(new THREE.AmbientLight(0xffffff, 0.35));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(3, 4, 6);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x7a12f5, 1.1);
    rim.position.set(-5, -2, -4);
    scene.add(rim);

    const glassBase = lite
      ? { roughness: 0.16, metalness: 0, transmission: 0, thickness: 0, ior: 1.5, clearcoat: 1, clearcoatRoughness: 0.1, envMapIntensity: 1.8, transparent: true, opacity: 0.5 }
      : { roughness: 0.03, metalness: 0, transmission: 1, thickness: 1.1, ior: 1.58, clearcoat: 1, clearcoatRoughness: 0.04, envMapIntensity: 1.6, transparent: true, opacity: 1 };
    const matClear = new THREE.MeshPhysicalMaterial({ ...glassBase, color: 0xffffff, attenuationDistance: 2.4, attenuationColor: new THREE.Color(0xbfc4ff) });
    const matBlue = new THREE.MeshPhysicalMaterial({ ...glassBase, color: lite ? 0x6f7dff : 0xffffff, attenuationDistance: 0.7, attenuationColor: new THREE.Color(0x0018fd) });
    const matMag = new THREE.MeshPhysicalMaterial({ ...glassBase, color: lite ? 0xff6cfb : 0xffffff, attenuationDistance: 0.7, attenuationColor: new THREE.Color(0xee00e9) });
    const mats = [matClear, matBlue, matMag];

    const group = new THREE.Group();
    scene.add(group);

    const geo = new THREE.IcosahedronGeometry(nodeR, lite ? 1 : 2);
    const nodes = [];
    for (let i = 0; i < N; i += 1) {
      const m = new THREE.Mesh(geo, matClear);
      group.add(m);
      nodes.push(m);
    }

    const lineGeo = new THREE.BufferGeometry();
    const lineMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.13 });
    group.add(new THREE.LineSegments(lineGeo, lineMat));

    const cur = [];
    const tgt = [];
    let pairs = [];
    const applyConfig = (name) => {
      const pts = buildConfig(name, N);
      for (let i = 0; i < N; i += 1) {
        tgt[i] = pts[i];
        if (!cur[i]) cur[i] = [pts[i][0] * 1.9, pts[i][1] * 1.9, pts[i][2] * 1.9];
      }
      pairs = buildPairs(pts, maxLines);
      lineGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pairs.length * 6), 3));
    };
    applyConfigRef.current = applyConfig;
    applyConfig(configRef.current);

    const mouse = { x: 0, y: 0, tx: 0, ty: 0, active: false };
    const onPointer = (e) => {
      mouse.tx = (e.clientX / window.innerWidth) * 2 - 1;
      mouse.ty = -((e.clientY / window.innerHeight) * 2 - 1);
      mouse.active = true;
    };
    let scrollY = 0;
    const onScroll = () => {
      scrollY = window.scrollY || 0;
    };
    const onResize = () => {
      const w = host.clientWidth || window.innerWidth;
      const h = host.clientHeight || window.innerHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      const dist = camera.position.z - backdrop.position.z;
      const bh = 2 * Math.tan((camera.fov * Math.PI) / 360) * dist * 1.35;
      backdrop.scale.set(bh * camera.aspect, bh, 1);
    };
    window.addEventListener('pointermove', onPointer, { passive: true });
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize);
    onResize();

    const plane = new THREE.Vector3();
    const tmp = new THREE.Vector3();
    const dir = new THREE.Vector3();
    let degraded = lite;
    let slow = 0;
    let last = performance.now();
    let raf;
    let readyFired = false;

    const frame = () => {
      const now = performance.now();
      const dt = now - last;
      last = now;
      if (!degraded) {
        if (dt > 34) slow += 1;
        else slow = Math.max(0, slow - 1);
        if (slow > 90) {
          degraded = true;
          mats.forEach((m) => {
            m.transmission = 0;
            m.opacity = 0.46;
            m.roughness = 0.16;
            m.needsUpdate = true;
          });
        }
      }

      mouse.x += (mouse.tx - mouse.x) * 0.07;
      mouse.y += (mouse.ty - mouse.y) * 0.07;

      const fov = (camera.fov * Math.PI) / 180;
      const hh = Math.tan(fov / 2) * camera.position.z;
      plane.set(mouse.x * hh * camera.aspect, mouse.y * hh, 0);

      group.rotation.y += 0.0015;
      group.rotation.x = Math.sin(now * 0.00013) * 0.15;
      camera.position.z = 9.2 + Math.min(scrollY, 1800) * 0.0013;
      camera.position.x += (mouse.x * 0.5 - camera.position.x) * 0.03;
      camera.position.y += (mouse.y * 0.32 - camera.position.y) * 0.03;
      camera.lookAt(0, 0, 0);

      const local = plane.clone().applyMatrix4(group.matrixWorld.clone().invert());
      const pos = lineGeo.getAttribute('position');

      for (let i = 0; i < N; i += 1) {
        const c = cur[i];
        const t = tgt[i];
        c[0] += (t[0] - c[0]) * 0.05;
        c[1] += (t[1] - c[1]) * 0.05;
        c[2] += (t[2] - c[2]) * 0.05;

        tmp.set(c[0], c[1], c[2]);
        const d = tmp.distanceTo(local);
        const push = mouse.active ? Math.max(0, 1 - d / 2.4) : 0;
        dir.copy(tmp).sub(local);
        if (dir.lengthSq() > 0.0001) dir.normalize();
        const n = nodes[i];
        n.position.set(c[0] + dir.x * push * 0.8, c[1] + dir.y * push * 0.8, c[2] + dir.z * push * 0.4);
        n.scale.setScalar(1 + push * push * 2.4);
        n.rotation.y += 0.005 + push * 0.012;
        n.material = push > 0.58 ? matMag : push > 0.26 ? matBlue : matClear;
      }
      for (let p = 0; p < pairs.length; p += 1) {
        const a = nodes[pairs[p][0]].position;
        const b = nodes[pairs[p][1]].position;
        pos.array[p * 6] = a.x;
        pos.array[p * 6 + 1] = a.y;
        pos.array[p * 6 + 2] = a.z;
        pos.array[p * 6 + 3] = b.x;
        pos.array[p * 6 + 4] = b.y;
        pos.array[p * 6 + 5] = b.z;
      }
      pos.needsUpdate = true;
      lineGeo.computeBoundingSphere();

      renderer.render(scene, camera);
      if (!readyFired) {
        readyFired = true;
        onReady?.();
      }
      raf = requestAnimationFrame(frame);
    };
    frame();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('pointermove', onPointer);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      renderer.dispose();
      renderer.forceContextLoss?.();
      geo.dispose();
      lineGeo.dispose();
      mats.forEach((m) => m.dispose());
      lineMat.dispose();
      bdTex.dispose();
      envRT.texture.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const cv = hostRef.current?.querySelector('canvas');
    if (cv) cv.style.opacity = String(intensity);
  }, [intensity]);

  return <div ref={hostRef} aria-hidden="true" style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }} />;
}

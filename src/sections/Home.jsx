import { useMemo, useState } from 'react';
import HeroCipherField from '../components/HeroCipherField.jsx';
import Button from '../components/ds/Button.jsx';
import StatBlock from '../components/ds/StatBlock.jsx';
import Tabs from '../components/ds/Tabs.jsx';
import Input from '../components/ds/Input.jsx';
import { PROJECTS, STATS, CAPABILITIES } from '../data/content.js';

const CIPHER_TABS = [
  { value: 'xor', label: 'XOR' },
  { value: 'b64', label: 'Base64' },
  { value: 'vig', label: 'Vigenère' },
];

const CAVEATS = {
  xor: 'A repeating-key XOR is a teaching cipher, not a secure one. Reuse the key across two messages and both fall out.',
  b64: 'Base64 is an encoding, not encryption. It carries no key and provides no confidentiality whatsoever.',
  vig: 'Vigenère resisted analysis for three centuries and then fell to frequency counting. Included for the history.',
};

const META = { xor: 'XOR · hex', b64: 'Base64 · RFC 4648', vig: 'Vigenère · A–Z' };

function xorHex(text, key) {
  const k = key || '0';
  const bytes = new TextEncoder().encode(text);
  const kb = new TextEncoder().encode(k);
  let out = '';
  for (let i = 0; i < bytes.length; i += 1) {
    const v = bytes[i] ^ kb[i % kb.length];
    out += v.toString(16).padStart(2, '0') + (i % 2 ? ' ' : '');
  }
  return out.trim();
}

function b64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  bytes.forEach((b) => {
    bin += String.fromCharCode(b);
  });
  try {
    return btoa(bin);
  } catch {
    return '';
  }
}

function vigenere(text, key) {
  const k = (key || 'a').toLowerCase().replace(/[^a-z]/g, '') || 'a';
  let ki = 0;
  let out = '';
  for (const ch of text) {
    const lower = ch >= 'a' && ch <= 'z';
    const upper = ch >= 'A' && ch <= 'Z';
    if (!lower && !upper) {
      out += ch;
      continue;
    }
    const base = lower ? 97 : 65;
    const shift = k.charCodeAt(ki % k.length) - 97;
    out += String.fromCharCode(((ch.charCodeAt(0) - base + shift) % 26) + base);
    ki += 1;
  }
  return out;
}

export default function Home({ onOpenCase, onNavigate }) {
  const [mode, setMode] = useState('xor');
  const [text, setText] = useState('attack at dawn');
  const [key, setKey] = useState('lattice');

  const out = useMemo(() => {
    if (mode === 'xor') return xorHex(text, key);
    if (mode === 'b64') return b64(text);
    return vigenere(text, key);
  }, [mode, text, key]);

  const bytes = new TextEncoder().encode(text).length;
  const featured = PROJECTS.slice(0, 3);

  return (
    <main id="top" className="page-fade">
      <section className="hero">
        <HeroCipherField density={15} className="hero-canvas" style={{ opacity: 0.42, mixBlendMode: 'screen' }} />
        <div className="hero-scrim" aria-hidden="true" />

        <div className="hero-topline">
          <span className="mono-label">Portfolio — 2026</span>
          <span className="mono-label">Move the cursor to decrypt</span>
        </div>

        <div className="hero-bottom">
          <div className="hero-rule" />
          <h1 className="hero-title">
            Tanisha
            <br />
            Brahma
          </h1>
          <div className="hero-meta-row">
            <p className="hero-lead">
              I am a SOC and a Cryptography Developer. I build the detections that catch intrusions and the primitives that make
              them expensive.
            </p>
            <div className="hero-facts">
              <div className="hero-fact">
                <span className="mono-label">Based</span>
                <span className="hero-fact-value">Bengaluru · UTC+5:30</span>
              </div>
              <div className="hero-fact">
                <span className="mono-label">Status</span>
                <span className="hero-fact-value">
                  <span className="status-dot" />
                  Open to work
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="scroll-cue">
          <span className="mono-label">Scroll</span>
          <span className="scroll-cue-line" />
        </div>
      </section>

      <section className="stat-band">
        <div className="container">
          <div className="stat-grid">
            {STATS.map((s) => (
              <div key={s.label} data-reveal="1" className="stat-cell">
                <StatBlock label={s.label} value={s.value} note={s.note} size="lg" />
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div data-reveal="1" className="section-head">
            <h2 className="section-title">Selected work</h2>
            <a href="#work" className="mono-link" onClick={(e) => { e.preventDefault(); onNavigate('work'); }}>
              All seven ↗
            </a>
          </div>
          <div className="work-featured-list">
            {featured.map((p) => (
              <div key={p.id} data-reveal="1" className="work-featured-row" onClick={() => onOpenCase(p.id)}>
                <span className="mono-index">{p.index}</span>
                <div className="work-featured-copy">
                  <span className="work-featured-title">{p.title}</span>
                  <span className="work-featured-tagline">{p.tagline}</span>
                </div>
                <span className="mono-label">{p.discipline}</span>
                <span className="mono-label align-end">{p.year}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container capabilities-layout">
          <div className="capabilities-intro">
            <span className="mono-label">Capabilities</span>
            <h2 className="h2-title">Two halves of the same job</h2>
            <p className="body-text">
              Defence teaches you where cryptography fails in production. Cryptography teaches you what an alert is actually
              worth.
            </p>
          </div>
          <div data-reveal="1" className="capabilities-grid">
            {CAPABILITIES.map((c) => (
              <div key={c.index} className="capability-cell">
                <span className="mono-index">{c.index}</span>
                <span className="capability-title">{c.title}</span>
                <span className="capability-body">{c.body}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div data-reveal="1" className="cipher-bench">
            <div className="cipher-bench-head">
              <div className="cipher-bench-badge">
                <span className="badge-dot" />
                <span className="mono-label">Live cipher bench</span>
              </div>
              <Tabs items={CIPHER_TABS} value={mode} onChange={setMode} variant="pill" />
            </div>
            <div className="cipher-bench-grid">
              <div className="cipher-input-col">
                <Input label="Plaintext" multiline rows={3} value={text} onChange={(e) => setText(e.target.value)} />
                <Input label="Key" value={key} onChange={(e) => setKey(e.target.value)} />
                <p className="cipher-caveat">{CAVEATS[mode]}</p>
              </div>
              <div className="cipher-output-col">
                <div className="cipher-output-head">
                  <span className="mono-label">Ciphertext</span>
                  <span className="mono-micro">{META[mode]}</span>
                </div>
                <div className="cipher-output">
                  {out || ' '}
                  <span className="cipher-caret">▍</span>
                </div>
                <div className="cipher-readout-grid">
                  <div className="cipher-readout-cell">
                    <span className="mono-micro">Input</span>
                    <span className="cipher-readout-value">{bytes} B</span>
                  </div>
                  <div className="cipher-readout-cell">
                    <span className="mono-micro">Key len</span>
                    <span className="cipher-readout-value">{(key || '').length} B</span>
                  </div>
                  <div className="cipher-readout-cell">
                    <span className="mono-micro">Output</span>
                    <span className="cipher-readout-value">{out.replace(/\s/g, '').length} ch</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div data-reveal="1" className="cta-band">
            <div className="cta-copy">
              <span className="mono-label light">Engagements</span>
              <h2 className="cta-title">Detection reviews, protocol work, and incident retainers.</h2>
            </div>
            <Button
              variant="secondary"
              size="lg"
              as="a"
              href="#contact"
              iconRight="arrow-up-right"
              onClick={(e) => {
                e.preventDefault();
                onNavigate('contact');
              }}
            >
              Start a conversation
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}

import { useEffect, useRef, useState } from 'react';
import SectionHeading from '../components/SectionHeading.jsx';
import CopyButton from '../components/CopyButton.jsx';
import { FORM_STEPS, SOCIALS, EMAIL, LOCATION, PGP_FINGERPRINT } from '../data/content.js';
import { ArrowLeft, ArrowRight, AlertIcon, MailIcon, PinIcon } from '../components/Icons.jsx';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const EMPTY = ['', '', '', ''];

function buildMailto([email, name, reason, msg]) {
  const subject = encodeURIComponent(`Portfolio contact — ${reason}`);
  const body = encodeURIComponent(`From: ${name} <${email}>\r\nReason: ${reason}\r\n\r\n${msg}`);
  return `mailto:${EMAIL}?subject=${subject}&body=${body}`;
}

export default function Contact() {
  const [step, setStep] = useState(0);
  const [vals, setVals] = useState(EMPTY);
  const [err, setErr] = useState(null);
  const [done, setDone] = useState(null); // null | 'sent' | 'opened' | 'unsure'
  const [busy, setBusy] = useState(false);
  const [website, setWebsite] = useState(''); // honeypot
  const blurTimer = useRef(null);

  const cfg = FORM_STEPS[step];
  const last = step === FORM_STEPS.length - 1;

  useEffect(() => () => clearTimeout(blurTimer.current), []);

  const validate = () => {
    const v = (vals[step] || '').trim();
    if (!v) {
      setErr('Please fill out this field');
      return false;
    }
    if (step === 0 && !EMAIL_RE.test(v)) {
      setErr('Please enter a valid email address');
      return false;
    }
    setErr(null);
    return true;
  };

  const submitMailto = () => {
    let opened = false;
    const onBlur = () => {
      opened = true;
    };
    window.addEventListener('blur', onBlur, { once: true });
    window.location.href = buildMailto(vals);
    blurTimer.current = setTimeout(() => {
      window.removeEventListener('blur', onBlur);
      setDone(opened ? 'opened' : 'unsure');
    }, 1500);
  };

  const submit = async () => {
    const [email, name, reason, msg] = vals;
    setBusy(true);
    try {
      const r = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, name, reason, message: msg, website }),
      });
      if (r.ok) {
        setDone('sent');
        return;
      }
      if (r.status === 429) {
        setErr('Too many messages from this address for now. Try again in a few minutes.');
        return;
      }
    } catch {
      /* no function available: fall through to mailto */
    } finally {
      setBusy(false);
    }
    submitMailto();
  };

  const next = () => {
    if (!validate()) return;
    if (!last) {
      setStep((s) => s + 1);
      return;
    }
    submit();
  };

  const onChange = (e) => {
    const copy = vals.slice();
    copy[step] = e.target.value;
    setVals(copy);
    setErr(null);
  };

  const onKey = (e) => {
    if (e.key !== 'Enter') return;
    if (cfg.type !== 'textarea' || e.metaKey || e.ctrlKey) {
      e.preventDefault();
      next();
    }
  };

  const reset = () => {
    setVals(EMPTY);
    setStep(0);
    setDone(null);
    setErr(null);
  };

  const draft = vals.map((v) => v.trim()).some(Boolean) ? buildMailto(vals) : `mailto:${EMAIL}`;

  return (
    <section id="contact" className="section contact">
      <div className="container contact-inner">
        <SectionHeading number="07" title="Contact" lead="Email is fastest. Encrypted mail welcome; the PGP fingerprint is below." />
        <div className="contact-panel" data-reveal="1">
          <div className="contact-glow tl" />
          <div className="contact-glow br" />
          <div className="contact-content">
            <div className="contact-row">
              <div className="contact-lead">
                <h2 className="contact-title">let's talk</h2>
                <div className="contact-direct">
                  <span className="eyebrow">Fastest route</span>
                  <a href={`mailto:${EMAIL}`} className="contact-email">
                    <MailIcon stroke="#E5203F" />
                    <span>{EMAIL}</span>
                  </a>
                  <div className="contact-direct-actions">
                    <CopyButton text={EMAIL} label="Copy address" />
                    <span className="contact-direct-note">Replies within two working days.</span>
                  </div>
                </div>
              </div>
              <div className="contact-form">
                <span className="eyebrow soft">Or draft it here</span>
                {!done ? (
                  <div className="form-step">
                    <div className="form-step-head">
                      <span className="form-step-label">{cfg.label}</span>
                      {step > 0 ? (
                        <button type="button" className="form-back" onClick={() => { setStep((s) => s - 1); setErr(null); }}>
                          <ArrowLeft size={14} stroke="#E5203F" /><span>back</span>
                        </button>
                      ) : null}
                    </div>
                    <div className="form-field">
                      {cfg.type === 'textarea' ? (
                        <textarea rows={2} value={vals[step]} onChange={onChange} onKeyDown={onKey} placeholder={cfg.placeholder} aria-label={cfg.placeholder} />
                      ) : (
                        <input type={cfg.type} value={vals[step]} onChange={onChange} onKeyDown={onKey} placeholder={cfg.placeholder} aria-label={cfg.placeholder} />
                      )}
                      <input type="text" name="website" value={website} onChange={(e) => setWebsite(e.target.value)} tabIndex={-1} autoComplete="off" aria-hidden="true" className="hp-field" />
                      <button type="button" className="form-next" onClick={next} disabled={busy} title={last ? 'Send' : 'Next step'} aria-label={last ? 'Send' : 'Next step'}>
                        <ArrowRight />
                      </button>
                    </div>
                    {err ? (
                      <div className="form-error" role="alert"><AlertIcon size={14} /><span>{err}</span></div>
                    ) : null}
                    {last && !err ? <span className="form-hint">Enter or the arrow sends it</span> : null}
                  </div>
                ) : (
                  <div className="form-done" role="status">
                    {done === 'sent' ? (
                      <p>Sent. I will get back to you within two working days.</p>
                    ) : done === 'opened' ? (
                      <p>Draft opened in your mail app. Hit send there and I will get back to you.</p>
                    ) : (
                      <p>No mail app answered. Copy the address above, or <a href={draft}>try the draft link</a> again.</p>
                    )}
                    <button type="button" onClick={reset}>Start another message</button>
                  </div>
                )}
              </div>
            </div>
            <div className="signature-rule">
              <span className="signature">Tanisha Brahma</span>
            </div>
            <div className="contact-foot">
              <div className="contact-socials">
                {SOCIALS.filter((s) => s.href.startsWith('http')).map((s) => (
                  <a key={s.label} href={s.href} target="_blank" rel="noopener noreferrer">{s.label.toLowerCase()}</a>
                ))}
              </div>
              <div className="contact-meta">
                <span className="contact-pgp" title="PGP fingerprint · Ed25519"><span className="eyebrow sm">PGP</span> <code>{PGP_FINGERPRINT}</code></span>
                <a href={LOCATION.href} target="_blank" rel="noopener noreferrer"><PinIcon stroke="#E5203F" /><span>{LOCATION.label}</span></a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

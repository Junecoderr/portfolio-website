import { useState } from 'react';
import SectionHeading from '../components/SectionHeading.jsx';
import { FORM_STEPS, SOCIALS, EMAIL, LOCATION } from '../data/content.js';
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
  const [done, setDone] = useState(false);

  const cfg = FORM_STEPS[step];
  const last = step === FORM_STEPS.length - 1;

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

  const next = () => {
    if (!validate()) return;
    if (!last) {
      setStep((s) => s + 1);
      return;
    }
    window.location.href = buildMailto(vals);
    setDone(true);
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
    setDone(false);
    setErr(null);
  };

  return (
    <section id="contact" className="section contact">
      <div className="container contact-inner">
        <SectionHeading number="06" title="Contact" />
        <div className="contact-panel">
          <div className="contact-glow tl" />
          <div className="contact-glow br" />
          <div className="contact-content">
            <div className="contact-row">
              <h2 className="contact-title">let's talk</h2>
              <div className="contact-form">
                {!done ? (
                  <div className="form-step">
                    <div className="form-step-head">
                      <span className="form-step-label">{cfg.label}</span>
                      {step > 0 ? (
                        <button type="button" className="form-back" onClick={() => { setStep((s) => s - 1); setErr(null); }}>
                          <ArrowLeft size={14} stroke="#5ED2F2" /><span>back</span>
                        </button>
                      ) : null}
                    </div>
                    <div className="form-field">
                      {cfg.type === 'textarea' ? (
                        <textarea rows={2} value={vals[step]} onChange={onChange} onKeyDown={onKey} placeholder={cfg.placeholder} />
                      ) : (
                        <input type={cfg.type} value={vals[step]} onChange={onChange} onKeyDown={onKey} placeholder={cfg.placeholder} />
                      )}
                      <button type="button" className="form-next" onClick={next} title="Next step" aria-label="Next step">
                        <ArrowRight />
                      </button>
                    </div>
                    {err ? (
                      <div className="form-error"><AlertIcon size={14} /><span>{err}</span></div>
                    ) : null}
                    {last && !err ? <span className="form-hint">Press Ctrl+Enter or click arrow to send</span> : null}
                  </div>
                ) : (
                  <div className="form-done">
                    <p>Message drafted in your mail app — send it and I will get back to you as soon as possible.</p>
                    <button type="button" onClick={reset}>Send another message</button>
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
                <a href={`mailto:${EMAIL}`}><MailIcon stroke="#5ED2F2" /><span>{EMAIL}</span></a>
                <a href={LOCATION.href} target="_blank" rel="noopener noreferrer"><PinIcon stroke="#5ED2F2" /><span>{LOCATION.label}</span></a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

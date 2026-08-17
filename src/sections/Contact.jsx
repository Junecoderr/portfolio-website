import { CONTACT_ROWS, PGP_FINGERPRINT } from '../data/content.js';

export default function Contact() {
  return (
    <main id="contact" className="page-fade route-main">
      <section className="section-pad">
        <div className="container">
          <span className="mono-label">Contact</span>
          <h1 className="route-title contact-title">
            Say something
            <br />
            encrypted.
          </h1>
          <div className="hero-rule" style={{ marginTop: 56 }} />
          <div className="contact-grid">
            <div className="contact-direct">
              <div className="contact-block">
                <span className="mono-label">Direct</span>
                <a href="mailto:tanisha@brahma.sec" className="contact-email">
                  tanisha@brahma.sec
                </a>
              </div>
              <p className="body-text wide">
                Encrypted mail preferred. My key fingerprint is below; verify it out of band before you send anything
                sensitive.
              </p>
              <div className="pgp-card">
                <span className="mono-label">PGP fingerprint · Ed25519</span>
                <span className="pgp-value">{PGP_FINGERPRINT}</span>
              </div>
            </div>
            <div className="fact-list">
              {CONTACT_ROWS.map((c) => (
                <a key={c.k} href={c.href} className="contact-row">
                  <span className="mono-label">{c.k}</span>
                  <span className="contact-row-value">
                    {c.v}
                    <span className="contact-row-arrow">↗</span>
                  </span>
                </a>
              ))}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

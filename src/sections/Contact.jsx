import SectionHeading from '../components/SectionHeading.jsx';
import CopyButton from '../components/CopyButton.jsx';
import { SOCIALS, EMAIL, LOCATION, PGP_FINGERPRINT } from '../data/content.js';

export default function Contact() {
  return (
    <section id="contact" className="section">
      <div className="container">
        <SectionHeading title="Contact" lead="Email is fastest. Encrypted mail welcome." />
        <a href={`mailto:${EMAIL}`} className="contact-email" data-reveal="1">{EMAIL}</a>
        <div className="contact-actions" data-reveal="1">
          <CopyButton text={EMAIL} label="Copy address" />
          <span className="contact-note">Replies within two working days.</span>
        </div>
        <div className="contact-meta" data-reveal="1">
          <span className="contact-pgp"><span>PGP · Ed25519</span><code>{PGP_FINGERPRINT}</code><CopyButton text={PGP_FINGERPRINT.replace(/\s+/g, '')} label="Copy" className="copy-btn-sm" /></span>
          <a href={LOCATION.href} target="_blank" rel="noopener noreferrer">{LOCATION.label}</a>
          <div className="contact-socials">
            {SOCIALS.filter((s) => s.href.startsWith('http')).map((s) => (
              <a key={s.label} href={s.href} target="_blank" rel="noopener noreferrer">{s.label}</a>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

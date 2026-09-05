import PageShell from './PageShell.jsx';
import { EMAIL, PGP_FINGERPRINT } from '../data/content.js';

const ROWS = [
  ['Scripts', 'Only first-party JavaScript runs here. The Content-Security-Policy allows scripts from this origin and Vercel Analytics, nothing else, and forbids inline script.'],
  ['Third parties', 'Fonts, icons and images are served from this site. No CDN, no tag manager, no embedded widgets, so no third party sees your visit.'],
  ['Framing and MIME', 'X-Frame-Options: DENY and frame-ancestors none block clickjacking. X-Content-Type-Options: nosniff stops MIME confusion.'],
  ['Referrers', 'strict-origin-when-cross-origin: outbound links learn the site name, not the page you were on.'],
  ['Contact path', 'The form posts to a same-origin function that forwards mail through a transactional provider with a honeypot and a per-address rate limit. Without that function, it falls back to your own mail client and nothing is stored.'],
  ['Analytics', 'Vercel Web Analytics, cookieless, aggregate only. No fingerprinting, no cross-site identifiers.'],
  ['Build and supply chain', 'Static pages are prerendered at build. Dependencies are pinned, updated by Dependabot, and every push runs lint, build and smoke tests in CI.'],
  ['Disclosure', `Found something? Mail ${EMAIL}. Encrypted mail welcome; the fingerprint is below. A machine-readable version lives at /.well-known/security.txt.`],
];

export default function Security() {
  return (
    <PageShell title="How this site is secured." lead="A portfolio for security work should hold up to the same scrutiny. This page is the site's own threat model, kept short.">
      <dl className="subpage-rows">
        {ROWS.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="subpage-pgp"><span className="eyebrow">PGP · Ed25519</span> <code>{PGP_FINGERPRINT}</code></p>
    </PageShell>
  );
}

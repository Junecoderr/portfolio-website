import { StrictMode } from 'react';
import { hydrateRoot, createRoot } from 'react-dom/client';
import { Analytics } from '@vercel/analytics/react';
import App from './App.jsx';
import Resume from './pages/Resume.jsx';
import Security from './pages/Security.jsx';
import NotFound from './pages/NotFound.jsx';
import { caseIdFromPath } from './seo.js';
import { pageFor } from './routes.js';
import { PGP_FINGERPRINT, EMAIL } from './data/content.js';
import './styles/global.css';
import './styles/site.css';

const root = document.getElementById('root');
const page = root.dataset.page || pageFor(window.location.pathname);
const hash = /^#work\/([a-z0-9-]+)$/.exec(window.location.hash);
const initialCase = caseIdFromPath(window.location.pathname) || (hash ? caseIdFromPath(`/work/${hash[1]}`) : null);
const PAGES = { resume: Resume, security: Security, notfound: NotFound };
const Page = PAGES[page];

const tree = (
  <StrictMode>
    {Page ? <Page /> : <App initialCase={initialCase} />}
    {window.location.hostname !== 'localhost' ? <Analytics /> : null}
  </StrictMode>
);

if (root.hasChildNodes()) hydrateRoot(root, tree);
else createRoot(root).render(tree);

// A note for the curious.
try {
  console.log(
    '%cTanisha Brahma%c\nSOC · detection engineering · applied cryptography\nPGP ' + PGP_FINGERPRINT + '\nHiring or collaborating? ' + EMAIL + '\nHow this site is secured: /security',
    'font: 600 16px Georgia, serif; color: #E5203F', 'font: 12px ui-monospace, monospace; color: #98A6B3'
  );
} catch {
  /* console unavailable */
}

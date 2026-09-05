import { StrictMode } from 'react';
import { hydrateRoot, createRoot } from 'react-dom/client';
import App from './App.jsx';
import { caseIdFromPath } from './seo.js';
import './styles/global.css';
import './styles/site.css';

const root = document.getElementById('root');
const hash = /^#work\/([a-z0-9-]+)$/.exec(window.location.hash);
const initialCase = caseIdFromPath(window.location.pathname) || (hash ? caseIdFromPath(`/work/${hash[1]}`) : null);
const tree = (
  <StrictMode>
    <App initialCase={initialCase} />
  </StrictMode>
);

if (root.hasChildNodes()) hydrateRoot(root, tree);
else createRoot(root).render(tree);

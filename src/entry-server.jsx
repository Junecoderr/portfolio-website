import { renderToString } from 'react-dom/server';
import App from './App.jsx';
import { headFor, caseIdFromPath, routes } from './seo.js';
import { SITE_URL } from './data/content.js';

export { routes };
export const siteUrl = SITE_URL;

export function render(path) {
  const html = renderToString(<App initialCase={caseIdFromPath(path)} />);
  return { html, head: headFor(path) };
}

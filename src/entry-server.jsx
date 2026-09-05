import { renderToString } from 'react-dom/server';
import App from './App.jsx';
import Resume from './pages/Resume.jsx';
import Security from './pages/Security.jsx';
import NotFound from './pages/NotFound.jsx';
import { headFor, caseIdFromPath, routes } from './seo.js';
import { pageFor } from './routes.js';
import * as content from './data/content.js';
import { SITE_URL } from './data/content.js';

export { routes, content };
export const siteUrl = SITE_URL;

const PAGES = { resume: Resume, security: Security, notfound: NotFound };

export function render(path) {
  const page = pageFor(path);
  const Page = PAGES[page];
  const html = renderToString(Page ? <Page /> : <App initialCase={caseIdFromPath(path)} />);
  return { html, head: headFor(path), page };
}

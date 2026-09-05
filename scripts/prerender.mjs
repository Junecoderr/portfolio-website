// Post-build: render every route to static HTML with its own head, write the sitemap.
// Run after `vite build` (client) and `vite build --ssr` (server bundle in dist/server).
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dist');
const template = readFileSync(resolve(dist, 'index.html'), 'utf8');
const { render, routes, siteUrl, content } = await import(pathToFileURL(resolve(dist, 'server/entry-server.js')).href);

// ---- content validation: fail the build on missing or malformed data ----
const problems = [];
const need = (cond, msg) => { if (!cond) problems.push(msg); };
need(/^https:\/\/[^/]+$/.test(content.SITE_URL), 'SITE_URL must be an https origin without a trailing slash');
need(/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(content.EMAIL), 'EMAIL is not a valid address');
need(Array.isArray(content.STATS) && content.STATS.length === 4, 'STATS must have exactly 4 entries');
for (const p of content.PROJECTS) {
  for (const k of ['id', 'title', 'tagline', 'discipline', 'year', 'summary', 'timelineTitle']) need(typeof p[k] === 'string' && p[k].trim(), `PROJECTS.${p.id || '?'}.${k} missing`);
  need(Array.isArray(p.facts) && p.facts.length >= 3, `PROJECTS.${p.id}.facts needs 3+ entries`);
  need(Array.isArray(p.metrics) && p.metrics.length === 4, `PROJECTS.${p.id}.metrics needs 4 entries`);
  need(Array.isArray(p.timeline) && p.timeline.length >= 3 && p.timeline.every((t) => t.t && t.title && t.detail), `PROJECTS.${p.id}.timeline malformed`);
}
for (const id of content.CASE_IDS) need(content.PROJECTS.some((p) => p.id === id), `CASE_IDS has unknown id ${id}`);
for (const c of content.WORK_CARDS) need(content.CASE_IDS.includes(c.id) && /^\d{3}$/.test(c.number) && c.tags.length >= 2, `WORK_CARDS.${c.id} malformed`);
for (const x of content.EXPERIENCE) need(x.org && x.role && x.period && x.points.length >= 2 && x.tags.length >= 1, `EXPERIENCE.${x.org || '?'} malformed`);
for (const g of content.RECOGNITION) need(g.title && g.items.every((i) => i.name && i.detail && i.meta), `RECOGNITION.${g.title || '?'} malformed`);
if (problems.length) {
  console.error('Content validation failed:\n - ' + problems.join('\n - '));
  process.exit(1);
}
console.log('content: ok');

const today = new Date().toISOString().slice(0, 10);
const urls = [];

for (const path of routes()) {
  const { html, head, page: kind } = render(path);
  const page = template.replace('<!--app-head-->', head).replace('<!--app-html-->', html).replace('<div id="root">', `<div id="root" data-page="${kind}">`);
  const file = path === '/' ? 'index.html' : `${path.replace(/^\//, '')}.html`;
  mkdirSync(dirname(resolve(dist, file)), { recursive: true });
  writeFileSync(resolve(dist, file), page);
  if (path !== '/404') urls.push({ loc: `${siteUrl}${path === '/' ? '/' : path}`, priority: path === '/' ? '1.0' : path.startsWith('/work/') ? '0.8' : '0.5' });
  console.log(`prerendered ${file}`);
}

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
  .map((u) => `  <url><loc>${u.loc}</loc><lastmod>${today}</lastmod><changefreq>monthly</changefreq><priority>${u.priority}</priority></url>`)
  .join('\n')}\n</urlset>\n`;
writeFileSync(resolve(dist, 'sitemap.xml'), sitemap);
rmSync(resolve(dist, 'server'), { recursive: true, force: true });
console.log(`sitemap: ${urls.length} urls`);

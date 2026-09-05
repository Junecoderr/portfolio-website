// Post-build: render every route to static HTML with its own head, write the sitemap.
// Run after `vite build` (client) and `vite build --ssr` (server bundle in dist/server).
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dist');
const template = readFileSync(resolve(dist, 'index.html'), 'utf8');
const { render, routes, siteUrl } = await import(pathToFileURL(resolve(dist, 'server/entry-server.js')).href);

const today = new Date().toISOString().slice(0, 10);
const urls = [];

for (const path of routes()) {
  const { html, head } = render(path);
  const page = template.replace('<!--app-head-->', head).replace('<!--app-html-->', html);
  const file = path === '/' ? 'index.html' : `${path.replace(/^\//, '')}.html`;
  mkdirSync(dirname(resolve(dist, file)), { recursive: true });
  writeFileSync(resolve(dist, file), page);
  urls.push({ loc: `${siteUrl}${path === '/' ? '/' : path}`, priority: path === '/' ? '1.0' : '0.8' });
  console.log(`prerendered ${file}`);
}

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
  .map((u) => `  <url><loc>${u.loc}</loc><lastmod>${today}</lastmod><changefreq>monthly</changefreq><priority>${u.priority}</priority></url>`)
  .join('\n')}\n</urlset>\n`;
writeFileSync(resolve(dist, 'sitemap.xml'), sitemap);
rmSync(resolve(dist, 'server'), { recursive: true, force: true });
console.log(`sitemap: ${urls.length} urls`);

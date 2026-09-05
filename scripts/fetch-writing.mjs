// Pre-build: pull the latest Medium posts into src/data/writing.generated.json.
// Never fails the build: on any error the file holds an empty list and the section stays hidden.
import { writeFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const out = resolve(dirname(fileURLToPath(import.meta.url)), '../src/data/writing.generated.json');
const FEED = 'https://medium.com/feed/@Junecodder';
const decode = (s) => s.replace(/<!\[CDATA\[|\]\]>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").trim();

let posts = [];
try {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  const res = await fetch(FEED, { signal: ctrl.signal, headers: { 'user-agent': 'portfolio-build/1.0' } });
  clearTimeout(t);
  if (res.ok) {
    const xml = await res.text();
    posts = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].slice(0, 3).map((m) => {
      const item = m[1];
      const pick = (tag) => (item.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`)) || [])[1] || '';
      const link = decode(pick('link')).split('?')[0];
      return { title: decode(pick('title')), url: link, date: new Date(decode(pick('pubDate'))).toISOString().slice(0, 10) };
    }).filter((p) => p.title && p.url);
  }
} catch {
  posts = [];
}
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(posts, null, 2));
console.log(`writing: ${posts.length} posts`);

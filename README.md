# Tanisha Brahma — Portfolio

Personal site for Tanisha Brahma, SOC analyst and cryptography developer.
Live at https://portfolio-website-junix1.vercel.app.

## Stack

- Vite 5 + React 18, no router. Case studies are real URLs (`/work/<id>`) prerendered at build.
- `@paper-design/shaders-react` for the grain-gradient backdrop, loaded lazily after first paint.
- Self-hosted fonts (Geist, Geist Mono, Instrument Serif as WOFF2; Nasalization and Bastliga One as OTF).
- Deployed on Vercel from `main`.

## Run

```bash
npm install
npm run dev        # local dev server
npm run build      # client build + SSR bundle + prerender + sitemap
npm run preview    # serve dist/
npm run lint       # eslint
npm test           # playwright smoke test against the built site
```

## Where things live

| Path | Purpose |
| --- | --- |
| `src/data/content.js` | All copy and data: projects, stats, experience, skills, recognition, contact, `SITE_URL` |
| `src/seo.js` | Per-page title, description, canonical, Open Graph, JSON-LD |
| `src/sections/*` | Page sections in scroll order |
| `src/components/*` | Nav, drawer, dialog, background, preloader, footer |
| `src/styles/site.css` | Class-based styles; tokens in `src/styles/tokens/` |
| `scripts/prerender.mjs` | Writes `dist/index.html`, `dist/work/<id>.html`, `dist/sitemap.xml` |
| `public/og/*` | Open Graph cards |
| `vercel.json` | Clean URLs, cache headers, Content-Security-Policy |

## Content hooks

- Drop a photo at `public/portrait.jpg` to replace the generated abstract in the About card.
- Set `RESUME_URL` and `BLOG_URL` in `src/data/content.js` to reveal those links.
- Change `SITE_URL` when a custom domain lands; canonicals, sitemap and schema follow.

## Licences

Code is MIT (see `LICENSE`). Written content and images belong to Tanisha Brahma.
Geist, Geist Mono and Instrument Serif are under the SIL Open Font License. Nasalization and
Bastliga One are third-party fonts with their own terms; check before reuse.

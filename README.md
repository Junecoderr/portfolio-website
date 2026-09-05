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
| `src/pages/*` | Secondary prerendered pages: `/resume`, `/security`, 404 |
| `scripts/fetch-writing.mjs` | Pulls the latest Medium posts at build into `src/data/writing.generated.json` |
| `api/contact.js` | Vercel function for the contact form (Resend); falls back to mailto when unconfigured |
| `public/.well-known/security.txt` | RFC 9116 security contact |
| `public/og/*` | Open Graph cards |
| `vercel.json` | Clean URLs, cache headers, Content-Security-Policy |

## Environment

| Variable | Where | Effect |
| --- | --- | --- |
| `RESEND_API_KEY` | Vercel project env | Enables server-side sending for the contact form. Without it the form opens the visitor's mail app. |
| `CONTACT_FROM` | Vercel project env | Optional sender, e.g. `Portfolio <hello@yourdomain>`; defaults to Resend's onboarding sender. |
| `LHCI_GITHUB_APP_TOKEN` | GitHub Actions secret | Optional; lets Lighthouse CI post status checks. |

## Content hooks

- Drop a photo at `public/portrait.jpg` to replace the generated abstract in the About card.
- `RESUME_URL` points at the built-in `/resume` page; swap it for a PDF URL if preferred. Set `BLOG_URL` to reveal the Blog link.
- Change `SITE_URL` when a custom domain lands; canonicals, sitemap and schema follow.

## Licences

Code is MIT (see `LICENSE`). Written content and images belong to Tanisha Brahma.
Geist, Geist Mono and Instrument Serif are under the SIL Open Font License. Nasalization and
Bastliga One are third-party fonts with their own terms; check before reuse.

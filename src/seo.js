import { PROJECTS, SEO, SITE_URL, SITE_NAME, SOCIALS, EMAIL, CASE_IDS, RECOGNITION } from './data/content.js';
import { pageFor } from './routes.js';

const esc = (v) => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const clip = (text, max = 155) => (text.length <= max ? text : `${text.slice(0, max - 1).replace(/\s+\S*$/, '')}…`);
const json = (obj) => `<script type="application/ld+json">${JSON.stringify(obj).replace(/</g, '\\u003c')}</script>`;

const sameAs = SOCIALS.filter((s) => s.href.startsWith('http')).map((s) => s.href);

export const person = {
  '@type': 'Person',
  '@id': `${SITE_URL}/#person`,
  name: SITE_NAME,
  url: `${SITE_URL}/`,
  image: `${SITE_URL}/og/home.png`,
  jobTitle: SEO.jobTitle,
  email: `mailto:${EMAIL}`,
  address: { '@type': 'PostalAddress', addressLocality: SEO.locality, addressRegion: SEO.region, addressCountry: SEO.country },
  knowsAbout: SEO.knowsAbout,
  sameAs,
  hasCredential: (RECOGNITION.find((g) => g.title === 'Certifications')?.items || []).map((c) => ({
    '@type': 'EducationalOccupationalCredential', name: c.name, alternateName: c.detail, credentialCategory: 'certification', dateCreated: c.meta,
  })),
  performerIn: (RECOGNITION.find((g) => g.title === 'Talks & papers')?.items || []).filter((t) => !/ePrint|Whitepaper/.test(t.detail)).map((t) => ({
    '@type': 'Event', name: t.name, startDate: t.meta, location: { '@type': 'Place', name: t.detail }, performer: { '@id': `${SITE_URL}/#person` },
  })),
};

const website = {
  '@type': 'WebSite',
  '@id': `${SITE_URL}/#website`,
  url: `${SITE_URL}/`,
  name: `${SITE_NAME} — Portfolio`,
  inLanguage: 'en',
  publisher: { '@id': `${SITE_URL}/#person` },
};

/** Every route the prerender emits, with its own head. */
export function routes() {
  return ['/', ...CASE_IDS.map((id) => `/work/${id}`), '/resume', '/security', '/404'];
}

const PAGES = {
  resume: { title: `Resume – ${SITE_NAME}, ${SEO.jobTitle}`, description: `One-page resume for ${SITE_NAME}: experience, skills, certifications, CVE credits and talks. Print or save as PDF.`, path: '/resume' },
  security: { title: `How this site is secured | ${SITE_NAME}`, description: 'The threat model behind this portfolio: content security policy, no third parties, contact path, analytics, build and disclosure.', path: '/security' },
  notfound: { title: `Page not found | ${SITE_NAME}`, description: 'Nothing at this address.', path: '/404', noindex: true },
};

export function caseIdFromPath(path) {
  const m = /^\/work\/([a-z0-9-]+)(?:\.html)?\/?$/.exec(path || '');
  return m && CASE_IDS.includes(m[1]) ? m[1] : null;
}

function meta(list) {
  return list.map(([k, v, attr = 'name']) => `<meta ${attr}="${k}" content="${esc(v)}" />`).join('\n  ');
}

export function headFor(path) {
  const page = pageFor(path);
  if (page !== 'home') {
    const cfg = PAGES[page];
    const url = `${SITE_URL}${cfg.path}`;
    const image = `${SITE_URL}/og/home.png`;
    return [
      `<title>${esc(cfg.title)}</title>`,
      meta([
        ['description', cfg.description],
        ['author', SITE_NAME],
        ['robots', cfg.noindex ? 'noindex, follow' : 'index, follow, max-image-preview:large'],
        ['og:type', 'website', 'property'],
        ['og:site_name', `${SITE_NAME} — Portfolio`, 'property'],
        ['og:title', cfg.title, 'property'],
        ['og:description', cfg.description, 'property'],
        ['og:url', url, 'property'],
        ['og:image', image, 'property'],
        ['twitter:card', 'summary_large_image'],
        ['twitter:title', cfg.title],
        ['twitter:description', cfg.description],
        ['twitter:image', image],
      ]),
      cfg.noindex ? '' : `<link rel="canonical" href="${url}" />`,
      json({ '@context': 'https://schema.org', '@graph': [person, website, { '@type': 'WebPage', '@id': `${url}#page`, url, name: cfg.title, isPartOf: { '@id': `${SITE_URL}/#website` }, about: { '@id': `${SITE_URL}/#person` } }] }),
    ].filter(Boolean).join('\n  ');
  }
  const id = caseIdFromPath(path);
  const project = id ? PROJECTS.find((p) => p.id === id) : null;
  const url = project ? `${SITE_URL}/work/${project.id}` : `${SITE_URL}/`;
  const title = project ? `${project.title} – ${project.discipline} case study | ${SITE_NAME}` : SEO.title;
  const description = project ? clip(`${project.tagline}. ${project.summary}`) : SEO.description;
  const image = project ? `${SITE_URL}/og/${project.id}.png` : `${SITE_URL}/og/home.png`;

  const graph = project
    ? [
        person,
        website,
        {
          '@type': 'CreativeWork',
          '@id': `${url}#work`,
          url,
          name: project.title,
          headline: `${project.title}: ${project.tagline}`,
          abstract: project.summary,
          about: project.discipline,
          dateCreated: String(project.year).slice(0, 4),
          image,
          inLanguage: 'en',
          author: { '@id': `${SITE_URL}/#person` },
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_URL}/` },
            { '@type': 'ListItem', position: 2, name: 'Work', item: `${SITE_URL}/#work` },
            { '@type': 'ListItem', position: 3, name: project.title, item: url },
          ],
        },
      ]
    : [
        person,
        website,
        { '@type': 'ProfilePage', '@id': `${SITE_URL}/#page`, url: `${SITE_URL}/`, name: SEO.title, mainEntity: { '@id': `${SITE_URL}/#person` }, isPartOf: { '@id': `${SITE_URL}/#website` } },
        {
          '@type': 'ItemList',
          name: 'Case studies',
          itemListElement: CASE_IDS.map((cid, i) => {
            const p = PROJECTS.find((x) => x.id === cid);
            return { '@type': 'ListItem', position: i + 1, name: p.title, url: `${SITE_URL}/work/${cid}` };
          }),
        },
      ];

  return [
    `<title>${esc(title)}</title>`,
    meta([
      ['description', description],
      ['author', SITE_NAME],
      ['robots', 'index, follow, max-image-preview:large, max-snippet:-1'],
      ['og:type', project ? 'article' : 'profile', 'property'],
      ['og:site_name', `${SITE_NAME} — Portfolio`, 'property'],
      ['og:locale', 'en_IN', 'property'],
      ['og:title', title, 'property'],
      ['og:description', description, 'property'],
      ['og:url', url, 'property'],
      ['og:image', image, 'property'],
      ['og:image:width', '1200', 'property'],
      ['og:image:height', '630', 'property'],
      ['og:image:alt', project ? `${project.title} case study card` : `${SITE_NAME} portfolio card`, 'property'],
      ['twitter:card', 'summary_large_image'],
      ['twitter:title', title],
      ['twitter:description', description],
      ['twitter:image', image],
    ]),
    `<link rel="canonical" href="${url}" />`,
    ...sameAs.map((h) => `<link rel="me" href="${h}" />`),
    json({ '@context': 'https://schema.org', '@graph': graph }),
  ].join('\n  ');
}

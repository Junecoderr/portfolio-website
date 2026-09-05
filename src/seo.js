import { PROJECTS, SEO, SITE_URL, SITE_NAME, SOCIALS, EMAIL, CASE_IDS } from './data/content.js';

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
  return ['/', ...CASE_IDS.map((id) => `/work/${id}`)];
}

export function caseIdFromPath(path) {
  const m = /^\/work\/([a-z0-9-]+)(?:\.html)?\/?$/.exec(path || '');
  return m && CASE_IDS.includes(m[1]) ? m[1] : null;
}

function meta(list) {
  return list.map(([k, v, attr = 'name']) => `<meta ${attr}="${k}" content="${esc(v)}" />`).join('\n  ');
}

export function headFor(path) {
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

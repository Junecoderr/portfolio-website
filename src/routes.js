/** Maps a path to the page shell to render. Shared by server prerender and client entry. */
export function pageFor(path) {
  const p = (path || '/').replace(/\.html$/, '').replace(/\/+$/, '') || '/';
  if (p === '/security') return 'security';
  if (p === '/resume') return 'resume';
  if (p === '/404') return 'notfound';
  if (p === '/' || /^\/work\/[a-z0-9-]+$/.test(p)) return 'home';
  return 'notfound';
}

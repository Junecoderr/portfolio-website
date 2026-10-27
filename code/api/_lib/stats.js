// Shared code for the site's visit and download counters (api/hit.js, api/stats.js).
//
// Visits: a cookie-free counter in Upstash Redis, spoken to over its REST API
// with plain fetch. Each page view adds 1 to `tb:visits` and to the page's
// field in the hash `tb:pages`. Nothing about the visitor (no cookie, IP
// address or user agent) is stored.
//
// Downloads: the GitHub release assets' own download_count, summed across
// every tb-v* release, fetched here on the server so browsers never talk to
// GitHub. Cached in memory for 10 minutes (the CDN caches the stats response
// for as long).
//
// Everything degrades: no Redis settings -> visits are null; GitHub down or
// rate-limited -> downloads are null. The handlers never answer 500.
// No dependencies; files under api/_lib are not deployed as functions.

export const VISITS_KEY = "tb:visits";
export const PAGES_KEY = "tb:pages";
export const RELEASES_URL = "https://api.github.com/repos/Junecoderr/portfolio-website/releases?per_page=100";
export const TAG_PREFIX = "tb-v";
export const DOWNLOADS_TTL_MS = 10 * 60 * 1000;
export const STATS_CACHE_CONTROL = "public, s-maxage=30, stale-while-revalidate=300";
export const MAX_BODY_BYTES = 1024;
export const MAX_PATH_LENGTH = 200;
const USER_AGENT = "tb-website-stats (code.tanisha.ceo)";
const TIMEOUT_MS = 4000;

// Lower-case substrings of user agents that are not people. Matched, never stored.
export const BOT_MARKERS = [
  "bot", "crawl", "spider", "slurp", "headless", "lighthouse", "pagespeed", "preview",
  "monitor", "uptime", "curl/", "wget/", "python-requests", "python-urllib", "httpclient",
  "go-http-client", "okhttp", "axios/", "node-fetch", "undici", "scrapy", "phantomjs",
  "facebookexternalhit", "embedly", "vercel-screenshot", "playwright", "puppeteer",
];

export function isBot(userAgent) {
  if (!userAgent) return true;
  const ua = String(userAgent).toLowerCase();
  return BOT_MARKERS.some((m) => ua.includes(m));
}

/** Upstash settings from either the Vercel KV names or Upstash's own; null if missing. */
export function redisConfig(env = process.env) {
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL;
  const token = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;
  return { url: String(url).replace(/\/+$/, ""), token: String(token) };
}

/** Run several Redis commands in one round trip; returns their results, or null on any failure. */
export async function redisPipeline(config, commands, fetchImpl = fetch) {
  if (!config) return null;
  try {
    const res = await fetchImpl(`${config.url}/pipeline`, {
      method: "POST",
      headers: { Authorization: `Bearer ${config.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(commands),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (!Array.isArray(data) || data.length !== commands.length) return null;
    if (data.some((item) => !item || item.error)) return null;
    return data.map((item) => item.result);
  } catch {
    return null;
  }
}

function toCount(value) {
  if (value === null || value === undefined) return 0;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
}

/** The total page views, 0 before the first one, or null when Redis is missing or failing. */
export async function readVisits(env = process.env, fetchImpl = fetch) {
  const results = await redisPipeline(redisConfig(env), [["GET", VISITS_KEY]], fetchImpl);
  return results ? toCount(results[0]) : null;
}

/** Add one page view (total and per page); returns the new total, or null. */
export async function countVisit(path, env = process.env, fetchImpl = fetch) {
  const results = await redisPipeline(
    redisConfig(env),
    [["INCR", VISITS_KEY], ["HINCRBY", PAGES_KEY, path, 1]],
    fetchImpl,
  );
  return results ? toCount(results[0]) : null;
}

/**
 * A page path the counter accepts, normalised (`/download.html` -> `/download`,
 * `/index.html` -> `/`), or null. Only short same-site paths: no scheme, host,
 * query, fragment, `..` or odd characters.
 */
export function normalisePath(value) {
  if (typeof value !== "string") return null;
  if (value.length === 0 || value.length > MAX_PATH_LENGTH) return null;
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  if (!/^\/[A-Za-z0-9._~\-/]*$/.test(value) || value.includes("..")) return null;
  let path = value.replace(/\/{2,}/g, "/");
  path = path.replace(/\.html$/, "");
  path = path.replace(/\/index$/, "/");
  if (path.length > 1) path = path.replace(/\/+$/, "");
  return path || "/";
}

/** Sum download_count per asset name across every tb-v* release. */
export function aggregateDownloads(releases) {
  if (!Array.isArray(releases)) return null;
  const assets = {};
  let total = 0;
  for (const release of releases) {
    if (!release || typeof release.tag_name !== "string" || !release.tag_name.startsWith(TAG_PREFIX)) continue;
    for (const asset of Array.isArray(release.assets) ? release.assets : []) {
      const count = Number(asset && asset.download_count);
      if (!asset || typeof asset.name !== "string" || !Number.isFinite(count) || count < 0) continue;
      assets[asset.name] = (assets[asset.name] || 0) + count;
      // Fetching the checksum list isn't a download of TB.
      if (asset.name !== "SHA256SUMS.txt") total += count;
    }
  }
  const sorted = {};
  for (const name of Object.keys(assets).sort()) sorted[name] = assets[name];
  return { total, assets: sorted };
}

let downloadsCache = null; // { at, value }

export function resetDownloadsCache() {
  downloadsCache = null;
}

/** Download counts from GitHub ({total, assets}), cached for 10 minutes; null when GitHub fails. */
export async function readDownloads(env = process.env, fetchImpl = fetch, now = Date.now()) {
  if (downloadsCache && now - downloadsCache.at < DOWNLOADS_TTL_MS) return downloadsCache.value;
  const headers = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": USER_AGENT,
  };
  if (env.GITHUB_TOKEN) headers.Authorization = `Bearer ${env.GITHUB_TOKEN}`;
  try {
    const res = await fetchImpl(RELEASES_URL, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) throw new Error(`GitHub answered ${res.status}`);
    const value = aggregateDownloads(await res.json());
    if (!value) throw new Error("unexpected GitHub response");
    downloadsCache = { at: now, value };
    return value;
  } catch {
    // Serve the last good numbers while GitHub is unhappy, rather than nothing.
    return downloadsCache ? downloadsCache.value : null;
  }
}

export function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

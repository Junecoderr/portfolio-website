// GET /api/stats -> {visits, downloads: {total, assets: {name: count}}, updated}
// visits or downloads are null when their source is unavailable. See api/_lib/stats.js.

import { STATS_CACHE_CONTROL, json, readDownloads, readVisits } from "./_lib/stats.js";

export async function handleStats(request, env = process.env, fetchImpl = fetch) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json({ error: "method not allowed" }, 405, { Allow: "GET, HEAD", "Cache-Control": "no-store" });
  }
  let visits = null;
  let downloads = null;
  try {
    [visits, downloads] = await Promise.all([readVisits(env, fetchImpl), readDownloads(env, fetchImpl)]);
  } catch {
    // Both readers already swallow their errors; this is belt and braces.
  }
  return json({ visits, downloads, updated: new Date().toISOString() }, 200, {
    "Cache-Control": STATS_CACHE_CONTROL,
  });
}

export function GET(request) {
  return handleStats(request);
}

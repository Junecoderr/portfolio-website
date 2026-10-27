// POST /api/hit with JSON {"path": "/download"} -> {visits}
// Adds one page view to the total and to that page's count. No cookies; the
// visitor's IP address and user agent are never stored (the user agent is only
// checked against a list of bots). See api/_lib/stats.js.

import { MAX_BODY_BYTES, countVisit, isBot, json, normalisePath } from "./_lib/stats.js";

const NO_STORE = { "Cache-Control": "no-store" };

function sameSite(request) {
  // Browsers send Sec-Fetch-Site; refuse hits posted from other sites.
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "same-site" && site !== "none") return false;
  const origin = request.headers.get("origin");
  if (origin) {
    let host;
    try { host = new URL(origin).host; } catch { return false; }
    const target = request.headers.get("x-forwarded-host") || request.headers.get("host") || new URL(request.url).host;
    if (host !== target) return false;
  }
  return true;
}

async function readBody(request) {
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > MAX_BODY_BYTES) return { tooLarge: true };
  if (!request.body) return { text: "" };
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      try { await reader.cancel(); } catch { /* ignore */ }
      return { tooLarge: true };
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) { bytes.set(c, offset); offset += c.byteLength; }
  return { text: new TextDecoder().decode(bytes) };
}

export async function handleHit(request, env = process.env, fetchImpl = fetch) {
  if (request.method !== "POST") {
    return json({ error: "method not allowed" }, 405, { Allow: "POST", ...NO_STORE });
  }
  if (!sameSite(request)) return json({ error: "cross-site request" }, 403, NO_STORE);

  let body;
  try {
    body = await readBody(request);
  } catch {
    return json({ error: "bad body" }, 400, NO_STORE);
  }
  if (body.tooLarge) return json({ error: "body too large" }, 413, NO_STORE);
  let data;
  try {
    data = JSON.parse(body.text);
  } catch {
    return json({ error: "body must be JSON" }, 400, NO_STORE);
  }
  const path = normalisePath(data && typeof data === "object" ? data.path : undefined);
  if (!path) return json({ error: "path must be a short path on this site" }, 400, NO_STORE);

  if (isBot(request.headers.get("user-agent"))) {
    return json({ visits: null, counted: false }, 202, NO_STORE);
  }
  let visits = null;
  try {
    visits = await countVisit(path, env, fetchImpl);
  } catch {
    visits = null;
  }
  return json({ visits, counted: visits !== null }, 200, NO_STORE);
}

export function POST(request) {
  return handleHit(request);
}

const notAllowed = (request) => handleHit(request);
export { notAllowed as GET, notAllowed as PUT, notAllowed as PATCH, notAllowed as DELETE };

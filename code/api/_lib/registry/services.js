// Plumbing for the TB Package Index: JSON responses, request bodies, cookies,
// HMAC signing, Upstash Redis over REST and Vercel Blob over REST. Everything
// is plain fetch + node:crypto; no npm packages.

import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import { redisConfig } from "../stats.js";

const TIMEOUT_MS = 8000;
export const KEY_PREFIX = "pkg:";
export const COOKIE_NAME = "__Host-tb_registry";

export class ApiError extends Error {
  constructor(status, message, extra = {}, retryAfter = 0) {
    super(message);
    this.status = status;
    this.extra = extra;
    this.retryAfter = retryAfter;
  }
}

export function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
  });
}

export function redirect(location, headers = {}) {
  return new Response(null, { status: 303, headers: { Location: location, "Cache-Control": "no-store", ...headers } });
}

/** Read a request body, refusing more than `limit` bytes (declared or actual). */
export async function readBytes(request, limit) {
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > limit) throw new ApiError(413, `The upload is bigger than ${Math.floor(limit / 1024 / 1024) || limit} ${limit >= 1024 * 1024 ? "MB" : "bytes"}.`);
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      try { await reader.cancel(); } catch { /* ignore */ }
      throw new ApiError(413, `The upload is bigger than ${Math.floor(limit / 1024 / 1024) || limit} ${limit >= 1024 * 1024 ? "MB" : "bytes"}.`);
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) { bytes.set(c, offset); offset += c.byteLength; }
  return bytes;
}

export async function readJson(request, limit = 8 * 1024) {
  const type = request.headers.get("content-type") || "";
  if (!type.toLowerCase().startsWith("application/json")) throw new ApiError(415, "Send JSON (Content-Type: application/json).");
  const bytes = await readBytes(request, limit);
  if (!bytes.length) return {};
  try {
    const value = JSON.parse(new TextDecoder().decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not an object");
    return value;
  } catch {
    throw new ApiError(400, "The body must be a JSON object.");
  }
}

/** Refuse cookie-authenticated writes from other sites (Origin / Sec-Fetch-Site). */
export function assertSameSite(request) {
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") throw new ApiError(403, "Cross-site request refused.");
  const origin = request.headers.get("origin");
  if (origin) {
    let host;
    try { host = new URL(origin).host; } catch { throw new ApiError(403, "Cross-site request refused."); }
    const target = request.headers.get("x-forwarded-host") || request.headers.get("host") || new URL(request.url).host;
    if (host !== target) throw new ApiError(403, "Cross-site request refused.");
  } else if (!site) {
    // Browsers always send one of the two on a POST; scripts use tokens instead.
    throw new ApiError(403, "Cross-site request refused.");
  }
}

// ---------------------------------------------------------------- crypto

export function b64url(bytes) {
  return Buffer.from(bytes).toString("base64url");
}

export function randomToken(bytes = 32) {
  return b64url(randomBytes(bytes));
}

export function sha256Hex(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function pkceChallenge(verifier) {
  return createHash("sha256").update(verifier).digest("base64url");
}

export function hmacHex(secret, purpose, value) {
  return createHmac("sha256", secret).update(`${purpose}:${value}`).digest("hex");
}

export function safeEqual(a, b) {
  const x = Buffer.from(String(a));
  const y = Buffer.from(String(b));
  return x.length === y.length && timingSafeEqual(x, y);
}

/** The HMAC key, or null when REGISTRY_SESSION_SECRET is missing or too short. */
export function sessionSecret(env) {
  const s = env.REGISTRY_SESSION_SECRET;
  return typeof s === "string" && s.length >= 32 ? s : null;
}

export function signValue(secret, payload) {
  const body = b64url(Buffer.from(JSON.stringify(payload)));
  return `v1.${body}.${createHmac("sha256", secret).update(`cookie:${body}`).digest("base64url")}`;
}

export function verifyValue(secret, value, now) {
  if (typeof value !== "string" || value.length > 2048) return null;
  const parts = value.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return null;
  const expected = createHmac("sha256", secret).update(`cookie:${parts[1]}`).digest("base64url");
  if (!safeEqual(expected, parts[2])) return null;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    if (!payload || typeof payload.exp !== "number" || payload.exp < now) return null;
    return payload;
  } catch {
    return null;
  }
}

export function readCookie(request, name = COOKIE_NAME) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq > 0 && part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim();
  }
  return null;
}

export function setCookie(value, maxAgeSeconds) {
  return `${COOKIE_NAME}=${value}; Path=/; Max-Age=${maxAgeSeconds}; HttpOnly; Secure; SameSite=Lax`;
}

export function clearCookie() {
  return `${COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;
}

// ---------------------------------------------------------------- Redis (Upstash REST)

export function registryRedis(env, fetchImpl) {
  const config = redisConfig(env);
  if (!config) return null;
  async function pipeline(commands) {
    if (!commands.length) return [];
    let res;
    try {
      res = await fetchImpl(`${config.url}/pipeline`, {
        method: "POST",
        headers: { Authorization: `Bearer ${config.token}`, "Content-Type": "application/json" },
        body: JSON.stringify(commands),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch {
      throw new ApiError(503, "The package database isn't answering; try again in a minute.");
    }
    if (!res.ok) throw new ApiError(503, "The package database isn't answering; try again in a minute.");
    const data = await res.json().catch(() => null);
    if (!Array.isArray(data) || data.length !== commands.length || data.some((d) => !d || d.error)) {
      throw new ApiError(503, "The package database gave an unexpected answer.");
    }
    return data.map((d) => d.result);
  }
  return {
    pipeline,
    async one(...command) {
      return (await pipeline([command]))[0];
    },
  };
}

/** Upstash returns HGETALL as a flat [field, value, ...] list. */
export function pairs(list) {
  const out = {};
  if (Array.isArray(list)) for (let i = 0; i + 1 < list.length; i += 2) out[list[i]] = list[i + 1];
  else if (list && typeof list === "object") Object.assign(out, list);
  return out;
}

export function parseJson(text, fallback = null) {
  if (typeof text !== "string") return fallback;
  try { return JSON.parse(text); } catch { return fallback; }
}

// ---------------------------------------------------------------- Vercel Blob (REST)
// The same requests @vercel/blob makes: PUT https://vercel.com/api/blob/?pathname=...
// with x-vercel-blob-access: private, GET https://<store>.private.blob.vercel-storage.com/<path>
// with the token, and POST .../delete. Read-write tokens look like
// vercel_blob_rw_<storeId>_<secret>.

export const BLOB_API = "https://vercel.com/api/blob";
export const BLOB_API_VERSION = "13";

export function blobConfig(env) {
  const token = env.BLOB_READ_WRITE_TOKEN;
  if (typeof token !== "string" || !token.startsWith("vercel_blob_rw_")) return null;
  let storeId = token.split("_")[3] || "";
  if (storeId.startsWith("store_")) storeId = storeId.slice(6);
  if (!/^[A-Za-z0-9]+$/.test(storeId)) return null;
  return { token, storeId, api: (env.VERCEL_BLOB_API_URL || BLOB_API).replace(/\/+$/, "") };
}

export function blobClient(env, fetchImpl) {
  const config = blobConfig(env);
  if (!config) return null;
  const headers = (extra = {}) => ({
    authorization: `Bearer ${config.token}`,
    "x-api-version": BLOB_API_VERSION,
    "x-vercel-blob-store-id": config.storeId,
    ...extra,
  });
  return {
    url(pathname) {
      return `https://${config.storeId}.private.blob.vercel-storage.com/${pathname}`;
    },
    async put(pathname, bytes, contentType = "application/zip") {
      const params = new URLSearchParams({ pathname });
      let res;
      try {
        res = await fetchImpl(`${config.api}/?${params}`, {
          method: "PUT",
          headers: headers({
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-content-type": contentType,
          }),
          body: bytes,
          signal: AbortSignal.timeout(20000),
        });
      } catch {
        throw new ApiError(503, "Package storage isn't answering; try again in a minute.");
      }
      if (!res.ok) throw new ApiError(503, "Package storage refused the upload; try again later.");
      const data = await res.json().catch(() => ({}));
      return { url: typeof data.url === "string" ? data.url : this.url(pathname), pathname };
    },
    /** The stored file as a Response, or null if it's gone. */
    async get(pathname) {
      let res;
      try {
        res = await fetchImpl(this.url(pathname), {
          method: "GET",
          headers: { authorization: `Bearer ${config.token}` },
          signal: AbortSignal.timeout(20000),
        });
      } catch {
        throw new ApiError(503, "Package storage isn't answering; try again in a minute.");
      }
      if (res.status === 404) return null;
      if (!res.ok) throw new ApiError(503, "Package storage isn't answering; try again in a minute.");
      return res;
    },
    async del(pathnames) {
      const urls = pathnames.map((p) => this.url(p));
      if (!urls.length) return;
      try {
        await fetchImpl(`${config.api}/delete`, {
          method: "POST",
          headers: headers({ "content-type": "application/json" }),
          body: JSON.stringify({ urls }),
          signal: AbortSignal.timeout(TIMEOUT_MS),
        });
      } catch {
        // A file left behind is harmless: nothing points at it any more.
      }
    },
  };
}

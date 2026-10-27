// The TB Package Index (code.tanisha.ceo/packages): every /api/registry/* route.
// api/registry.js is the Vercel Function; vercel.json rewrites
// /api/registry/<route> to it. See CONTRIBUTING.md ("Package index") for the
// routes, the Redis keys and the review rules.
//
// Review rules: the first version of a new package waits in the review queue
// until an admin approves it (pending -> approved or rejected). Once a package
// is approved, later versions from its owner are published at once; owners and
// admins can yank any version (it stays installable by exact version, but
// ranges skip it), and admins can remove versions or whole packages.

import { SEED_OWNER, SEED_PACKAGES, STDLIB_NAMES } from "./seed.js";
import { MAX_ARCHIVE_BYTES, NAME_RE, checkName, compareVersions, isPrerelease, parseVersion, similarNames } from "./rules.js";
import { ArchiveError, validateArchive } from "./zip.js";
import {
  ApiError, assertSameSite, blobClient, clearCookie, hmacHex, json, pairs, parseJson, pkceChallenge,
  randomToken, readBytes, readCookie, readJson, redirect, registryRedis, sessionSecret, setCookie, sha256Hex,
  signValue, verifyValue, safeEqual,
} from "./services.js";

export const SESSION_TTL = 7 * 24 * 3600;
export const OAUTH_TTL = 600;
export const PUBLISH_LIMIT = 10; // per user per hour
export const REPORT_LIMIT = 5; // per user per hour
export const TOKEN_CREATE_LIMIT = 20; // per user per hour
export const MAX_TOKENS = 10;
const GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize";
const GITHUB_TOKEN = "https://github.com/login/oauth/access_token";
const GITHUB_USER = "https://api.github.com/user";
const USER_AGENT = "tb-package-index (code.tanisha.ceo)";
const PUBLIC_CACHE = "public, s-maxage=60, stale-while-revalidate=300";

export const K = {
  meta: (n) => `pkg:pkg:${n}`,
  versions: (n) => `pkg:versions:${n}`,
  readme: (n, v) => `pkg:readme:${n}:${v}`,
  owner: (n) => `pkg:owner:${n}`,
  claim: (n, v) => `pkg:claim:${n}:${v}`,
  dl: (n) => `pkg:dl:${n}`,
  owned: (uid) => `pkg:owned:${uid}`,
  user: (uid) => `pkg:user:${uid}`,
  session: (h) => `pkg:session:${h}`,
  token: (h) => `pkg:token:${h}`,
  tokens: (uid) => `pkg:tokens:${uid}`,
  rate: (kind, id, bucket) => `pkg:rl:${kind}:${id}:${bucket}`,
  names: "pkg:names",
  listed: "pkg:listed",
  queue: "pkg:queue",
  downloads: "pkg:downloads",
  reports: "pkg:reports",
  audit: "pkg:audit",
};

export const blobPath = (name, version) => `packages/${name}/${version}.zip`;

const SEED = new Map(SEED_PACKAGES.map((p) => [p.name, p]));

function seedVersion(p) {
  return {
    version: p.version, status: "approved", sha256: p.sha256, size: p.size, created: p.created,
    publisher: SEED_OWNER, publisherLogin: SEED_OWNER, description: p.description, keywords: p.keywords,
    license: p.license, dependencies: p.dependencies, entry: p.entry, files: p.files.length, seed: true,
  };
}

// ---------------------------------------------------------------- context

function makeContext(request, env, fetchImpl, now) {
  const url = new URL(request.url);
  let route = url.searchParams.get("route");
  if (route === null) {
    const m = url.pathname.match(/^\/api\/registry\/?(.*)$/);
    route = m ? m[1] : "";
  }
  const parts = route.split("/").filter(Boolean).map((p) => {
    try { return decodeURIComponent(p); } catch { return p; }
  });
  return {
    request, env, now, url, parts, fetch: fetchImpl,
    redis: registryRedis(env, fetchImpl),
    blob: blobClient(env, fetchImpl),
    secret: sessionSecret(env),
    siteUrl: String(env.REGISTRY_SITE_URL || url.origin).replace(/\/+$/, ""),
  };
}

function needRedis(ctx) {
  if (!ctx.redis) throw new ApiError(503, "The package index database isn't set up yet (KV_REST_API_URL / KV_REST_API_TOKEN are missing).");
  return ctx.redis;
}

function needBlob(ctx) {
  if (!ctx.blob) throw new ApiError(503, "Package storage isn't set up yet (BLOB_READ_WRITE_TOKEN is missing).");
  return ctx.blob;
}

function needSecret(ctx) {
  if (!ctx.secret) throw new ApiError(503, "Sign-in isn't set up yet (REGISTRY_SESSION_SECRET is missing or shorter than 32 characters).");
  return ctx.secret;
}

function iso(ctx) {
  return new Date(ctx.now).toISOString();
}

// ---------------------------------------------------------------- users and auth

export function adminIds(env) {
  return String(env.REGISTRY_ADMINS || "").split(",").map((s) => s.trim()).filter(Boolean);
}

export function isAdmin(env, user) {
  return Boolean(user && user.id !== SEED_OWNER && adminIds(env).includes(String(user.id)));
}

async function getUser(ctx, uid) {
  if (uid === SEED_OWNER) return { id: SEED_OWNER, login: SEED_OWNER, name: "TB" };
  if (!ctx.redis) return null;
  return parseJson(await ctx.redis.one("GET", K.user(uid)));
}

async function cookieUser(ctx) {
  if (!ctx.secret || !ctx.redis) return null;
  const payload = verifyValue(ctx.secret, readCookie(ctx.request), ctx.now);
  if (!payload || payload.k !== "s" || typeof payload.sid !== "string") return null;
  const uid = await ctx.redis.one("GET", K.session(hmacHex(ctx.secret, "session", payload.sid)));
  return uid ? getUser(ctx, String(uid)) : null;
}

function bearer(ctx) {
  const header = ctx.request.headers.get("authorization") || "";
  const m = header.match(/^Bearer\s+(\S+)$/i);
  return m ? m[1] : null;
}

async function tokenUser(ctx, token) {
  if (!/^tbr_[A-Za-z0-9_-]{43}$/.test(token)) throw new ApiError(401, "That API token isn't valid. Make a new one at /packages/publish and run tb login.");
  needSecret(ctx);
  needRedis(ctx);
  const record = parseJson(await ctx.redis.one("GET", K.token(hmacHex(ctx.secret, "token", token))));
  const user = record ? await getUser(ctx, String(record.uid)) : null;
  if (!user) throw new ApiError(401, "That API token isn't valid (revoked?). Make a new one at /packages/publish and run tb login.");
  return { user, via: "token", tokenId: record.id };
}

/** Who is asking: {user, via: "token"|"cookie"} or null. */
async function viewer(ctx) {
  const token = bearer(ctx);
  if (token) return tokenUser(ctx, token);
  const user = await cookieUser(ctx);
  return user ? { user, via: "cookie" } : null;
}

async function requireViewer(ctx, { cookieOnly = false } = {}) {
  const who = await viewer(ctx);
  if (!who) {
    needSecret(ctx);
    needRedis(ctx);
    throw new ApiError(401, "Sign in first (or send an API token).");
  }
  if (cookieOnly && who.via !== "cookie") throw new ApiError(403, "Do this on the website while signed in.");
  if (who.via === "cookie" && ctx.request.method !== "GET" && ctx.request.method !== "HEAD") assertSameSite(ctx.request);
  return who;
}

async function requireAdmin(ctx) {
  const who = await requireViewer(ctx);
  if (!isAdmin(ctx.env, who.user)) throw new ApiError(403, "Only index admins can do that.");
  return who;
}

async function rateLimit(ctx, kind, id, limit, windowSeconds = 3600) {
  const bucket = Math.floor(ctx.now / 1000 / windowSeconds);
  const key = K.rate(kind, id, bucket);
  const [count] = await needRedis(ctx).pipeline([["INCR", key], ["EXPIRE", key, String(windowSeconds)]]);
  if (Number(count) > limit) {
    const retry = (bucket + 1) * windowSeconds - Math.floor(ctx.now / 1000);
    throw new ApiError(429, `Too many requests: at most ${limit} an hour. Try again in ${Math.ceil(retry / 60)} minutes.`, {}, retry);
  }
}

async function audit(ctx, who, action, name, version = null, reason = "") {
  const entry = JSON.stringify({ at: iso(ctx), by: who.user.login, byId: who.user.id, action, name, version, reason });
  await ctx.redis.pipeline([["LPUSH", K.audit, entry], ["LTRIM", K.audit, "0", "999"]]);
}

// ---------------------------------------------------------------- packages

async function loadVersions(ctx, name) {
  const out = {};
  const seed = SEED.get(name);
  if (seed) out[seed.version] = seedVersion(seed);
  if (ctx.redis) {
    const stored = pairs(await ctx.redis.one("HGETALL", K.versions(name)));
    for (const [v, text] of Object.entries(stored)) {
      const record = parseJson(text);
      if (record) out[v] = record;
    }
  }
  return out;
}

const VISIBLE = new Set(["approved", "yanked"]);

function sortedVersions(versions) {
  return Object.keys(versions).sort((a, b) => compareVersions(b, a));
}

/** The package summary written to pkg:pkg:<name>, from its versions. */
function computeMeta(name, versions, base) {
  const order = sortedVersions(versions);
  const approved = order.filter((v) => versions[v].status === "approved");
  const latest = approved.find((v) => !isPrerelease(v)) || approved[0] || null;
  const shown = latest || order.find((v) => VISIBLE.has(versions[v].status)) || order[0] || null;
  const rec = shown ? versions[shown] : {};
  let status = "empty";
  if (order.some((v) => VISIBLE.has(versions[v].status))) status = "approved";
  else if (order.some((v) => versions[v].status === "pending")) status = "pending";
  else if (order.some((v) => versions[v].status === "rejected")) status = "rejected";
  const summary = {};
  for (const v of order) {
    const r = versions[v];
    if (VISIBLE.has(r.status)) summary[v] = { sha256: r.sha256, status: r.status, size: r.size, created: r.created };
  }
  return {
    name, owner: base.owner, ownerLogin: base.ownerLogin, created: base.created, updated: base.updated || base.created,
    flags: base.flags || [], status, latest, shown,
    description: rec.description || "", keywords: rec.keywords || [], license: rec.license || "",
    summary,
  };
}

async function loadMeta(ctx, name, versions = null) {
  let meta = null;
  if (ctx.redis) meta = parseJson(await ctx.redis.one("GET", K.meta(name)));
  if (meta) return meta;
  const seed = SEED.get(name);
  if (!seed) return null;
  return computeMeta(name, versions || { [seed.version]: seedVersion(seed) }, {
    owner: SEED_OWNER, ownerLogin: SEED_OWNER, created: seed.created,
  });
}

/** Recompute and store a package's summary after any change. */
async function refresh(ctx, name, base = null) {
  const versions = await loadVersions(ctx, name);
  const old = base || (await loadMeta(ctx, name, versions)) || {};
  const meta = computeMeta(name, versions, { ...old, updated: iso(ctx) });
  await ctx.redis.pipeline([
    ["SET", K.meta(name), JSON.stringify(meta)],
    meta.status === "approved" ? ["SADD", K.listed, name] : ["SREM", K.listed, name],
  ]);
  return { meta, versions };
}

function validName(name) {
  return typeof name === "string" && (NAME_RE.test(name) || SEED.has(name));
}

function downloadUrl(ctx, name, version) {
  return `${ctx.siteUrl}/api/registry/packages/${name}/${version}/download`;
}

/** Every listed package: {name: entry} for the index and search. */
async function listedPackages(ctx) {
  const names = new Set(SEED.keys());
  let metas = {};
  let downloads = {};
  let degraded = !ctx.redis;
  if (ctx.redis) {
    try {
      const listed = await ctx.redis.one("SMEMBERS", K.listed);
      for (const n of listed || []) names.add(n);
      const all = [...names].sort();
      const [values, counts] = await ctx.redis.pipeline([["MGET", ...all.map(K.meta)], ["HGETALL", K.downloads]]);
      all.forEach((n, i) => { const m = parseJson(values[i]); if (m) metas[n] = m; });
      downloads = pairs(counts);
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      degraded = true;
      metas = {};
    }
  }
  const packages = {};
  for (const name of [...names].sort()) {
    const meta = metas[name] || (await loadMeta({ ...ctx, redis: null }, name));
    if (!meta || meta.status !== "approved") continue;
    const versions = {};
    for (const [v, s] of Object.entries(meta.summary || {})) versions[v] = s.status === "yanked" ? { sha256: s.sha256, yanked: true } : { sha256: s.sha256 };
    if (!Object.keys(versions).length) continue;
    packages[name] = {
      description: meta.description, version: meta.latest, keywords: meta.keywords, license: meta.license,
      owner: meta.ownerLogin, downloads: degraded ? null : Number(downloads[name] || 0), versions,
      ...(meta.latest ? { zip: downloadUrl(ctx, name, meta.latest) } : {}),
    };
  }
  return { packages, degraded };
}

// ---------------------------------------------------------------- read API

async function getIndex(ctx) {
  const { packages, degraded } = await listedPackages(ctx);
  return json({
    format: "tb-registry-1", name: "TB Package Index", registry: `${ctx.siteUrl}/api/registry`,
    generated: iso(ctx), degraded, packages,
  }, 200, { "Cache-Control": degraded ? "no-store" : PUBLIC_CACHE });
}

export function searchPackages(packages, query) {
  const terms = String(query || "").toLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
  const results = [];
  for (const [name, p] of Object.entries(packages)) {
    let score = 0;
    let all = true;
    for (const t of terms) {
      let s = 0;
      if (name === t) s += 100;
      else if (name.startsWith(t)) s += 50;
      else if (name.includes(t)) s += 30;
      if ((p.keywords || []).includes(t)) s += 20;
      else if ((p.keywords || []).some((k) => k.includes(t))) s += 8;
      if ((p.description || "").toLowerCase().includes(t)) s += 10;
      if (!s) { all = false; break; }
      score += s;
    }
    if (all) results.push({ name, score, ...p });
  }
  results.sort((a, b) => b.score - a.score || (b.downloads || 0) - (a.downloads || 0) || (a.name < b.name ? -1 : 1));
  return results.slice(0, 50).map(({ score, ...rest }) => rest);
}

async function getSearch(ctx) {
  const q = (ctx.url.searchParams.get("q") || "").trim().slice(0, 100);
  const { packages, degraded } = await listedPackages(ctx);
  return json({ query: q, degraded, results: searchPackages(packages, q) }, 200, { "Cache-Control": degraded ? "no-store" : PUBLIC_CACHE });
}

async function canSeeHidden(ctx, meta) {
  let who = null;
  try { who = await viewer(ctx); } catch { who = null; }
  if (!who) return { who: null, hidden: false };
  return { who, hidden: who.user.id === meta.owner || isAdmin(ctx.env, who.user) };
}

async function readmeFor(ctx, name, version) {
  const seed = SEED.get(name);
  if (ctx.redis) {
    const text = await ctx.redis.one("GET", K.readme(name, version));
    if (typeof text === "string") return text;
  }
  return seed && seed.version === version ? seed.readme : "";
}

async function getPackage(ctx, name, onlyVersion = null) {
  if (!validName(name)) throw new ApiError(404, `There's no package called '${String(name).slice(0, 60)}'.`);
  const versions = await loadVersions(ctx, name);
  const meta = await loadMeta(ctx, name, versions);
  if (!meta) throw new ApiError(404, `There's no package called '${name}'.`);
  const { hidden } = await canSeeHidden(ctx, meta);
  if (meta.status !== "approved" && !hidden) throw new ApiError(404, `There's no package called '${name}'.`);
  const counts = ctx.redis ? pairs(await ctx.redis.one("HGETALL", K.dl(name)).catch(() => [])) : {};
  const list = sortedVersions(versions)
    .filter((v) => hidden || VISIBLE.has(versions[v].status))
    .map((v) => {
      const r = versions[v];
      const item = {
        version: v, status: r.status, sha256: r.sha256, size: r.size, created: r.created,
        downloads: Number(counts[v] || 0), dependencies: r.dependencies || {}, entry: r.entry,
      };
      if (r.status === "yanked") item.yankReason = r.yankReason || "";
      if (hidden) Object.assign(item, { reason: r.reason || "", flags: r.flags || [], yankedBy: r.yankedBy || null });
      return item;
    });
  if (onlyVersion) {
    const item = list.find((x) => x.version === onlyVersion);
    if (!item) throw new ApiError(404, `${name} has no version ${String(onlyVersion).slice(0, 40)}.`);
    item.readme = await readmeFor(ctx, name, onlyVersion);
    return json({ name, ...item }, 200, { "Cache-Control": hidden ? "no-store" : PUBLIC_CACHE });
  }
  const show = meta.shown || (list[0] && list[0].version);
  const body = {
    name, description: meta.description, owner: meta.ownerLogin, status: meta.status, latest: meta.latest,
    keywords: meta.keywords, license: meta.license, created: meta.created, updated: meta.updated,
    downloads: list.reduce((n, v) => n + v.downloads, 0),
    install: `tb add ${name}`, versions: list,
    readme: show ? await readmeFor(ctx, name, show) : "", readmeVersion: show || null,
  };
  if (hidden) body.flags = meta.flags || [];
  return json(body, 200, { "Cache-Control": hidden || ctx.request.headers.get("cookie") ? "no-store" : PUBLIC_CACHE });
}

async function download(ctx, name, version) {
  if (!validName(name) || !parseVersion(version)) throw new ApiError(404, "No such package version.");
  const versions = await loadVersions(ctx, name);
  const record = versions[version];
  if (!record) throw new ApiError(404, `${name} has no version ${version}.`);
  let count = VISIBLE.has(record.status);
  if (!count) {
    const meta = await loadMeta(ctx, name, versions);
    const { hidden } = meta ? await canSeeHidden(ctx, meta) : { hidden: false };
    if (!hidden || record.status === "rejected") throw new ApiError(404, `${name} has no version ${version}.`);
  }
  const headers = {
    "Content-Type": "application/zip",
    "Content-Disposition": `attachment; filename="${name}-${version}.zip"`,
    "Cache-Control": "no-store",
    "X-TB-SHA256": record.sha256,
  };
  let body;
  const seed = SEED.get(name);
  if (record.seed && seed && seed.version === version) {
    const bytes = Buffer.from(seed.zip, "base64");
    headers["Content-Length"] = String(bytes.length);
    body = bytes;
  } else {
    const res = await needBlob(ctx).get(blobPath(name, version));
    if (!res) throw new ApiError(404, `The archive for ${name} ${version} is missing; tell the index admins.`);
    const len = res.headers.get("content-length");
    if (len) headers["Content-Length"] = len;
    body = res.body;
  }
  if (count && ctx.redis) {
    try {
      await ctx.redis.pipeline([["HINCRBY", K.dl(name), version, "1"], ["HINCRBY", K.downloads, name, "1"]]);
    } catch {
      // A missed count never blocks a download.
    }
  }
  return new Response(body, { status: 200, headers });
}

// ---------------------------------------------------------------- publishing

async function publish(ctx, name, version) {
  const token = bearer(ctx);
  if (!token) throw new ApiError(401, "Publishing needs an API token: make one at /packages/publish, then run tb login.");
  const who = await tokenUser(ctx, token);
  const nameProblem = checkName(name);
  if (nameProblem) throw new ApiError(400, nameProblem);
  if (!parseVersion(version)) throw new ApiError(400, `'${String(version).slice(0, 40)}' isn't a version like 1.2.3 (semantic versioning).`);
  const redis = needRedis(ctx);
  const blob = needBlob(ctx);
  const publishKey = K.rate("publish", who.user.id, Math.floor(ctx.now / 3600000));
  if (Number((await redis.one("GET", publishKey)) || 0) >= PUBLISH_LIMIT) {
    const retry = 3600 - Math.floor(ctx.now / 1000) % 3600;
    throw new ApiError(429, `Too many uploads: at most ${PUBLISH_LIMIT} an hour. Try again in ${Math.ceil(retry / 60)} minutes.`, {}, retry);
  }
  const type = (ctx.request.headers.get("content-type") || "").toLowerCase();
  if (!type.startsWith("application/zip") && !type.startsWith("application/octet-stream")) {
    throw new ApiError(415, "Upload the package as a zip (Content-Type: application/zip).");
  }
  const bytes = await readBytes(ctx.request, MAX_ARCHIVE_BYTES);
  let info;
  try {
    info = validateArchive(bytes, name, version);
  } catch (err) {
    if (err instanceof ArchiveError) throw new ApiError(400, err.message);
    throw err;
  }
  const admin = isAdmin(ctx.env, who.user);
  const uid = String(who.user.id);
  const owner = SEED.has(name) ? SEED_OWNER : await redis.one("GET", K.owner(name));
  if (owner && owner !== uid && !(owner === SEED_OWNER && admin)) {
    throw new ApiError(403, `'${name}' belongs to another account. Pick another name.`);
  }
  const versions = await loadVersions(ctx, name);
  const existing = versions[version];
  if (existing && existing.status !== "rejected") {
    throw new ApiError(409, `${name} ${version} already exists. Versions can't be replaced; bump the version in tb.toml.`);
  }
  const claimed = await redis.one("SET", K.claim(name, version), uid, "NX");
  if (claimed === null && !(existing && existing.status === "rejected")) {
    throw new ApiError(409, `${name} ${version} already exists. Versions can't be replaced; bump the version in tb.toml.`);
  }
  const isNew = !owner;
  const flags = [];
  if (isNew) {
    const known = new Set([...(await redis.one("SMEMBERS", K.names) || []), ...SEED.keys(), ...STDLIB_NAMES]);
    const similar = similarNames(name, known);
    if (similar.length) flags.push(`similar to existing names: ${similar.join(", ")}`);
    if (name.startsWith("tb-")) flags.push("uses the tb- prefix of the bundled packages");
    const got = await redis.one("SET", K.owner(name), uid, "NX");
    if (got === null) {
      await redis.one("DEL", K.claim(name, version));
      throw new ApiError(409, `Someone has just claimed '${name}'. Pick another name.`);
    }
  }
  const sha256 = sha256Hex(bytes);
  try {
    await blob.put(blobPath(name, version), bytes);
  } catch (err) {
    await redis.pipeline([["DEL", K.claim(name, version)], ...(isNew ? [["DEL", K.owner(name)]] : [])]);
    throw err;
  }
  const meta = await loadMeta(ctx, name, versions);
  const trusted = !isNew && meta && meta.status === "approved";
  const status = trusted ? "approved" : "pending";
  const record = {
    version, status, sha256, size: bytes.length, created: iso(ctx), publisher: uid, publisherLogin: who.user.login,
    description: info.description, keywords: info.keywords, license: info.license, dependencies: info.dependencies,
    entry: info.entry, files: info.fileCount, fileList: info.files.slice(0, 200), unpackedSize: info.unpackedSize,
    flags: isNew ? flags : [],
  };
  const commands = [
    ["HSET", K.versions(name), version, JSON.stringify(record)],
    ["SET", K.readme(name, version), info.readme],
    ["SADD", K.names, name],
    ["SADD", K.owned(owner || uid), name],
  ];
  if (status === "pending") commands.push(["SADD", K.queue, `${name}@${version}`]);
  commands.push(["INCR", publishKey], ["EXPIRE", publishKey, "3600"]);
  await redis.pipeline(commands);
  const base = meta && !isNew ? meta : { owner: uid, ownerLogin: who.user.login, created: iso(ctx), flags };
  await refresh(ctx, name, base);
  const message = status === "approved"
    ? `Published ${name} ${version}. Install it with: tb add ${name}`
    : `Submitted ${name} ${version} for review. It appears in the index once an admin approves it; see /packages/account for its status.`;
  return json({ name, version, status, sha256, size: bytes.length, flags: record.flags, message }, 201);
}

async function setYank(ctx, name, version, yank) {
  const who = await requireViewer(ctx);
  const body = await optionalJson(ctx.request);
  const versions = await loadVersions(ctx, name);
  const record = versions[version];
  const meta = record ? await loadMeta(ctx, name, versions) : null;
  const admin = isAdmin(ctx.env, who.user);
  if (!record || !meta || (meta.owner !== who.user.id && !admin)) throw new ApiError(404, `${name} has no version ${version} that you can change.`);
  needRedis(ctx);
  if (yank) {
    if (record.status !== "approved") throw new ApiError(409, `${name} ${version} is ${record.status}; only published versions can be yanked.`);
    record.status = "yanked";
    record.yankReason = String(body.reason || "").slice(0, 300);
    record.yankedBy = meta.owner === who.user.id ? "owner" : "admin";
    record.yankedAt = iso(ctx);
  } else {
    if (record.status !== "yanked") throw new ApiError(409, `${name} ${version} isn't yanked.`);
    if (record.yankedBy === "admin" && !admin) throw new ApiError(403, "An admin yanked this version; ask them to undo it.");
    record.status = "approved";
    delete record.yankReason;
    delete record.yankedBy;
    delete record.yankedAt;
  }
  await ctx.redis.one("HSET", K.versions(name), version, JSON.stringify(record));
  await refresh(ctx, name, meta);
  if (admin && meta.owner !== who.user.id) await audit(ctx, who, yank ? "yank" : "unyank", name, version, record.yankReason || "");
  return json({ name, version, status: record.status, message: yank ? `Yanked ${name} ${version}.` : `${name} ${version} is back.` });
}

async function optionalJson(request) {
  const type = request.headers.get("content-type") || "";
  if (!type) return {};
  return readJson(request);
}

async function report(ctx, name) {
  const who = await requireViewer(ctx);
  if (!validName(name)) throw new ApiError(404, "No such package.");
  const meta = await loadMeta(ctx, name);
  if (!meta || meta.status !== "approved") throw new ApiError(404, `There's no package called '${name}'.`);
  const body = await readJson(ctx.request);
  const reason = String(body.reason || "").trim();
  if (reason.length < 5 || reason.length > 1000) throw new ApiError(400, "Say what's wrong in 5 to 1000 characters.");
  const version = typeof body.version === "string" && parseVersion(body.version) ? body.version : null;
  await rateLimit(ctx, "report", who.user.id, REPORT_LIMIT);
  const entry = { id: randomToken(9), name, version, reason, by: who.user.login, byId: who.user.id, at: iso(ctx) };
  await ctx.redis.pipeline([["LPUSH", K.reports, JSON.stringify(entry)], ["LTRIM", K.reports, "0", "499"]]);
  return json({ reported: true, message: "Thanks. The index admins will take a look." }, 201);
}

// ---------------------------------------------------------------- account and tokens

async function me(ctx) {
  let who = null;
  try { who = await viewer(ctx); } catch (err) { if (!(err instanceof ApiError)) throw err; }
  const setup = {
    signIn: Boolean(ctx.env.GITHUB_CLIENT_ID && ctx.env.GITHUB_CLIENT_SECRET && ctx.secret && ctx.redis),
    storage: Boolean(ctx.blob),
    database: Boolean(ctx.redis),
  };
  if (!who) return json({ signedIn: false, setup });
  const u = who.user;
  return json({ signedIn: true, via: who.via, user: { id: u.id, login: u.login, name: u.name || "" }, admin: isAdmin(ctx.env, u), setup });
}

async function accountPackages(ctx) {
  const who = await requireViewer(ctx);
  const names = (await ctx.redis.one("SMEMBERS", K.owned(who.user.id))) || [];
  const packages = [];
  for (const name of [...names].sort()) {
    const versions = await loadVersions(ctx, name);
    const meta = await loadMeta(ctx, name, versions);
    if (!meta) continue;
    packages.push({
      name, status: meta.status, latest: meta.latest, description: meta.description,
      versions: sortedVersions(versions).map((v) => ({
        version: v, status: versions[v].status, created: versions[v].created, sha256: versions[v].sha256,
        reason: versions[v].reason || "", yankReason: versions[v].yankReason || "", yankedBy: versions[v].yankedBy || null,
      })),
    });
  }
  return json({ user: who.user.login, packages });
}

async function listTokens(ctx) {
  const who = await requireViewer(ctx);
  const tokens = Object.values(pairs(await ctx.redis.one("HGETALL", K.tokens(who.user.id))))
    .map((t) => parseJson(t)).filter(Boolean)
    .map(({ id, name, created }) => ({ id, name, created }))
    .sort((a, b) => (a.created < b.created ? 1 : -1));
  return json({ tokens });
}

async function createToken(ctx) {
  const who = await requireViewer(ctx, { cookieOnly: true });
  const secret = needSecret(ctx);
  const body = await readJson(ctx.request);
  const name = String(body.name || "").trim().slice(0, 40) || "tb CLI";
  if (/[\u0000-\u001f]/.test(name)) throw new ApiError(400, "Give the token a plain name.");
  await rateLimit(ctx, "token", who.user.id, TOKEN_CREATE_LIMIT);
  const existing = pairs(await ctx.redis.one("HGETALL", K.tokens(who.user.id)));
  if (Object.keys(existing).length >= MAX_TOKENS) throw new ApiError(400, `You have ${MAX_TOKENS} tokens already; revoke one first.`);
  const token = `tbr_${randomToken(32)}`;
  const hash = hmacHex(secret, "token", token);
  const id = randomToken(9);
  const created = iso(ctx);
  await ctx.redis.pipeline([
    ["SET", K.token(hash), JSON.stringify({ uid: who.user.id, id, name, created })],
    ["HSET", K.tokens(who.user.id), id, JSON.stringify({ id, name, created, hash })],
  ]);
  return json({ token, id, name, created, message: "Copy the token now: it won't be shown again." }, 201);
}

async function revokeToken(ctx, id) {
  const who = await requireViewer(ctx);
  const record = parseJson(await ctx.redis.one("HGET", K.tokens(who.user.id), String(id)));
  if (!record) throw new ApiError(404, "No such token.");
  await ctx.redis.pipeline([["DEL", K.token(record.hash)], ["HDEL", K.tokens(who.user.id), String(id)]]);
  return json({ revoked: true, id });
}

// ---------------------------------------------------------------- sign-in (GitHub OAuth with state + PKCE)

function safeNext(value) {
  return typeof value === "string" && /^\/packages(\/(publish|account|admin))?$/.test(value) ? value : "/packages/publish";
}

function oauthConfig(ctx) {
  const { GITHUB_CLIENT_ID: id, GITHUB_CLIENT_SECRET: secret } = ctx.env;
  if (!id || !secret || !ctx.secret || !ctx.redis) return null;
  return { id, secret, redirectUri: `${ctx.siteUrl}/api/registry/auth/callback` };
}

function login(ctx) {
  const config = oauthConfig(ctx);
  if (!config) return redirect("/packages/publish?error=signin-unavailable");
  const state = randomToken(24);
  const verifier = randomToken(48);
  const next = safeNext(ctx.url.searchParams.get("next"));
  const cookie = signValue(ctx.secret, { k: "o", st: state, cv: verifier, nx: next, exp: ctx.now + OAUTH_TTL * 1000 });
  const params = new URLSearchParams({
    client_id: config.id, redirect_uri: config.redirectUri, state,
    code_challenge: pkceChallenge(verifier), code_challenge_method: "S256", allow_signup: "true",
  });
  return redirect(`${GITHUB_AUTHORIZE}?${params}`, { "Set-Cookie": setCookie(cookie, OAUTH_TTL) });
}

async function callback(ctx) {
  const fail = (code) => redirect(`/packages/publish?error=${code}`, { "Set-Cookie": clearCookie() });
  const config = oauthConfig(ctx);
  if (!config) return redirect("/packages/publish?error=signin-unavailable");
  const pending = verifyValue(ctx.secret, readCookie(ctx.request), ctx.now);
  const state = ctx.url.searchParams.get("state") || "";
  if (!pending || pending.k !== "o" || !state || !safeEqual(pending.st, state)) return fail("state");
  if (ctx.url.searchParams.get("error")) return fail("denied");
  const code = ctx.url.searchParams.get("code");
  if (!code || code.length > 200) return fail("denied");
  let ghUser;
  try {
    const tokenRes = await ctx.fetch(GITHUB_TOKEN, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT },
      body: new URLSearchParams({
        client_id: config.id, client_secret: config.secret, code, redirect_uri: config.redirectUri, code_verifier: pending.cv,
      }).toString(),
      signal: AbortSignal.timeout(8000),
    });
    const tokenData = tokenRes.ok ? await tokenRes.json() : null;
    const accessToken = tokenData && typeof tokenData.access_token === "string" ? tokenData.access_token : null;
    if (!accessToken) return fail("github");
    const userRes = await ctx.fetch(GITHUB_USER, {
      headers: { Authorization: `Bearer ${accessToken}`, Accept: "application/vnd.github+json", "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28" },
      signal: AbortSignal.timeout(8000),
    });
    ghUser = userRes.ok ? await userRes.json() : null;
  } catch {
    return fail("github");
  }
  if (!ghUser || !Number.isSafeInteger(ghUser.id) || typeof ghUser.login !== "string" || !/^[A-Za-z0-9-]{1,39}$/.test(ghUser.login)) {
    return fail("github");
  }
  const uid = String(ghUser.id);
  try {
    const old = parseJson(await ctx.redis.one("GET", K.user(uid))) || {};
    const user = {
      id: uid, login: ghUser.login, name: typeof ghUser.name === "string" ? ghUser.name.slice(0, 100) : "",
      created: old.created || iso(ctx), lastLogin: iso(ctx),
    };
    const sid = randomToken(32);
    await ctx.redis.pipeline([
      ["SET", K.user(uid), JSON.stringify(user)],
      ["SET", K.session(hmacHex(ctx.secret, "session", sid)), uid, "EX", String(SESSION_TTL)],
    ]);
    const cookie = signValue(ctx.secret, { k: "s", sid, exp: ctx.now + SESSION_TTL * 1000 });
    return redirect(safeNext(pending.nx), { "Set-Cookie": setCookie(cookie, SESSION_TTL) });
  } catch {
    return fail("unavailable");
  }
}

async function logout(ctx) {
  assertSameSite(ctx.request);
  if (ctx.secret && ctx.redis) {
    const payload = verifyValue(ctx.secret, readCookie(ctx.request), ctx.now);
    if (payload && payload.k === "s" && typeof payload.sid === "string") {
      await ctx.redis.one("DEL", K.session(hmacHex(ctx.secret, "session", payload.sid)));
    }
  }
  return json({ signedOut: true }, 200, { "Set-Cookie": clearCookie() });
}

// ---------------------------------------------------------------- admin

async function adminQueue(ctx) {
  await requireAdmin(ctx);
  const [queue, reports, log] = await ctx.redis.pipeline([
    ["SMEMBERS", K.queue], ["LRANGE", K.reports, "0", "99"], ["LRANGE", K.audit, "0", "49"],
  ]);
  const items = [];
  for (const item of [...(queue || [])].sort()) {
    const at = item.lastIndexOf("@");
    const name = item.slice(0, at);
    const version = item.slice(at + 1);
    const record = parseJson(await ctx.redis.one("HGET", K.versions(name), version));
    if (!record) continue;
    const meta = await loadMeta(ctx, name);
    const readme = (await readmeFor(ctx, name, version)) || "";
    items.push({
      name, version, newPackage: !meta || meta.status !== "approved", owner: meta ? meta.ownerLogin : record.publisherLogin,
      publisher: record.publisherLogin, created: record.created, sha256: record.sha256, size: record.size,
      files: record.fileList || [], entry: record.entry, description: record.description, keywords: record.keywords,
      license: record.license, dependencies: record.dependencies, flags: [...new Set([...(record.flags || []), ...((meta && meta.flags) || [])])],
      readme: readme.slice(0, 20000),
    });
  }
  return json({
    queue: items,
    reports: (reports || []).map((r) => parseJson(r)).filter(Boolean),
    audit: (log || []).map((r) => parseJson(r)).filter(Boolean),
  });
}

async function review(ctx, name, version, approve) {
  const who = await requireAdmin(ctx);
  const body = await optionalJson(ctx.request);
  const versions = await loadVersions(ctx, name);
  const record = versions[version];
  if (!record) throw new ApiError(404, `${name} has no version ${version}.`);
  if (record.status !== "pending") throw new ApiError(409, `${name} ${version} is ${record.status}, not waiting for review.`);
  const reason = String(body.reason || "").trim().slice(0, 500);
  if (!approve && reason.length < 3) throw new ApiError(400, "Give the author a reason for the rejection.");
  record.status = approve ? "approved" : "rejected";
  record.reviewedBy = who.user.login;
  record.reviewedAt = iso(ctx);
  if (reason) record.reason = reason;
  const commands = [["HSET", K.versions(name), version, JSON.stringify(record)], ["SREM", K.queue, `${name}@${version}`]];
  if (!approve) commands.push(["DEL", K.claim(name, version)]);
  await ctx.redis.pipeline(commands);
  if (!approve && ctx.blob) await ctx.blob.del([blobPath(name, version)]);
  const { meta } = await refresh(ctx, name);
  await audit(ctx, who, approve ? "approve" : "reject", name, version, reason);
  return json({ name, version, status: record.status, packageStatus: meta.status });
}

async function removeVersion(ctx, name, version) {
  const who = await requireAdmin(ctx);
  const body = await optionalJson(ctx.request);
  const versions = await loadVersions(ctx, name);
  const record = versions[version];
  if (!record) throw new ApiError(404, `${name} has no version ${version}.`);
  if (record.seed) throw new ApiError(400, "Bundled versions can't be removed; yank them instead.");
  const commands = [["HDEL", K.versions(name), version], ["DEL", K.readme(name, version)], ["SREM", K.queue, `${name}@${version}`], ["HDEL", K.dl(name), version]];
  if (record.status === "rejected") commands.push(["DEL", K.claim(name, version)]);
  await ctx.redis.pipeline(commands);
  if (ctx.blob) await ctx.blob.del([blobPath(name, version)]);
  const { meta } = await refresh(ctx, name);
  await audit(ctx, who, "remove-version", name, version, String(body.reason || "").slice(0, 500));
  return json({ name, version, removed: true, packageStatus: meta.status });
}

async function removePackage(ctx, name) {
  const who = await requireAdmin(ctx);
  const body = await optionalJson(ctx.request);
  if (SEED.has(name)) throw new ApiError(400, "Bundled packages can't be removed; yank their versions instead.");
  const versions = await loadVersions(ctx, name);
  const meta = await loadMeta(ctx, name, versions);
  const owner = await ctx.redis.one("GET", K.owner(name));
  if (!meta && !owner) throw new ApiError(404, `There's no package called '${name}'.`);
  const list = Object.keys(versions);
  const commands = [
    ["DEL", K.meta(name), K.versions(name), K.owner(name), K.dl(name), ...list.map((v) => K.readme(name, v)), ...list.map((v) => K.claim(name, v))],
    ["SREM", K.names, name], ["SREM", K.listed, name], ["HDEL", K.downloads, name],
  ];
  if (list.length) commands.push(["SREM", K.queue, ...list.map((v) => `${name}@${v}`)]);
  if (owner) commands.push(["SREM", K.owned(owner), name]);
  await ctx.redis.pipeline(commands);
  if (ctx.blob) await ctx.blob.del(list.map((v) => blobPath(name, v)));
  await audit(ctx, who, "remove-package", name, null, String(body.reason || "").slice(0, 500));
  return json({ name, removed: true });
}

async function dismissReport(ctx, id) {
  const who = await requireAdmin(ctx);
  const all = (await ctx.redis.one("LRANGE", K.reports, "0", "-1")) || [];
  const raw = all.find((r) => (parseJson(r) || {}).id === id);
  if (!raw) throw new ApiError(404, "No such report.");
  await ctx.redis.one("LREM", K.reports, "1", raw);
  await audit(ctx, who, "dismiss-report", (parseJson(raw) || {}).name || "", null, "");
  return json({ dismissed: true, id });
}

// ---------------------------------------------------------------- router

async function route(ctx) {
  const m = ctx.request.method === "HEAD" ? "GET" : ctx.request.method;
  const [a, b, c, d, e] = ctx.parts;
  const n = ctx.parts.length;
  // "/api/registry/index" is redirected to "/api/registry" by the site's clean
  // URLs, so the index answers on the bare path and on "index.json" too.
  if (m === "GET" && (n === 0 || (n === 1 && (a === "index" || a === "index.json")))) return getIndex(ctx);
  if (m === "GET" && n === 1 && a === "search") return getSearch(ctx);
  if (m === "GET" && n === 1 && a === "me") return me(ctx);
  if (a === "auth" && n === 2) {
    if (m === "GET" && b === "login") return login(ctx);
    if (m === "GET" && b === "callback") return callback(ctx);
    if (m === "POST" && b === "logout") return logout(ctx);
  }
  if (a === "tokens") {
    if (n === 1 && m === "GET") return listTokens(ctx);
    if (n === 1 && m === "POST") return createToken(ctx);
    if (n === 2 && m === "DELETE") return revokeToken(ctx, b);
  }
  if (a === "account" && b === "packages" && n === 2 && m === "GET") return accountPackages(ctx);
  if (a === "packages") {
    if (n === 2 && m === "GET") return getPackage(ctx, b);
    if (n === 3 && m === "POST" && c === "report") return report(ctx, b);
    if (n === 3 && m === "GET") return getPackage(ctx, b, c);
    if (n === 3 && (m === "POST" || m === "PUT")) return publish(ctx, b, c);
    if (n === 4 && m === "GET" && d === "download") return download(ctx, b, c);
    if (n === 4 && m === "POST" && (d === "yank" || d === "unyank")) return setYank(ctx, b, c, d === "yank");
  }
  if (a === "admin") {
    if (n === 2 && b === "queue" && m === "GET") return adminQueue(ctx);
    if (b === "packages" && m === "POST") {
      if (n === 4 && d === "remove") return removePackage(ctx, c);
      if (n === 5 && (e === "approve" || e === "reject")) return review(ctx, c, d, e === "approve");
      if (n === 5 && e === "remove") return removeVersion(ctx, c, d);
    }
    if (n === 4 && b === "reports" && d === "dismiss" && m === "POST") return dismissReport(ctx, c);
  }
  return json({ error: "There's no such registry route." }, 404);
}

/** The Function's entry: every method, every /api/registry/* path. */
export async function handleRegistry(request, env = process.env, fetchImpl = fetch, now = Date.now()) {
  const ctx = makeContext(request, env, fetchImpl, now);
  try {
    return await route(ctx);
  } catch (err) {
    if (err instanceof ApiError) {
      const headers = {};
      if (err.status === 429 && err.retryAfter) headers["Retry-After"] = String(err.retryAfter);
      if (err.status === 401) headers["WWW-Authenticate"] = 'Bearer realm="TB Package Index"';
      return json({ error: err.message, ...err.extra }, err.status, headers);
    }
    console.error("registry error", err && err.stack ? err.stack : err);
    return json({ error: "Something went wrong in the package index." }, 500);
  }
}

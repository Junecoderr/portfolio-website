// Unit tests for the TB Package Index API (api/registry.js, api/_lib/registry/).
// Run: node --test tests/api/   (tests/test_site.py runs this when node is installed)
// fetch is mocked throughout: a fake Upstash Redis, a fake Vercel Blob store and
// a fake GitHub. Nothing here talks to the network.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { deflateRawSync } from "node:zlib";

import { handleRegistry, K, PUBLISH_LIMIT, searchPackages } from "../../api/_lib/registry/app.js";
import { compareVersions, editDistance, parseToml, similarNames, checkName } from "../../api/_lib/registry/rules.js";
import { SEED_PACKAGES, STDLIB_NAMES } from "../../api/_lib/registry/seed.js";
import { crc32 } from "../../api/_lib/registry/zip.js";

const SITE = "https://code.tanisha.ceo";
const SECRET = "s".repeat(40);
const BLOB_TOKEN = "vercel_blob_rw_abc123XYZ_secretpart";
const T0 = Date.parse("2026-10-27T12:00:00Z");

// ---------------------------------------------------------------- fakes

function fakeRedis() {
  const kv = new Map();
  const glob = (key) => kv.get(key);
  const hash = (key) => { if (!kv.has(key)) kv.set(key, new Map()); return kv.get(key); };
  const set = (key) => { if (!kv.has(key)) kv.set(key, new Set()); return kv.get(key); };
  const list = (key) => { if (!kv.has(key)) kv.set(key, []); return kv.get(key); };
  const run = ([cmd, ...a]) => {
    switch (cmd) {
      case "GET": { const v = glob(a[0]); return typeof v === "string" ? v : null; }
      case "SET": {
        if (a.includes("NX") && kv.has(a[0])) return null;
        kv.set(a[0], String(a[1]));
        return "OK";
      }
      case "DEL": { let n = 0; for (const k of a) n += kv.delete(k) ? 1 : 0; return n; }
      case "MGET": return a.map((k) => (typeof glob(k) === "string" ? glob(k) : null));
      case "INCR": { const v = Number(glob(a[0]) || 0) + 1; kv.set(a[0], String(v)); return v; }
      case "EXPIRE": return 1;
      case "HGET": { const h = glob(a[0]); return h instanceof Map && h.has(a[1]) ? h.get(a[1]) : null; }
      case "HSET": { const h = hash(a[0]); for (let i = 1; i < a.length; i += 2) h.set(a[i], String(a[i + 1])); return 1; }
      case "HDEL": { const h = glob(a[0]); let n = 0; if (h instanceof Map) for (const f of a.slice(1)) n += h.delete(f) ? 1 : 0; return n; }
      case "HGETALL": { const h = glob(a[0]); return h instanceof Map ? [...h.entries()].flat() : []; }
      case "HINCRBY": { const h = hash(a[0]); const v = Number(h.get(a[1]) || 0) + Number(a[2]); h.set(a[1], String(v)); return v; }
      case "SADD": { const s = set(a[0]); let n = 0; for (const m of a.slice(1)) if (!s.has(m)) { s.add(m); n++; } return n; }
      case "SREM": { const s = glob(a[0]); let n = 0; if (s instanceof Set) for (const m of a.slice(1)) n += s.delete(m) ? 1 : 0; return n; }
      case "SMEMBERS": { const s = glob(a[0]); return s instanceof Set ? [...s] : []; }
      case "LPUSH": { const l = list(a[0]); for (const v of a.slice(1)) l.unshift(String(v)); return l.length; }
      case "LTRIM": { const l = list(a[0]); const end = Number(a[2]); l.splice(end + 1); return "OK"; }
      case "LRANGE": { const l = glob(a[0]) || []; const end = Number(a[2]); return l.slice(Number(a[1]), end < 0 ? undefined : end + 1); }
      case "LREM": { const l = glob(a[0]) || []; const i = l.indexOf(a[2]); if (i >= 0) l.splice(i, 1); return i >= 0 ? 1 : 0; }
      default: throw new Error(`fake redis: unknown command ${cmd}`);
    }
  };
  return { kv, run };
}

function world({ env: extra = {}, redis = true, blob = true } = {}) {
  const r = fakeRedis();
  const blobs = new Map();
  const calls = [];
  const github = { users: {}, codes: {}, exchanged: [] };
  const env = {
    REGISTRY_SESSION_SECRET: SECRET,
    GITHUB_CLIENT_ID: "client-id",
    GITHUB_CLIENT_SECRET: "client-secret",
    REGISTRY_ADMINS: "1",
    ...(redis ? { KV_REST_API_URL: "https://kv.example", KV_REST_API_TOKEN: "kvtoken" } : {}),
    ...(blob ? { BLOB_READ_WRITE_TOKEN: BLOB_TOKEN } : {}),
    ...extra,
  };
  const fetchImpl = async (input, init = {}) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === "https://kv.example/pipeline") {
      assert.equal(init.headers.Authorization, "Bearer kvtoken");
      const commands = JSON.parse(init.body);
      for (const c of commands) for (const part of c) assert.equal(typeof part, "string", `redis args are strings: ${JSON.stringify(c)}`);
      return Response.json(commands.map((c) => ({ result: r.run(c) })));
    }
    if (url.startsWith("https://vercel.com/api/blob/?")) {
      assert.equal(init.method, "PUT");
      assert.equal(init.headers.authorization, `Bearer ${BLOB_TOKEN}`);
      assert.equal(init.headers["x-vercel-blob-access"], "private");
      assert.equal(init.headers["x-api-version"], "13");
      const pathname = new URL(url).searchParams.get("pathname");
      blobs.set(pathname, Buffer.from(init.body));
      return Response.json({ url: `https://abc123XYZ.private.blob.vercel-storage.com/${pathname}`, pathname });
    }
    if (url === "https://vercel.com/api/blob/delete") {
      for (const u of JSON.parse(init.body).urls) blobs.delete(new URL(u).pathname.slice(1));
      return Response.json({});
    }
    if (url.startsWith("https://abc123XYZ.private.blob.vercel-storage.com/")) {
      assert.equal(init.headers.authorization, `Bearer ${BLOB_TOKEN}`);
      const data = blobs.get(new URL(url).pathname.slice(1));
      return data ? new Response(data, { headers: { "content-length": String(data.length) } }) : new Response("nope", { status: 404 });
    }
    if (url === "https://github.com/login/oauth/access_token") {
      const body = new URLSearchParams(init.body);
      github.exchanged.push(Object.fromEntries(body));
      const grant = github.codes[body.get("code")];
      if (!grant || body.get("client_secret") !== "client-secret") return Response.json({ error: "bad_verification_code" });
      // PKCE: the verifier must hash to the challenge sent to /authorize.
      const challenge = createHash("sha256").update(body.get("code_verifier")).digest("base64url");
      if (challenge !== grant.challenge) return Response.json({ error: "invalid_grant" });
      return Response.json({ access_token: `gho_${grant.user}`, token_type: "bearer" });
    }
    if (url === "https://api.github.com/user") {
      const token = init.headers.Authorization.replace("Bearer gho_", "");
      const user = github.users[token];
      return user ? Response.json(user) : new Response("{}", { status: 401 });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  let now = T0;
  const call = (method, path, { headers = {}, body, cookie, token } = {}) => {
    const h = Object.fromEntries(Object.entries(headers).filter(([, v]) => v !== undefined));
    if (cookie) { h.cookie = cookie; if (method !== "GET" && !("sec-fetch-site" in headers)) { h["sec-fetch-site"] = "same-origin"; h.origin = SITE; } }
    if (token) h.authorization = `Bearer ${token}`;
    let payload = body;
    if (body !== undefined && !(body instanceof Uint8Array) && typeof body !== "string") {
      payload = JSON.stringify(body);
      h["content-type"] = h["content-type"] || "application/json";
    }
    const init = { method, headers: h };
    if (payload !== undefined && method !== "GET") init.body = payload;
    return handleRegistry(new Request(`${SITE}/api/registry/${path}`, init), env, fetchImpl, now);
  };
  return {
    env, r, blobs, calls, github, call,
    tick(ms) { now += ms; },
    get now() { return now; },
  };
}

/** Sign in through the real login + callback flow; returns the session cookie. */
async function signIn(w, user) {
  w.github.users[user.login] = user;
  const res = await w.call("GET", "auth/login?next=/packages/account");
  assert.equal(res.status, 303);
  const location = new URL(res.headers.get("location"));
  const state = location.searchParams.get("state");
  w.github.codes[`code-${user.login}`] = { user: user.login, challenge: location.searchParams.get("code_challenge") };
  const oauthCookie = res.headers.get("set-cookie").split(";")[0];
  const cb = await w.call("GET", `auth/callback?code=code-${user.login}&state=${state}`, { headers: { cookie: oauthCookie } });
  assert.equal(cb.status, 303, await cb.clone().text());
  assert.equal(cb.headers.get("location"), "/packages/account");
  return cb.headers.get("set-cookie").split(";")[0];
}

async function newToken(w, cookie) {
  const res = await w.call("POST", "tokens", { cookie, body: { name: "laptop" } });
  assert.equal(res.status, 201, await res.clone().text());
  return (await res.json()).token;
}

const ALICE = { id: 1, login: "alice", name: "Alice Admin" };
const BOB = { id: 2, login: "bob", name: "Bob" };
const CAROL = { id: 3, login: "carol", name: "" };

// ---------------------------------------------------------------- zips

function makeZip(files, { top = null } = {}) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const f of files) {
    const name = Buffer.from((top ? `${top}/` : "") + f.name, "utf8");
    const data = typeof f.data === "string" ? Buffer.from(f.data, "utf8") : Buffer.from(f.data || []);
    const method = f.method ?? 8;
    const stored = method === 8 ? deflateRawSync(data) : data;
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(f.flags || 0, 6);
    local.writeUInt16LE(method, 8); local.writeUInt32LE(crc, 14); local.writeUInt32LE(stored.length, 18);
    local.writeUInt32LE(f.fakeSize ?? data.length, 22); local.writeUInt16LE(name.length, 26);
    const cd = Buffer.alloc(46);
    cd.writeUInt32LE(0x02014b50, 0); cd.writeUInt16LE((3 << 8) | 20, 4); cd.writeUInt16LE(20, 6);
    cd.writeUInt16LE(f.flags || 0, 8); cd.writeUInt16LE(method, 10); cd.writeUInt32LE(crc, 16);
    cd.writeUInt32LE(stored.length, 20); cd.writeUInt32LE(f.fakeSize ?? data.length, 24); cd.writeUInt16LE(name.length, 28);
    cd.writeUInt32LE(((f.mode ?? 0o100644) << 16) >>> 0, 38); cd.writeUInt32LE(offset, 42);
    locals.push(local, name, stored);
    central.push(cd, name);
    offset += 30 + name.length + stored.length;
  }
  const cdBuf = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(cdBuf.length, 12); end.writeUInt32LE(offset, 16);
  return new Uint8Array(Buffer.concat([...locals, cdBuf, end]));
}

function manifest(name, version, extra = "") {
  return `[package]\nname = "${name}"\nversion = "${version}"\ndescription = "Greets people politely."\nlicense = "MIT"\nkeywords = ["greeting", "hello"]\n${extra}\n[dependencies]\ntb-colors = "^1.0"\n`;
}

function pkgZip(name, version, { extra = [], tomlExtra = "", omit = [] } = {}) {
  const files = [
    { name: "tb.toml", data: manifest(name, version, tomlExtra) },
    { name: "lib.tb", data: 'export fn hello(n) { return "Hi " + n }\n' },
    { name: "README.md", data: `# ${name}\n\nSays **hello**.\n\n<script>alert(1)</script>\n` },
    ...extra,
  ].filter((f) => !omit.includes(f.name));
  return makeZip(files, { top: name });
}

function upload(w, token, name, version, bytes, type = "application/zip") {
  return w.call("POST", `packages/${name}/${version}`, { token, body: bytes, headers: { "content-type": type } });
}

async function setup() {
  const w = world();
  const alice = await signIn(w, ALICE);
  const bob = await signIn(w, BOB);
  return { w, alice, bob, aliceToken: await newToken(w, alice), bobToken: await newToken(w, bob) };
}

// ---------------------------------------------------------------- tests

test("the bundled packages are listed and downloadable without any storage", async () => {
  const w = world({ redis: false, blob: false });
  const res = await w.call("GET", "index");
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.format, "tb-registry-1");
  assert.equal(body.degraded, true);
  assert.deepEqual(Object.keys(body.packages), ["tb-colors", "tb-progress", "tb-tables"]);
  const colors = body.packages["tb-colors"];
  assert.equal(colors.owner, "tb");
  assert.equal(colors.version, "1.0.0");
  assert.equal(colors.zip, `${SITE}/api/registry/packages/tb-colors/1.0.0/download`);
  const dl = await w.call("GET", "packages/tb-colors/1.0.0/download");
  assert.equal(dl.status, 200);
  const bytes = Buffer.from(await dl.arrayBuffer());
  assert.equal(createHash("sha256").update(bytes).digest("hex"), colors.versions["1.0.0"].sha256);
  assert.equal(dl.headers.get("x-tb-sha256"), colors.versions["1.0.0"].sha256);
  const view = await (await w.call("GET", "packages/tb-tables")).json();
  assert.equal(view.owner, "tb");
  assert.match(view.readme, /# tb-tables/);
  assert.equal(view.install, "tb add tb-tables");
  assert.equal(w.calls.length, 0);
});

test("the router also takes the route from ?route= (the vercel.json rewrite)", async () => {
  const w = world({ redis: false, blob: false });
  const res = await handleRegistry(new Request(`${SITE}/api/registry?route=packages/tb-colors`), w.env, async () => { throw new Error("no"); }, T0);
  assert.equal((await res.json()).name, "tb-colors");
  const missing = await w.call("GET", "nowhere");
  assert.equal(missing.status, 404);
});

test("sign-in uses state and PKCE, and sets one HttpOnly Secure SameSite=Lax cookie", async () => {
  const w = world();
  const res = await w.call("GET", "auth/login?next=https://evil.example/");
  const location = new URL(res.headers.get("location"));
  assert.equal(location.origin + location.pathname, "https://github.com/login/oauth/authorize");
  assert.equal(location.searchParams.get("client_id"), "client-id");
  assert.equal(location.searchParams.get("redirect_uri"), `${SITE}/api/registry/auth/callback`);
  assert.equal(location.searchParams.get("code_challenge_method"), "S256");
  assert.ok(location.searchParams.get("state").length >= 32);
  assert.equal(location.searchParams.get("scope"), null, "no scopes: public profile only");
  const cookie = res.headers.get("set-cookie");
  assert.match(cookie, /^__Host-tb_registry=v1\./);
  for (const attr of ["HttpOnly", "Secure", "SameSite=Lax", "Path=/"]) assert.ok(cookie.includes(attr), attr);

  // The callback refuses a missing cookie, a wrong state and a forged cookie.
  const state = location.searchParams.get("state");
  w.github.users.alice = ALICE;
  w.github.codes.c1 = { user: "alice", challenge: location.searchParams.get("code_challenge") };
  const jar = cookie.split(";")[0];
  for (const [query, headers] of [
    [`code=c1&state=${state}`, {}],
    [`code=c1&state=${state}x`, { cookie: jar }],
    [`code=c1&state=${state}`, { cookie: jar.replace(/.$/, (c) => (c === "A" ? "B" : "A")) }],
  ]) {
    const bad = await w.call("GET", `auth/callback?${query}`, { headers });
    assert.equal(bad.headers.get("location"), "/packages/publish?error=state");
  }
  assert.equal(w.github.exchanged.length, 0, "no code exchange without a valid state");
  // An outside `next` falls back to the publish page.
  const ok = await w.call("GET", `auth/callback?code=c1&state=${state}`, { headers: { cookie: jar } });
  assert.equal(ok.headers.get("location"), "/packages/publish");
  assert.equal(w.github.exchanged[0].code_verifier.length >= 43, true);
  const session = ok.headers.get("set-cookie").split(";")[0];
  const me = await (await w.call("GET", "me", { cookie: session })).json();
  assert.deepEqual(me.user, { id: "1", login: "alice", name: "Alice Admin" });
  assert.equal(me.admin, true);
  assert.equal(me.setup.signIn, true);
  // The GitHub access token is never stored.
  for (const value of w.r.kv.values()) assert.ok(!String(value instanceof Map ? [...value.values()] : value).includes("gho_"));
  // An expired OAuth cookie is refused.
  const later = world();
  const res2 = await later.call("GET", "auth/login");
  const st2 = new URL(res2.headers.get("location")).searchParams.get("state");
  later.tick(11 * 60 * 1000);
  const late = await later.call("GET", `auth/callback?code=x&state=${st2}`, { headers: { cookie: res2.headers.get("set-cookie").split(";")[0] } });
  assert.equal(late.headers.get("location"), "/packages/publish?error=state");
});

test("logout ends the session server-side", async () => {
  const w = world();
  const cookie = await signIn(w, BOB);
  assert.equal((await (await w.call("GET", "me", { cookie })).json()).signedIn, true);
  const cross = await w.call("POST", "auth/logout", { cookie, headers: { "sec-fetch-site": "cross-site", origin: "https://evil.example" } });
  assert.equal(cross.status, 403);
  const out = await w.call("POST", "auth/logout", { cookie });
  assert.equal(out.status, 200);
  assert.match(out.headers.get("set-cookie"), /Max-Age=0/);
  const me = await (await w.call("GET", "me", { cookie })).json();
  assert.equal(me.signedIn, false);
});

test("without GitHub settings sign-in fails with a clear message", async () => {
  const w = world({ env: { GITHUB_CLIENT_ID: "", GITHUB_CLIENT_SECRET: "" } });
  const res = await w.call("GET", "auth/login");
  assert.equal(res.headers.get("location"), "/packages/publish?error=signin-unavailable");
  const me = await (await w.call("GET", "me")).json();
  assert.equal(me.signedIn, false);
  assert.equal(me.setup.signIn, false);
  const noSecret = world({ env: { REGISTRY_SESSION_SECRET: "short" } });
  const tokens = await noSecret.call("GET", "tokens");
  assert.equal(tokens.status, 503);
  assert.match((await tokens.json()).error, /REGISTRY_SESSION_SECRET/);
});

test("API tokens are shown once, stored only as a hash, listed and revoked", async () => {
  const w = world();
  const cookie = await signIn(w, BOB);
  // Cookie-authenticated writes must come from this site.
  const cross = await w.call("POST", "tokens", { cookie, body: { name: "x" }, headers: { "sec-fetch-site": "cross-site", origin: "https://evil.example" } });
  assert.equal(cross.status, 403);
  const noOrigin = await w.call("POST", "tokens", { cookie, body: { name: "x" }, headers: { "sec-fetch-site": undefined } });
  assert.equal(noOrigin.status, 403);
  const formPost = await w.call("POST", "tokens", { cookie, body: "name=x", headers: { "content-type": "application/x-www-form-urlencoded" } });
  assert.equal(formPost.status, 415);
  const token = await newToken(w, cookie);
  assert.match(token, /^tbr_[A-Za-z0-9_-]{43}$/);
  for (const [, value] of w.r.kv) {
    const text = value instanceof Map ? JSON.stringify([...value]) : String(value instanceof Set ? [...value] : value);
    assert.ok(!text.includes(token), "the raw token is never stored");
  }
  const list = await (await w.call("GET", "tokens", { cookie })).json();
  assert.equal(list.tokens.length, 1);
  assert.deepEqual(Object.keys(list.tokens[0]).sort(), ["created", "id", "name"]);
  // A token works as a bearer token, but can't make more tokens.
  const me = await (await w.call("GET", "me", { token })).json();
  assert.equal(me.user.login, "bob");
  assert.equal(me.via, "token");
  assert.equal((await w.call("POST", "tokens", { token, body: {} })).status, 403);
  const revoke = await w.call("DELETE", `tokens/${list.tokens[0].id}`, { cookie });
  assert.equal(revoke.status, 200);
  assert.equal((await (await w.call("GET", "me", { token })).json()).signedIn, false);
  assert.equal((await upload(w, token, "greet", "1.0.0", pkgZip("greet", "1.0.0"))).status, 401);
});

test("publishing refuses bad requests and unsafe archives, storing nothing", async () => {
  const { w, bobToken } = await setup();
  const good = (name = "greet", v = "1.0.0") => pkgZip(name, v);
  const cases = [
    ["no token", () => w.call("POST", "packages/greet/1.0.0", { body: good(), headers: { "content-type": "application/zip" } }), 401, /API token/],
    ["bad token", () => upload(w, "tbr_" + "x".repeat(43), "greet", "1.0.0", good()), 401, /isn't valid/],
    ["upper-case name", () => upload(w, bobToken, "Greet", "1.0.0", good()), 400, /lower-case/],
    ["short name", () => upload(w, bobToken, "gr", "1.0.0", good()), 400, /3 to 40/],
    ["trailing dash", () => upload(w, bobToken, "greet-", "1.0.0", good()), 400, /3 to 40/],
    ["stdlib name", () => upload(w, bobToken, "json", "1.0.0", pkgZip("json", "1.0.0")), 400, /reserved/],
    ["reserved word", () => upload(w, bobToken, "stdlib", "1.0.0", pkgZip("stdlib", "1.0.0")), 400, /reserved/],
    ["bad version", () => upload(w, bobToken, "greet", "1.0", good()), 400, /semantic/],
    ["leading zero", () => upload(w, bobToken, "greet", "01.0.0", good()), 400, /semantic/],
    ["wrong type", () => upload(w, bobToken, "greet", "1.0.0", good(), "text/plain"), 415, /zip/],
    ["too big", () => upload(w, bobToken, "greet", "1.0.0", new Uint8Array(4 * 1024 * 1024 + 1)), 413, /bigger/],
    ["not a zip", () => upload(w, bobToken, "greet", "1.0.0", new TextEncoder().encode("hello")), 400, /isn't a zip/],
    ["no tb.toml", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { omit: ["tb.toml"] })), 400, /no tb.toml/],
    ["name mismatch", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("other", "1.0.0")), 400, /name is 'other'/],
    ["version mismatch", () => upload(w, bobToken, "greet", "1.0.1", good()), 400, /version is '1.0.0'/],
    ["no entry", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { omit: ["lib.tb"] })), 400, /entry file/],
    ["missing main", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { tomlExtra: 'main = "app.tb"' })), 400, /isn't in the archive/],
    ["absolute path", () => upload(w, bobToken, "greet", "1.0.0", makeZip([{ name: "/etc/x.tb", data: "x" }, { name: "tb.toml", data: manifest("greet", "1.0.0") }])), 400, /absolute/],
    ["dot-dot", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "../escape.tb", data: "x" }] })), 400, /\.\./],
    ["backslash", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "a\\b.tb", data: "x" }] })), 400, /backslash/],
    ["drive letter", () => upload(w, bobToken, "greet", "1.0.0", makeZip([{ name: "C:/x.tb", data: "x" }])), 400, /absolute/],
    ["symlink", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "link.tb", data: "/etc/passwd", mode: 0o120777 }] })), 400, /symbolic link/],
    ["hidden file", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: ".env", data: "X=1" }] })), 400, /hidden/],
    ["binary file", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "tool.exe", data: new Uint8Array([77, 90, 0, 1]) }] })), 400, /isn't allowed/],
    ["NUL in text", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "data.txt", data: "a\u0000b" }] })), 400, /binary/],
    ["not UTF-8", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "data.txt", data: new Uint8Array([0xff, 0xfe, 0x41]) }] })), 400, /UTF-8/],
    ["fake image", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "shot.png", data: "not a png" }] })), 400, /image/],
    ["duplicate", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "LIB.tb", data: "x" }] })), 400, /twice/],
    ["tb_packages", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "tb_packages/x/lib.tb", data: "x" }] })), 400, /tb_packages/],
    ["too many files", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: Array.from({ length: 200 }, (_, i) => ({ name: `f${i}.tb`, data: "x", method: 0 })) })), 400, /files/],
    ["file too big", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "big.txt", data: "a".repeat(1024 * 1024 + 1) }] })), 400, /bigger than 1 MB/],
    ["zip bomb", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "bomb.txt", data: "a".repeat(200000), fakeSize: 10 }] })), 400, /decompressed|damaged/],
    ["encrypted", () => upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0", { extra: [{ name: "x.tb", data: "x", flags: 1 }] })), 400, /Encrypted/],
    ["bad keyword", () => upload(w, bobToken, "greet", "1.0.0", makeZip([{ name: "tb.toml", data: '[package]\nname = "greet"\nversion = "1.0.0"\nkeywords = ["Bad Word"]\n' }, { name: "lib.tb", data: "x" }])), 400, /keyword/],
    ["bad toml", () => upload(w, bobToken, "greet", "1.0.0", makeZip([{ name: "tb.toml", data: '[package]\nname = """greet"""\n' }, { name: "lib.tb", data: "x" }])), 400, /tb.toml can't be read/],
  ];
  for (const [label, run, status, pattern] of cases) {
    const res = await run();
    const body = await res.json();
    assert.equal(res.status, status, `${label}: ${body.error}`);
    assert.match(body.error, pattern, label);
  }
  assert.equal(w.blobs.size, 0, "nothing reached storage");
  assert.equal(w.r.kv.get(K.names), undefined);
  assert.ok(![...w.r.kv.keys()].some((k) => k.startsWith("pkg:owner:") || k.startsWith("pkg:claim:")));
});

test("a new package waits for review and is hidden until approved", async () => {
  const { w, alice, aliceToken, bobToken } = await setup();
  const res = await upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0"));
  const body = await res.json();
  assert.equal(res.status, 201, body.error);
  assert.equal(body.status, "pending");
  assert.match(body.message, /for review/);
  const zip = pkgZip("greet", "1.0.0");
  assert.equal(body.sha256, createHash("sha256").update(zip).digest("hex"));
  assert.ok(w.blobs.has("packages/greet/1.0.0.zip"));
  // Public views don't see it.
  assert.ok(!("greet" in (await (await w.call("GET", "index")).json()).packages));
  assert.equal((await w.call("GET", "packages/greet")).status, 404);
  assert.equal((await w.call("GET", "packages/greet/1.0.0/download")).status, 404);
  assert.deepEqual((await (await w.call("GET", "search?q=greet")).json()).results, []);
  // The owner does, with its status.
  const own = await (await w.call("GET", "packages/greet", { token: bobToken })).json();
  assert.equal(own.status, "pending");
  assert.equal(own.versions[0].status, "pending");
  const account = await (await w.call("GET", "account/packages", { token: bobToken })).json();
  assert.deepEqual(account.packages.map((p) => [p.name, p.status]), [["greet", "pending"]]);
  const ownDl = await w.call("GET", "packages/greet/1.0.0/download", { token: bobToken });
  assert.equal(ownDl.status, 200);
  // Admins see the queue; others don't.
  assert.equal((await w.call("GET", "admin/queue", { token: bobToken })).status, 403);
  assert.equal((await w.call("POST", "admin/packages/greet/1.0.0/approve", { token: bobToken })).status, 403);
  const queue = await (await w.call("GET", "admin/queue", { cookie: alice })).json();
  assert.equal(queue.queue.length, 1);
  const item = queue.queue[0];
  assert.equal(item.name, "greet");
  assert.equal(item.newPackage, true);
  assert.equal(item.owner, "bob");
  assert.deepEqual(item.files, ["README.md", "lib.tb", "tb.toml"]);
  assert.match(item.readme, /<script>/, "admins see the raw README");
  // Approve: now public, searchable and downloadable (counted).
  const approve = await w.call("POST", "admin/packages/greet/1.0.0/approve", { cookie: alice });
  assert.equal(approve.status, 200, (await approve.clone().json()).error);
  const index = await (await w.call("GET", "index")).json();
  assert.equal(index.packages.greet.version, "1.0.0");
  assert.equal(index.packages.greet.owner, "bob");
  assert.deepEqual(index.packages.greet.keywords, ["greeting", "hello"]);
  assert.equal(index.packages.greet.versions["1.0.0"].sha256, body.sha256);
  const view = await (await w.call("GET", "packages/greet")).json();
  assert.equal(view.status, "approved");
  assert.equal(view.license, "MIT");
  assert.deepEqual(view.versions[0].dependencies, { "tb-colors": "^1.0" });
  const dl = await w.call("GET", "packages/greet/1.0.0/download");
  assert.equal(dl.status, 200);
  assert.equal(createHash("sha256").update(Buffer.from(await dl.arrayBuffer())).digest("hex"), body.sha256);
  await w.call("GET", "packages/greet/1.0.0/download");
  assert.equal((await (await w.call("GET", "packages/greet")).json()).downloads, 2);
  assert.equal((await (await w.call("GET", "index")).json()).packages.greet.downloads, 2);
  const hits = (await (await w.call("GET", "search?q=hello")).json()).results;
  assert.deepEqual(hits.map((p) => p.name), ["greet"]);
  // Later versions by the owner publish at once; others can't publish it.
  const v2 = await (await upload(w, bobToken, "greet", "1.1.0", pkgZip("greet", "1.1.0"))).json();
  assert.equal(v2.status, "approved");
  assert.match(v2.message, /Published greet 1.1.0/);
  assert.equal((await (await w.call("GET", "index")).json()).packages.greet.version, "1.1.0");
  const stolen = await upload(w, aliceToken, "greet", "2.0.0", pkgZip("greet", "2.0.0"));
  assert.equal(stolen.status, 403);
  const again = await upload(w, bobToken, "greet", "1.1.0", pkgZip("greet", "1.1.0"));
  assert.equal(again.status, 409);
  assert.match((await again.json()).error, /can't be replaced/);
  // A pre-release isn't "latest".
  await upload(w, bobToken, "greet", "2.0.0-beta.1", pkgZip("greet", "2.0.0-beta.1"));
  assert.equal((await (await w.call("GET", "index")).json()).packages.greet.version, "1.1.0");
});

test("names close to existing ones are flagged for the reviewer", async () => {
  const { w, alice, bobToken } = await setup();
  await upload(w, bobToken, "tb-color", "1.0.0", pkgZip("tb-color", "1.0.0"));
  await upload(w, bobToken, "jsonn", "1.0.0", pkgZip("jsonn", "1.0.0"));
  const queue = await (await w.call("GET", "admin/queue", { cookie: alice })).json();
  const flags = Object.fromEntries(queue.queue.map((i) => [i.name, i.flags.join("; ")]));
  assert.match(flags["tb-color"], /similar to existing names: tb-colors/);
  assert.match(flags["tb-color"], /tb- prefix/);
  assert.match(flags.jsonn, /json/);
});

test("a rejection reaches the owner, frees the version and deletes the archive", async () => {
  const { w, alice, bobToken } = await setup();
  await upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0"));
  const noReason = await w.call("POST", "admin/packages/greet/1.0.0/reject", { cookie: alice, body: {} });
  assert.equal(noReason.status, 400);
  const rej = await w.call("POST", "admin/packages/greet/1.0.0/reject", { cookie: alice, body: { reason: "README is empty" } });
  assert.equal(rej.status, 200);
  assert.equal((await rej.json()).packageStatus, "rejected");
  assert.ok(!w.blobs.has("packages/greet/1.0.0.zip"));
  const account = await (await w.call("GET", "account/packages", { token: bobToken })).json();
  assert.equal(account.packages[0].versions[0].status, "rejected");
  assert.equal(account.packages[0].versions[0].reason, "README is empty");
  // The owner keeps the name and can send the fixed version again.
  const again = await (await upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0"))).json();
  assert.equal(again.status, "pending");
  const audit = (await (await w.call("GET", "admin/queue", { cookie: alice })).json()).audit;
  assert.equal(audit[0].action, "reject");
  assert.equal(audit[0].reason, "README is empty");
});

test("yanked versions stay downloadable but leave the ranges; admins' yanks stick", async () => {
  const { w, alice, bobToken } = await setup();
  await upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0"));
  await w.call("POST", "admin/packages/greet/1.0.0/approve", { cookie: alice });
  await upload(w, bobToken, "greet", "1.1.0", pkgZip("greet", "1.1.0"));
  const y = await w.call("POST", "packages/greet/1.1.0/yank", { token: bobToken, body: { reason: "broken" } });
  assert.equal(y.status, 200);
  let index = await (await w.call("GET", "index")).json();
  assert.equal(index.packages.greet.version, "1.0.0");
  assert.deepEqual(index.packages.greet.versions["1.1.0"], { sha256: index.packages.greet.versions["1.1.0"].sha256, yanked: true });
  assert.equal((await w.call("GET", "packages/greet/1.1.0/download")).status, 200);
  const view = await (await w.call("GET", "packages/greet")).json();
  assert.equal(view.versions[0].yankReason, "broken");
  await w.call("POST", "packages/greet/1.1.0/unyank", { token: bobToken });
  index = await (await w.call("GET", "index")).json();
  assert.equal(index.packages.greet.version, "1.1.0");
  // Someone else can't yank; an admin can, and the owner can't undo that.
  const carol = await signIn(w, CAROL);
  assert.equal((await w.call("POST", "packages/greet/1.1.0/yank", { cookie: carol, body: {} })).status, 404);
  assert.equal((await w.call("POST", "packages/greet/1.1.0/yank", { cookie: alice, body: { reason: "malware" } })).status, 200);
  const undo = await w.call("POST", "packages/greet/1.1.0/unyank", { token: bobToken });
  assert.equal(undo.status, 403);
  // Bundled packages can be yanked too.
  assert.equal((await w.call("POST", "packages/tb-colors/1.0.0/yank", { cookie: alice, body: {} })).status, 200);
  assert.equal((await (await w.call("GET", "index")).json()).packages["tb-colors"].versions["1.0.0"].yanked, true);
});

test("publishing is rate-limited per user", async () => {
  const { w, bobToken } = await setup();
  for (let i = 0; i < PUBLISH_LIMIT; i++) {
    const res = await upload(w, bobToken, "greet", `1.0.${i}`, pkgZip("greet", `1.0.${i}`));
    assert.equal(res.status, 201, `upload ${i}`);
  }
  const over = await upload(w, bobToken, "greet", "1.0.99", pkgZip("greet", "1.0.99"));
  assert.equal(over.status, 429);
  assert.ok(Number(over.headers.get("retry-after")) > 0);
  w.tick(3600 * 1000);
  assert.equal((await upload(w, bobToken, "greet", "1.0.99", pkgZip("greet", "1.0.99"))).status, 201);
});

test("reports need sign-in and reach the admins", async () => {
  const { w, alice, bob } = await setup();
  assert.equal((await w.call("POST", "packages/tb-colors/report", { body: { reason: "spam spam" } })).status, 401);
  assert.equal((await w.call("POST", "packages/tb-colors/report", { cookie: bob, body: { reason: "x" } })).status, 400);
  assert.equal((await w.call("POST", "packages/nope-nope/report", { cookie: bob, body: { reason: "spam spam" } })).status, 404);
  const ok = await w.call("POST", "packages/tb-colors/report", { cookie: bob, body: { reason: "Looks suspicious", version: "1.0.0" } });
  assert.equal(ok.status, 201);
  const queue = await (await w.call("GET", "admin/queue", { cookie: alice })).json();
  assert.equal(queue.reports.length, 1);
  assert.equal(queue.reports[0].by, "bob");
  const dismiss = await w.call("POST", `admin/reports/${queue.reports[0].id}/dismiss`, { cookie: alice });
  assert.equal(dismiss.status, 200);
  assert.equal((await (await w.call("GET", "admin/queue", { cookie: alice })).json()).reports.length, 0);
});

test("admins can remove a version or a whole package, freeing the name", async () => {
  const { w, alice, bobToken, aliceToken } = await setup();
  await upload(w, bobToken, "greet", "1.0.0", pkgZip("greet", "1.0.0"));
  await w.call("POST", "admin/packages/greet/1.0.0/approve", { cookie: alice });
  await upload(w, bobToken, "greet", "1.1.0", pkgZip("greet", "1.1.0"));
  const rv = await w.call("POST", "admin/packages/greet/1.1.0/remove", { cookie: alice, body: { reason: "bad" } });
  assert.equal(rv.status, 200);
  assert.ok(!w.blobs.has("packages/greet/1.1.0.zip"));
  assert.equal((await (await w.call("GET", "index")).json()).packages.greet.version, "1.0.0");
  assert.equal((await upload(w, bobToken, "greet", "1.1.0", pkgZip("greet", "1.1.0"))).status, 409, "a removed version number isn't reused");
  assert.equal((await w.call("POST", "admin/packages/tb-colors/remove", { cookie: alice })).status, 400);
  const rp = await w.call("POST", "admin/packages/greet/remove", { cookie: alice, body: { reason: "abuse" } });
  assert.equal(rp.status, 200);
  assert.equal(w.blobs.size, 0);
  assert.equal((await w.call("GET", "packages/greet", { token: bobToken })).status, 404);
  const fresh = await (await upload(w, aliceToken, "greet", "1.0.0", pkgZip("greet", "1.0.0"))).json();
  assert.equal(fresh.status, "pending");
});

test("missing storage settings give clear 503s", async () => {
  const w = world({ blob: false });
  const cookie = await signIn(w, BOB);
  const token = await newToken(w, cookie);
  const res = await upload(w, token, "greet", "1.0.0", pkgZip("greet", "1.0.0"));
  assert.equal(res.status, 503);
  assert.match((await res.json()).error, /BLOB_READ_WRITE_TOKEN/);
  const nodb = world({ redis: false });
  assert.match((await (await nodb.call("GET", "tokens")).json()).error, /KV_REST_API_URL/);
});

test("search scores name, keyword and description matches", () => {
  const packages = {
    "tb-tables": { description: "Pretty tables", keywords: ["table"], downloads: 5 },
    "csv-kit": { description: "Read CSV and tables", keywords: ["csv"], downloads: 50 },
    tablet: { description: "Unrelated", keywords: [], downloads: 0 },
  };
  assert.deepEqual(searchPackages(packages, "table").map((p) => p.name), ["tb-tables", "tablet", "csv-kit"]);
  assert.deepEqual(searchPackages(packages, "csv tables").map((p) => p.name), ["csv-kit"]);
  assert.equal(searchPackages(packages, "").length, 3);
});

test("rules: names, versions, edit distance, TOML", () => {
  assert.equal(checkName("tb-colors"), null);
  assert.match(checkName("tb"), /3 to 40/);
  assert.match(checkName("a--b"), /two -/);
  for (const lib of STDLIB_NAMES) if (lib.length >= 3) assert.match(checkName(lib), /reserved/, lib);
  assert.ok(compareVersions("1.10.0", "1.9.9") > 0);
  assert.ok(compareVersions("1.0.0-beta.2", "1.0.0-beta.10") < 0);
  assert.ok(compareVersions("1.0.0-rc.1", "1.0.0") < 0);
  assert.ok(compareVersions("1.0.0-alpha", "1.0.0-alpha.1") < 0);
  assert.equal(editDistance("tb-colors", "tb-color"), 1);
  assert.equal(editDistance("kitten", "sitting", 5), 3);
  assert.deepEqual(similarNames("tbcolors", ["tb-colors", "tb-tables"]), ["tb-colors"]);
  const t = parseToml('# c\n[package]\nname = "x" # trailing\nkeywords = ["a", "b"]\nn = 3\nok = true\n[dependencies]\n"q-k" = { git = "https://h/x.git", tag = "v1" }\nesc = "a\\"b\\u00e9"\n');
  assert.deepEqual(t, { package: { name: "x", keywords: ["a", "b"], n: 3, ok: true }, dependencies: { "q-k": { git: "https://h/x.git", tag: "v1" }, esc: 'a"bé' } });
  assert.throws(() => parseToml("[package]\n__proto__ = 1\n"));
});

test("the seed matches the bundled packages", () => {
  assert.deepEqual(SEED_PACKAGES.map((p) => p.name), ["tb-colors", "tb-progress", "tb-tables"]);
  for (const p of SEED_PACKAGES) {
    const bytes = Buffer.from(p.zip, "base64");
    assert.equal(createHash("sha256").update(bytes).digest("hex"), p.sha256);
    assert.equal(bytes.length, p.size);
  }
});

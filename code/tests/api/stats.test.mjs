// Unit tests for the website's counter functions (api/hit.js, api/stats.js).
// Run: node --test tests/api/   (tests/test_site.py runs this when node is installed)
// fetch is mocked throughout: nothing here talks to Redis or GitHub.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { handleHit } from "../../api/hit.js";
import { handleStats } from "../../api/stats.js";
import {
  aggregateDownloads, isBot, normalisePath, readDownloads, redisConfig, resetDownloadsCache,
} from "../../api/_lib/stats.js";

const BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const KV = { KV_REST_API_URL: "https://kv.example/", KV_REST_API_TOKEN: "secret" };

/** A fake Upstash: keeps a total and a hash, answers /pipeline. Records requests. */
function fakeRedis() {
  const state = { visits: 0, pages: {}, calls: [] };
  const fetchImpl = async (url, init = {}) => {
    state.calls.push({ url: String(url), init });
    if (String(url).startsWith("https://api.github.com/")) return new Response("nope", { status: 503 });
    assert.equal(String(url), "https://kv.example/pipeline");
    assert.equal(init.headers.Authorization, "Bearer secret");
    const results = JSON.parse(init.body).map(([cmd, ...args]) => {
      if (cmd === "INCR") return { result: ++state.visits };
      if (cmd === "HINCRBY") {
        state.pages[args[1]] = (state.pages[args[1]] || 0) + Number(args[2]);
        return { result: state.pages[args[1]] };
      }
      if (cmd === "GET") return { result: state.visits ? String(state.visits) : null };
      return { error: "unknown command" };
    });
    return Response.json(results);
  };
  return { state, fetchImpl };
}

function hit(body, { method = "POST", ua = BROWSER, headers = {} } = {}) {
  const init = { method, headers: { "user-agent": ua, "content-type": "application/json", ...headers } };
  if (body !== undefined && method !== "GET" && method !== "HEAD") init.body = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://code.tanisha.ceo/api/hit", init);
}

const RELEASES = [
  { tag_name: "tb-v3.0.0", assets: [
    { name: "tb-macos-arm64.zip", download_count: 10 },
    { name: "tb-windows-x64-setup.exe", download_count: 25 },
  ] },
  { tag_name: "tb-v2.0.1", assets: [
    { name: "tb-macos-arm64.zip", download_count: 5 },
    { name: "tb-python.zip", download_count: 3 },
  ] },
  { tag_name: "portfolio-v1", assets: [{ name: "site.zip", download_count: 999 }] },
];

beforeEach(() => resetDownloadsCache());

test("a page view adds one to the total and to the page", async () => {
  const { state, fetchImpl } = fakeRedis();
  let res = await handleHit(hit({ path: "/download.html" }), KV, fetchImpl);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { visits: 1, counted: true });
  res = await handleHit(hit({ path: "/download" }), KV, fetchImpl);
  assert.deepEqual(await res.json(), { visits: 2, counted: true });
  await handleHit(hit({ path: "/index.html" }), KV, fetchImpl);
  assert.equal(state.visits, 3);
  assert.deepEqual(state.pages, { "/download": 2, "/": 1 });
  assert.equal(res.headers.get("cache-control"), "no-store");
  // Nothing about the visitor reaches Redis.
  for (const call of state.calls) assert.ok(!call.init.body.includes("Mozilla"));
});

test("the Upstash names work as well as the Vercel KV names", async () => {
  const { state, fetchImpl } = fakeRedis();
  const env = { UPSTASH_REDIS_REST_URL: "https://kv.example", UPSTASH_REDIS_REST_TOKEN: "secret" };
  const res = await handleHit(hit({ path: "/" }), env, fetchImpl);
  assert.deepEqual(await res.json(), { visits: 1, counted: true });
  assert.equal(state.visits, 1);
  assert.equal(redisConfig({}), null);
});

test("only POST is accepted", async () => {
  const { state, fetchImpl } = fakeRedis();
  for (const method of ["GET", "PUT", "DELETE"]) {
    const res = await handleHit(hit(method === "GET" ? undefined : { path: "/" }, { method }), KV, fetchImpl);
    assert.equal(res.status, 405, method);
    assert.equal(res.headers.get("allow"), "POST");
  }
  assert.equal(state.calls.length, 0);
});

test("bad bodies and paths are refused without counting", async () => {
  const { state, fetchImpl } = fakeRedis();
  const cases = [
    ["not json", 400],
    [JSON.stringify({}), 400],
    [JSON.stringify({ path: 42 }), 400],
    [JSON.stringify({ path: "https://evil.example/" }), 400],
    [JSON.stringify({ path: "//evil.example/x" }), 400],
    [JSON.stringify({ path: "/a/../b" }), 400],
    [JSON.stringify({ path: "/x?y=1" }), 400],
    [JSON.stringify({ path: "/" + "a".repeat(300) }), 400],
    [JSON.stringify({ path: "/", pad: "x".repeat(2000) }), 413],
  ];
  for (const [body, status] of cases) {
    const res = await handleHit(hit(body), KV, fetchImpl);
    assert.equal(res.status, status, body.slice(0, 60));
  }
  assert.equal(state.calls.length, 0);
});

test("hits posted from another site are refused", async () => {
  const { state, fetchImpl } = fakeRedis();
  let res = await handleHit(hit({ path: "/" }, { headers: { "sec-fetch-site": "cross-site" } }), KV, fetchImpl);
  assert.equal(res.status, 403);
  res = await handleHit(hit({ path: "/" }, { headers: { origin: "https://evil.example" } }), KV, fetchImpl);
  assert.equal(res.status, 403);
  res = await handleHit(hit({ path: "/" }, { headers: { origin: "https://code.tanisha.ceo", "sec-fetch-site": "same-origin" } }), KV, fetchImpl);
  assert.equal(res.status, 200);
  assert.equal(state.visits, 1);
});

test("bots are ignored and their user agent is not stored", async () => {
  const { state, fetchImpl } = fakeRedis();
  for (const ua of ["Googlebot/2.1 (+http://www.google.com/bot.html)", "curl/8.4.0", "Mozilla/5.0 HeadlessChrome/120", ""]) {
    const res = await handleHit(hit({ path: "/" }, { ua }), KV, fetchImpl);
    assert.equal(res.status, 202, ua);
    assert.deepEqual(await res.json(), { visits: null, counted: false });
  }
  assert.equal(state.calls.length, 0);
  assert.ok(!isBot(BROWSER));
});

test("without Redis settings the counter answers null, not an error", async () => {
  const calls = [];
  const fetchImpl = async (url) => { calls.push(url); return new Response("", { status: 500 }); };
  const res = await handleHit(hit({ path: "/" }), {}, fetchImpl);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { visits: null, counted: false });
  assert.equal(calls.length, 0);
});

test("a failing Redis gives null visits, not a 500", async () => {
  const fetchImpl = async () => { throw new Error("network down"); };
  const res = await handleHit(hit({ path: "/" }), KV, fetchImpl);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { visits: null, counted: false });
});

test("paths are normalised", () => {
  assert.equal(normalisePath("/"), "/");
  assert.equal(normalisePath("/index.html"), "/");
  assert.equal(normalisePath("/download.html"), "/download");
  assert.equal(normalisePath("/lib/ioc.html"), "/lib/ioc");
  assert.equal(normalisePath("/docs/"), "/docs");
  assert.equal(normalisePath(""), null);
  assert.equal(normalisePath("download"), null);
  assert.equal(normalisePath("/<script>"), null);
});

test("downloads are summed per asset across tb-v* releases", () => {
  assert.deepEqual(aggregateDownloads(RELEASES), {
    total: 43,
    assets: { "tb-macos-arm64.zip": 15, "tb-python.zip": 3, "tb-windows-x64-setup.exe": 25 },
  });
  assert.equal(aggregateDownloads({ message: "rate limited" }), null);
});

test("stats combine visits and downloads, with CDN caching", async () => {
  const { state, fetchImpl: redis } = fakeRedis();
  state.visits = 51200;
  const seen = [];
  const fetchImpl = async (url, init = {}) => {
    if (String(url).startsWith("https://api.github.com/")) {
      seen.push(init.headers);
      return Response.json(RELEASES);
    }
    return redis(url, init);
  };
  const res = await handleStats(new Request("https://code.tanisha.ceo/api/stats"), { ...KV, GITHUB_TOKEN: "gh" }, fetchImpl);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "public, s-maxage=30, stale-while-revalidate=300");
  const body = await res.json();
  assert.equal(body.visits, 51200);
  assert.equal(body.downloads.total, 43);
  assert.equal(body.downloads.assets["tb-windows-x64-setup.exe"], 25);
  assert.ok(!Number.isNaN(Date.parse(body.updated)));
  assert.equal(seen[0].Authorization, "Bearer gh");
  assert.ok(seen[0]["User-Agent"]);
});

test("missing settings and a failing GitHub give nulls, never a 500", async () => {
  const fetchImpl = async () => new Response("rate limited", { status: 403 });
  const res = await handleStats(new Request("https://code.tanisha.ceo/api/stats"), {}, fetchImpl);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.visits, null);
  assert.equal(body.downloads, null);
  const thrower = async () => { throw new Error("offline"); };
  const res2 = await handleStats(new Request("https://code.tanisha.ceo/api/stats"), KV, thrower);
  assert.equal(res2.status, 200);
  assert.deepEqual([(await res2.json()).visits, null], [null, null]);
});

test("download counts are cached for ten minutes", async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return Response.json(RELEASES); };
  const t0 = 1_000_000;
  assert.equal((await readDownloads({}, fetchImpl, t0)).total, 43);
  assert.equal((await readDownloads({}, fetchImpl, t0 + 9 * 60 * 1000)).total, 43);
  assert.equal(calls, 1);
  await readDownloads({}, fetchImpl, t0 + 11 * 60 * 1000);
  assert.equal(calls, 2);
  // After a good answer, a GitHub failure serves the last numbers.
  const failing = async () => new Response("", { status: 500 });
  assert.equal((await readDownloads({}, failing, t0 + 30 * 60 * 1000)).total, 43);
});

test("stats only answer GET", async () => {
  const res = await handleStats(new Request("https://code.tanisha.ceo/api/stats", { method: "POST", body: "{}" }), {}, fetch);
  assert.equal(res.status, 405);
});

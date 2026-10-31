// Node tests for site/assets/js/tb-pyodide.js with a fake Worker: Stop is per
// run (stopping a queued run leaves the running one alone), and a long ask()
// answer is cut between characters. Run: node --test tests/browser/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../site/assets/js/tb-pyodide.js", import.meta.url), "utf8");

function load({ shared = false } = {}) {
  const workers = [];
  class FakeWorker {
    constructor() { this.posted = []; this.terminated = false; workers.push(this); }
    postMessage(m) {
      this.posted.push(m);
      if (m.type === "init") setTimeout(() => this.onmessage({ data: { type: "ready", engine: "vm" } }));
    }
    terminate() { this.terminated = true; }
    reply(data) { this.onmessage({ data }); }
  }
  const ctx = {
    document: { currentScript: { src: "http://site/assets/js/tb-pyodide.js" } },
    location: { href: "http://site/" },
    Worker: FakeWorker, URL, TextEncoder, Promise, setTimeout, clearTimeout, Atomics, Int32Array, Uint8Array,
    SharedArrayBuffer: shared ? SharedArrayBuffer : undefined,
    requestAnimationFrame: (f) => setTimeout(f),
  };
  ctx.self = ctx;
  ctx.self.crossOriginIsolated = shared;
  ctx.window = ctx;
  vm.runInNewContext(SOURCE, ctx);
  return { TB: ctx.TB, workers };
}

const tick = () => new Promise((r) => setTimeout(r, 5));
const runsSent = (w) => w.posted.filter((m) => m.type === "run");

test("stopping a queued run leaves the running one alone", async () => {
  const { TB, workers } = load();
  const a = TB.run("say(1)"), b = TB.run("say(2)");
  assert.notEqual(a.id, b.id);
  await tick(); await tick();
  const w = workers[0];
  assert.deepEqual(runsSent(w).map((m) => m.id), [a.id]);
  TB.stop(b.id);
  assert.equal(JSON.stringify(await b), JSON.stringify({ ok: false, error: "You stopped the program.", stopped: true }));
  await tick();
  assert.equal(w.terminated, false);  // A was not stopped
  assert.equal(TB.isRunning(), true);
  w.reply({ type: "done", id: a.id });
  assert.equal((await a).ok, true);
  await tick();
  assert.deepEqual(runsSent(w).map((m) => m.id), [a.id]);  // B never started
});

test("stop(id) stops only that run; stop() still stops whatever runs", async () => {
  const { TB, workers } = load();
  const a = TB.run("while true {}");
  await tick(); await tick();
  TB.stop(a.id + 100);  // an unknown or finished run: nothing happens
  await tick();
  assert.equal(workers[0].terminated, false);
  TB.stop(a.id);
  assert.equal((await a).stopped, true);
  assert.equal(workers[0].terminated, true);  // no shared memory: the worker is replaced
  const c = TB.run("while true {}");
  await tick(); await tick();
  TB.stop();
  assert.equal((await c).stopped, true);
});

test("a long answer is cut between characters", async () => {
  const { TB, workers } = load({ shared: true });
  const answer = "a" + "€".repeat(30000);  // 1 + 90,000 bytes; the buffer holds 65,536
  const a = TB.run("ask()", { onAsk: () => answer });
  await tick(); await tick();
  const w = workers[0];
  const control = w.posted[0].control;
  w.reply({ type: "ask", prompt: "?" });
  await tick();
  const flags = new Int32Array(control, 0, 3);
  const n = Atomics.load(flags, 2);
  const bytes = new Uint8Array(control, 12, n);
  assert.equal(n, 1 + 3 * Math.floor(65535 / 3));
  assert.equal(new TextDecoder("utf-8", { fatal: true }).decode(bytes.slice()), answer.slice(0, 1 + Math.floor(65535 / 3)));
  w.reply({ type: "done", id: a.id });
  await a;
});

// Drives the built site in headless Chrome over the DevTools protocol (no
// npm packages) and checks the playground worker: the page stays responsive
// during a long program, output streams before the end, Stop ends an endless
// loop quickly, ask() reads an answer typed into the page, and the Run
// buttons on docs.html still work.
//
//   node tests/browser/playground.mjs http://127.0.0.1:8000/ [chrome-path]
//
// Serve dist/site with scripts/serve_site.py (it sends vercel.json's
// COOP/COEP headers). Pass --fallback with a server that does not send them
// (python -m http.server) to check the no-SharedArrayBuffer path instead.
// Prints one JSON line per check; exits non-zero if any check fails.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const fallback = args.includes("--fallback");
const [base, chromeArg] = args.filter((a) => !a.startsWith("--"));
const CHROMES = [
  chromeArg, process.env.CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
].filter(Boolean);
const chrome = CHROMES.find((p) => existsSync(p));
if (!base || !chrome) {
  console.error("usage: node tests/browser/playground.mjs <site-url> [chrome-path]");
  process.exit(2);
}

const profile = mkdtempSync(join(tmpdir(), "tb-cdp-"));
const proc = spawn(chrome, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function devtoolsPort() {
  const file = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 100; i++) {
    if (existsSync(file)) { const [port] = readFileSync(file, "utf8").split("\n"); if (port) return port; }
    await sleep(100);
  }
  throw new Error("Chrome did not start");
}

let ws, nextId = 1;
const pending = new Map(), listeners = [];
function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 500));
  return r.result.value;
}
async function open(url) {
  await send("Page.navigate", { url });
  await sleep(300);
  await evaluate(`new Promise(r => document.readyState === "complete" ? r() : addEventListener("load", () => r()))`);
}

const results = [];
function check(name, ok, evidence) {
  results.push(ok);
  console.log(JSON.stringify({ check: name, pass: !!ok, ...evidence }));
}

// Runs code in the playground and collects timings in the page itself.
const RUN = (code, extra = "") => `(async () => {
  const code = document.getElementById("pg-code"), out = document.getElementById("pg-output");
  const run = document.getElementById("pg-run"), stop = document.getElementById("pg-stop");
  code.value = ${JSON.stringify(code)};
  code.dispatchEvent(new Event("input"));
  let frames = 0, maxGap = 0, last = performance.now(), live = true;
  (function tick(t) { if (!live) return; frames++; maxGap = Math.max(maxGap, t - last); last = t; requestAnimationFrame(tick); })(last);
  let clicks = 0; const btn = document.body.appendChild(document.createElement("button"));
  const onClick = () => clicks++; btn.addEventListener("click", onClick);
  const t0 = performance.now(); run.click();
  const r = { firstOutputMs: null, frames: 0, maxFrameGapMs: 0, clicksHandled: 0 };
  ${extra}
  while (run.disabled) {
    if (r.firstOutputMs == null && out.textContent.trim()) r.firstOutputMs = Math.round(performance.now() - t0);
    await new Promise(res => setTimeout(res, 20));
  }
  r.totalMs = Math.round(performance.now() - t0);
  live = false; btn.removeEventListener("click", onClick);
  r.frames = frames; r.maxFrameGapMs = Math.round(maxGap); r.clicksHandled = clicks;
  r.output = out.textContent.slice(-300);
  return r;
})()`;

try {
  const port = await devtoolsPort();
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    } else if (m.method) listeners.forEach((f) => f(m));
  });
  await send("Page.enable");
  await send("Runtime.enable");
  // In --fallback mode ask() uses the browser's prompt box: answer it.
  listeners.push((m) => {
    if (m.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", { accept: true, promptText: "Ada" });
  });

  await open(new URL("playground.html", base).href);
  const boot = await evaluate(`(async () => {
    const t0 = performance.now();
    while (!window.TB.isLoaded()) { if (performance.now() - t0 > 90000) break; await new Promise(r => setTimeout(r, 100)); }
    // Let the warm-up status clear before driving the page.
    await new Promise(r => setTimeout(r, 300));
    return { loaded: window.TB.isLoaded(), isolated: self.crossOriginIsolated, loadMs: Math.round(performance.now() - t0) };
  })()`);
  check("worker loads", boot.loaded && boot.isolated === !fallback, boot);

  // (1) + (2): a long loop that prints now and then.
  const LONG = "let i = 0\nwhile i < 1500000 {\n  if i % 150000 == 0 { say(i) }\n  i = i + 1\n}\nsay(\"end\")\n";
  const long = await evaluate(RUN(LONG, `
    // Click a page button every 100 ms while the program runs.
    const clicker = setInterval(() => btn.click(), 100);
    setTimeout(() => clearInterval(clicker), 60000);
    const waitEnd = setInterval(() => { if (!run.disabled) clearInterval(clicker); }, 50);`));
  check("page stays responsive", long.totalMs > 1000 && long.maxFrameGapMs < 250 && long.clicksHandled >= long.totalMs / 200,
        { totalMs: long.totalMs, frames: long.frames, maxFrameGapMs: long.maxFrameGapMs, clicksHandled: long.clicksHandled });
  check("output streams before the end", long.firstOutputMs != null && long.firstOutputMs < long.totalMs - 500 && /end/.test(long.output),
        { firstOutputMs: long.firstOutputMs, totalMs: long.totalMs });

  // (3) Stop ends `while true { }` (and a printing one) quickly.
  for (const [label, code] of [["silent", "while true { }\n"], ["printing", "let n = 0\nwhile true {\n  n = n + 1\n  say(n)\n}\n"]]) {
    const stop = await evaluate(RUN(code, `
      // Let it really run (after a restart TB loads again first).
      while (!window.TB.isLoaded()) await new Promise(res => setTimeout(res, 50));
      await new Promise(res => setTimeout(res, 1500));
      const tStop = performance.now(); stop.click();
      while (run.disabled) await new Promise(res => setTimeout(res, 10));
      r.stopMs = Math.round(performance.now() - tStop);`));
    check(`Stop ends a ${label} endless loop`, stop.stopMs < 1100 && /You stopped the program/.test(stop.output),
          { stopMs: stop.stopMs, tail: stop.output.slice(-60) });
  }
  // The engine still works after a Stop.
  const after = await evaluate(RUN('say("still here")\n'));
  check("runs again after Stop", /still here/.test(after.output), { tail: after.output.slice(-40) });

  // (4) ask() reads an answer typed into the page.
  const ASK = 'let name = ask("Name? ")\nsay("Hi " + name + "!")\n';
  const asked = await evaluate(RUN(ASK, fallback ? "" : `
    let box = null;
    for (let i = 0; i < 200 && !box; i++) { box = out.querySelector("input.out-input"); if (!box) await new Promise(res => setTimeout(res, 25)); }
    r.inputShown = !!box;
    if (box) { box.value = "Ada"; box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); }`));
  check("ask() reads the answer", /Hi Ada!/.test(asked.output) && (fallback || asked.inputShown),
        { inputShown: asked.inputShown, tail: asked.output.slice(-60) });

  // (5) The Run buttons on docs.html.
  await open(new URL("docs.html", base).href);
  const docs = await evaluate(`(async () => {
    const btn = document.querySelector(".runner .button-run");
    const wrap = btn.closest(".runner"), out = wrap.querySelector(".output"), status = wrap.querySelector(".runner-status");
    const t0 = performance.now(); btn.click();
    while (!/Done|mistake|Stopped/.test(status.textContent) && performance.now() - t0 < 90000) await new Promise(r => setTimeout(r, 50));
    const first = { status: status.textContent, output: out.textContent.slice(0, 120), ms: Math.round(performance.now() - t0) };
    // A docs block that asks a question.
    const askBtn = document.querySelector("#ask-care .runner .button-run");
    const aw = askBtn.closest(".runner"), aout = aw.querySelector(".output"), ast = aw.querySelector(".runner-status");
    askBtn.click();
    let box = null;
    for (let i = 0; i < 400 && !box; i++) { box = aout.querySelector("input.out-input"); if (!box && !/Done|mistake/.test(ast.textContent)) await new Promise(r => setTimeout(r, 25)); else if (!box) break; }
    if (box) { box.value = "Ada"; box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); }
    while (!/Done|mistake|Stopped/.test(ast.textContent)) await new Promise(r => setTimeout(r, 50));
    return { first, askStatus: ast.textContent, askOutput: aout.textContent.slice(0, 160), inputShown: !!box,
      crossOrigin: performance.getEntriesByType("resource").map(e => e.name).filter(n => !n.startsWith(location.origin)) };
  })()`);
  check("docs Run buttons work", docs.first.status === "Done." && docs.first.output.trim() && docs.askStatus === "Done." && /Ada/.test(docs.askOutput) && docs.crossOrigin.length === 0,
        docs);

  // (6) Stop is per run: stopping queued run B leaves running run A alone.
  const perRun = await evaluate(`(async () => {
    const a = window.TB.run("while true { }"), b = window.TB.run('say("b")');
    await new Promise(r => setTimeout(r, 300));
    window.TB.stop(b.id);
    const bResult = await b;
    await new Promise(r => setTimeout(r, 500));
    const aStillRunning = window.TB.isRunning();
    window.TB.stop(a.id);
    const aResult = await a;
    return { bStopped: bResult.stopped, aStillRunning, aStopped: aResult.stopped };
  })()`);
  check("Stop on a queued run leaves the running one alone", perRun.bStopped && perRun.aStillRunning && perRun.aStopped, perRun);
} catch (e) {
  check("driver", false, { error: String(e && e.stack || e) });
} finally {
  try { ws && ws.close(); } catch {}
  proc.kill();
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
process.exit(results.every(Boolean) ? 0 : 1);

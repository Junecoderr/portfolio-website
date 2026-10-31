// Runs TB programs in the browser, in a Web Worker (tb-worker.js) so the page
// stays usable while a program runs.
//
//   TB.load()  -> Promise that resolves once Pyodide and tb.py are ready
//   TB.run(source, { onOutput(line), onAsk(prompt, recent) -> string | Promise })
//              -> Promise<{ ok, error, stopped }>
//              Output arrives while the program runs. recent = lines printed
//              since the last question.
//   TB.stop()  stops the running program at once
//   TB.askInline(outputEl, prompt, recent) -> an onAsk that types the answer
//              into the output box, or (when the browser can't) a prompt box
//
// Inline answers and an instant Stop need a SharedArrayBuffer, which browsers
// only give cross-origin isolated pages (the COOP/COEP headers in
// vercel.json). Without one, Stop restarts the worker, and a program that
// calls ask() is run again on the page's own thread with prompt boxes.
//
// Everything (Pyodide in vendor/pyodide/, tb.py, tbcore/, tblib/) comes from
// this same site; nothing is fetched from anywhere else.
(function () {
  "use strict";

  var here = (document.currentScript && document.currentScript.src) || location.href;
  var SITE_ROOT = new URL("../../", here).href;
  var WORKER_URL = new URL("tb-worker.js", here).href;
  var MAIN_TIME_LIMIT = 10;  // seconds, only on the page's own thread
  var STOP_GRACE_MS = 1000;  // then the worker is replaced

  var SHARED = typeof SharedArrayBuffer === "function" && self.crossOriginIsolated === true;

  var worker = null, control = null, flags = null, answerBytes = null;
  var loading = null, engineName = "vm", ready = false;
  var job = null;   // the running program: { id, opts, source, resolve, recent }
  var nextId = 1;
  var queue = Promise.resolve();

  function fail(text) { return { ok: false, error: text, stopped: false }; }

  function startWorker() {
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      if (typeof Worker !== "function") return reject(new Error("This browser can't run Web Workers."));
      worker = new Worker(WORKER_URL);
      if (SHARED) {
        control = new SharedArrayBuffer(12 + 65536);
        flags = new Int32Array(control, 0, 3);
        answerBytes = new Uint8Array(control, 12);
      }
      worker.onmessage = function (e) {
        var m = e.data || {};
        if (m.type === "ready") { engineName = m.engine; ready = true; resolve(); }
        else if (m.type === "failed") { reject(new Error(m.error)); }
        else if (m.type === "out") { if (job) deliver(m.lines); }
        else if (m.type === "ask") { if (job) answer(m.prompt); }
        else if (m.type === "done") { if (job && job.id === m.id) finish(m); }
      };
      worker.onerror = function (e) {
        e.preventDefault();
        reject(new Error(e.message || "The TB worker could not start."));
        if (job) finish({ error: "Oops! TB stopped unexpectedly: " + (e.message || "worker error") });
      };
      worker.postMessage({ type: "init", root: SITE_ROOT, control: control });
    });
    loading.catch(function () { killWorker(); });
    return loading;
  }

  function killWorker() {
    if (worker) worker.terminate();
    worker = null; loading = null; ready = false;
  }

  // Output is painted at most once per frame.
  // (tb-worker.js sends at most 10,000 lines per run.)
  var pendingLines = [], painting = false;
  function deliver(lines) {
    for (var i = 0; i < lines.length; i++) pendingLines.push(lines[i]);
    job.recent = job.recent.concat(lines).slice(-50);
    if (!painting) { painting = true; requestAnimationFrame(paint); }
  }
  function paint() {
    painting = false;
    var lines = pendingLines, out = job ? job.opts.onOutput : null;
    pendingLines = [];
    if (typeof out === "function") lines.forEach(function (l) { out(l); });
  }

  function answer(prompt) {
    paint();
    var current = job, shown = job.recent;
    job.recent = [];
    var onAsk = typeof job.opts.onAsk === "function" ? job.opts.onAsk : defaultAsk;
    Promise.resolve(onAsk(prompt, shown)).then(function (text) {
      if (job !== current || !flags) return;
      var bytes = new TextEncoder().encode(text == null ? "" : String(text));
      var n = Math.min(bytes.length, answerBytes.length);
      answerBytes.set(bytes.subarray(0, n));
      Atomics.store(flags, 2, n);
      Atomics.store(flags, 1, 1);
      Atomics.notify(flags, 1);
    });
  }

  function finish(m) {
    var j = job;
    paint();
    job = null;
    clearTimeout(j.stopTimer);
    if (m.needsInput) return j.resolve(runOnPage(j));
    if (m.stopped) return j.resolve({ ok: false, error: "You stopped the program.", stopped: true });
    j.resolve(m.error == null ? { ok: true, error: null, stopped: false } : fail(String(m.error)));
  }

  function load() {
    return startWorker();
  }

  function run(source, opts) {
    opts = opts || {};
    var p = queue.then(function () {
      return new Promise(function (resolve) {
        // The job exists while TB is still loading, so Stop works then too.
        var j = job = { id: nextId++, opts: opts, source: String(source), resolve: resolve, recent: [], sent: false };
        startWorker().then(function () {
          if (job !== j) return;
          j.sent = true;
          if (flags) { Atomics.store(flags, 0, 0); Atomics.store(flags, 1, 0); }
          worker.postMessage({ type: "run", id: j.id, source: j.source });
        }, function (e) {
          if (job === j) finish({ error: "TB could not start in this browser: " + (e && e.message ? e.message : e) });
        });
      });
    });
    queue = p.catch(function () {});
    return p;
  }

  function stop() {
    if (!job) return;
    var j = job;
    if (!j.sent) return finish({ stopped: true });  // still loading: never start it
    if (flags) {
      Atomics.store(flags, 0, 2);  // SIGINT: Python raises KeyboardInterrupt
      Atomics.notify(flags, 1);
    }
    // No shared memory, or stuck in a long call that never checks: start over.
    j.stopTimer = setTimeout(function () {
      if (job !== j) return;
      killWorker();
      finish({ stopped: true });
    }, flags ? STOP_GRACE_MS : 0);
  }

  // ----------------------------------------------- the page's own thread
  // Only for a program that asks questions when there is no shared memory.
  var pageEngine = null;
  function loadPageEngine() {
    if (pageEngine) return pageEngine;
    pageEngine = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = WORKER_URL;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("Could not load " + WORKER_URL)); };
      document.head.appendChild(s);
    }).then(function () { return self.TBEngine.load(SITE_ROOT); });
    pageEngine.catch(function () { pageEngine = null; });
    return pageEngine;
  }

  function runOnPage(j) {
    var out = typeof j.opts.onOutput === "function" ? j.opts.onOutput : function () {};
    var recent = [];
    out("(This browser can't take answers inside the page, so TB is starting the program again with pop-up boxes.)");
    return loadPageEngine().then(function () {
      // Give the browser a frame to paint before the page is busy.
      return new Promise(function (r) { setTimeout(r, 16); });
    }).then(function () {
      return self.TBEngine.run(j.source, function (lines) {
        lines.forEach(function (l) { recent.push(l); if (recent.length > 50) recent.shift(); out(l); });
      }, function (prompt) {
        var shown = recent;
        recent = [];
        var onAsk = typeof j.opts.onAsk === "function" ? j.opts.onAsk : defaultAsk;
        var a = onAsk(prompt, shown);
        if (a && typeof a.then === "function") a = defaultAsk(prompt, shown);
        return a == null ? "" : String(a);
      }, MAIN_TIME_LIMIT);
    }).then(function (err) {
      return err == null ? { ok: true, error: null, stopped: false } : fail(err);
    }, function (e) {
      return fail("TB could not start in this browser: " + (e && e.message ? e.message : e));
    });
  }

  // ----------------------------------------------- answering ask()
  // Text for window.prompt: the last few printed lines, then the question.
  function promptText(prompt, recent) {
    var lines = (recent || []).slice(-12);
    while (lines.length && !lines[0].trim()) lines.shift();
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    var question = prompt && prompt.trim() ? prompt : "TB is asking you something:";
    return lines.length ? lines.join("\n") + "\n\n" + question : question;
  }

  function defaultAsk(prompt, recent) {
    var answer = window.prompt(promptText(prompt, recent), "");
    return answer == null ? "" : answer;
  }

  function span(cls, text) {
    var s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    return s;
  }

  // Shows the question in outputEl with a text box after it; resolves with
  // what was typed when Enter is pressed. On the page's own thread (job is
  // null there) it falls back to a prompt box.
  function askInline(outputEl, prompt, recent) {
    if (!SHARED || !job) {
      var a = defaultAsk(prompt, recent);
      outputEl.appendChild(span("out-ask", prompt));
      outputEl.appendChild(span("out-answer", a));
      outputEl.appendChild(document.createTextNode("\n"));
      return a;
    }
    var current = job;
    return new Promise(function (resolve) {
      outputEl.appendChild(span("out-ask", prompt));
      var input = document.createElement("input");
      input.type = "text";
      input.className = "out-input";
      input.setAttribute("aria-label", prompt && prompt.trim() ? prompt.trim() : "Your answer");
      input.autocomplete = "off";
      input.spellcheck = false;
      outputEl.appendChild(input);
      var nl = document.createTextNode("\n");
      outputEl.appendChild(nl);
      input.focus({ preventScroll: true });
      outputEl.scrollTop = outputEl.scrollHeight;
      var done = false;
      function finishAsk(text) {
        if (done) return;
        done = true;
        clearInterval(watch);
        outputEl.replaceChild(span("out-answer", text), input);
        resolve(text);
      }
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); finishAsk(input.value); }
      });
      // Stop (or a new run) while waiting: drop the box.
      var watch = setInterval(function () { if (job !== current) finishAsk(""); }, 200);
    });
  }

  window.TB = {
    load: load, run: run, stop: stop, promptText: promptText, ask: defaultAsk, askInline: askInline,
    canAskInline: SHARED,
    isLoaded: function () { return ready; },
    isRunning: function () { return !!job; },
    engine: function () { return engineName; }
  };
})();

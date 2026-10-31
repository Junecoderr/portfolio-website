// The TB engine: Pyodide (Python compiled to WebAssembly) plus tb.py, tbcore/
// and tblib/ from this same site. tb-pyodide.js starts this file as a Web
// Worker, so programs run off the page's main thread: the page stays usable,
// output streams in while the program runs and Stop works at once.
//
// Messages in:  {type: "init", root, control?: SharedArrayBuffer}
//               {type: "run", id, source}
// Messages out: {type: "ready", engine} | {type: "failed", error}
//               {type: "out", lines} | {type: "ask", prompt}
//               {type: "done", id, error, stopped, needsInput}
//
// With a SharedArrayBuffer (the page is cross-origin isolated) ask() waits
// for the answer typed into the page and Stop uses Pyodide's interrupt
// buffer. Without one, Stop terminates the worker, and a program that calls
// ask() ends with needsInput so the page can run it again on its own thread,
// where the browser's prompt box works.
//
// The page can also load this file as a plain <script>: then it only
// defines self.TBEngine (that main-thread fallback), with a time limit
// because nothing can press Stop while the page is busy.
(function () {
  "use strict";

  var PY_HELPER = [
    "import os, sys, time",
    "os.environ['TB_ENGINE'] = 'vm'",
    "import tb",
    "",
    "class _TBTimeout(BaseException):",
    "    pass",
    "",
    "class _TBNeedsInput(BaseException):",
    "    pass",
    "",
    "# Keep the VM's fast (untraced) bytecode even though a trace hook is set:",
    "# its loop back-edges and function calls still call the hook.",
    "def _tb_fast_hook():",
    "    try:",
    "        import tbcore.vm as _vm, tbcore.compiler as _c",
    "        orig_program, orig_code = _c.compile_program, _c.FnTemplate.code",
    "        _vm.compile_program = lambda body, file=None, traced=False: orig_program(body, file, False)",
    "        _c.FnTemplate.code = lambda self, traced: orig_code(self, False)",
    "        return True",
    "    except Exception:",
    "        return False  # then traced code: slower, still stops loops with a body",
    "",
    "_tb_fast_hook()",
    "",
    "def _tb_engine():",
    "    cls = getattr(tb, 'VMInterpreter', None) if os.environ.get('TB_ENGINE') != 'tree' else None",
    "    return cls or getattr(tb, 'TreeInterpreter', None) or tb.Interpreter",
    "",
    "def _tb_engine_name():",
    "    return 'vm' if _tb_engine() is getattr(tb, 'VMInterpreter', object()) else 'tree'",
    "",
    "# on_output gets lists of lines, at most every 50 ms, so a loop that prints",
    "# fast doesn't flood the page. on_ask returns the answer, or None when the",
    "# answer can't be had here. time_limit 0 means no limit (Stop instead).",
    "def _tb_web_run(source, on_output, on_ask, time_limit):",
    "    deadline = [time.monotonic() + time_limit]",
    "    pending = []",
    "    last = [time.monotonic()]",
    "    ticks = [0]",
    "    def flush():",
    "        if pending:",
    "            lines = pending[:]",
    "            del pending[:]",
    "            on_output(lines)",
    "        last[0] = time.monotonic()",
    "    def hook(event, line, file, env):",
    "        ticks[0] += 1",
    "        if ticks[0] % 512 == 0:",
    "            now = time.monotonic()",
    "            if pending and now - last[0] > 0.05:",
    "                flush()",
    "            if time_limit and now > deadline[0]:",
    "                raise _TBTimeout()",
    "    def output(text):",
    "        pending.append(str(text))",
    "        if time.monotonic() - last[0] > 0.05:",
    "            flush()",
    "    def ask(prompt):",
    "        flush()",
    "        answer = on_ask(str(prompt))",
    "        if answer is None:",
    "            raise _TBNeedsInput()",
    "        deadline[0] = time.monotonic() + time_limit",
    "        return str(answer)",
    "    old_limit = sys.getrecursionlimit()",
    "    real_set = sys.setrecursionlimit",
    "    try:",
    "        interp = _tb_engine()(output=output, input_fn=ask)",
    "        interp.trace = hook",
    "        tb.run(source, interpreter=interp)",
    "        return None",
    "    except tb.TBError as err:",
    "        trace = err.trace_text() if hasattr(err, 'trace_text') else ''",
    "        if len(trace) > 240:",
    "            trace = trace[:240].rsplit(' ', 1)[0] + ' ...'",
    "        return str(err) + ('\\n' + trace if trace else '')",
    "    except tb.TBExit:",
    "        return None",
    "    except KeyboardInterrupt:",
    "        return 'STOPPED'",
    "    except _TBNeedsInput:",
    "        return 'NEEDS_INPUT'",
    "    except _TBTimeout:",
    "        return ('Oops! The program ran for more than ' + str(int(time_limit)) +",
    "                ' seconds, so TB stopped it. Is there a while loop that never ends?')",
    "    except RecursionError:",
    "        return 'Oops! The program went too deep (too many functions inside functions).'",
    "    except Exception as err:",
    "        return 'Oops! Something went wrong inside TB itself: ' + type(err).__name__ + ': ' + str(err)",
    "    finally:",
    "        flush()",
    "        real_set(old_limit)",
  ].join("\n");

  // Pyodide packages outside its standard library zip, vendored next to it by
  // scripts/fetch_pyodide.py and loaded the first time a program imports a TB
  // library that needs them.
  var NEEDS = {
    sqlite: ["sqlite3"],
    crypto: ["hashlib"],
    datetime: ["tzdata"],
    // These import Python's ssl module when they load (stix and siem work in
    // the browser; the others then explain that they need the TB app).
    stix: ["ssl"], siem: ["ssl"], http: ["ssl"], intel: ["ssl"], tls: ["ssl"], websocket: ["ssl"]
  };
  var loadedPackages = {};

  function packagesFor(source) {
    var wanted = [];
    var re = /^\s*(?:import\s+([a-z_]\w*)|from\s+([a-z_]\w*)\s+import)/gm;
    var m;
    while ((m = re.exec(String(source)))) {
      var lib = m[1] || m[2];
      (NEEDS[lib] || []).forEach(function (p) {
        if (!loadedPackages[p] && wanted.indexOf(p) < 0) wanted.push(p);
      });
    }
    return wanted;
  }

  var pyodide = null;
  var pyRun = null;
  var engineName = "vm";

  async function loadPackagesFor(source) {
    var wanted = packagesFor(source);
    if (!wanted.length) return;
    try {
      await pyodide.loadPackage(wanted, { messageCallback: function () {}, errorCallback: function () {} });
    } catch (e) {
      // Not vendored? The library gives its own friendly error when it runs.
    }
    wanted.forEach(function (p) { loadedPackages[p] = true; });
    // hashlib may already be imported (by the hash library) without OpenSSL;
    // reload it so scrypt and pbkdf2_hmac appear.
    if (wanted.indexOf("hashlib") >= 0) {
      try {
        pyodide.runPython("import importlib, sys\nif 'hashlib' in sys.modules: importlib.reload(sys.modules['hashlib'])");
      } catch (e) { /* keep going; crypto reports what is missing */ }
    }
  }

  function loadScript(src) {
    if (typeof importScripts === "function") { importScripts(src); return Promise.resolve(); }
    return new Promise(function (resolve, reject) {
      if (typeof self.loadPyodide === "function") return resolve();
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("Could not load " + src + ".")); };
      document.head.appendChild(s);
    });
  }

  function fetchOk(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("Could not load " + url + " (HTTP " + r.status + ").");
      return r;
    });
  }

  // root: the site root URL. Resolves to the engine's name ("vm").
  async function load(root) {
    var PYODIDE_URL = root + "vendor/pyodide/";
    try {
      await loadScript(PYODIDE_URL + "pyodide.js");
    } catch (e) {
      throw new Error("Could not load " + PYODIDE_URL + "pyodide.js. Was the site built with scripts/fetch_pyodide.py?");
    }
    var py = await self.loadPyodide({ indexURL: PYODIDE_URL, stdout: function () {}, stderr: function () {} });
    // tb.py, the standard library (tblib/) and the sample data the guides use
    // (apps/data/) go into Pyodide's in-memory file system, laid out like the
    // real project, so `import logs` and logs.read("apps/data/auth.log") work
    // just as on a laptop. build_site.py lists the files in index.json.
    var HOME = "/home/pyodide/";
    var lists = await Promise.all([
      fetchOk(root + "tbcore/index.json").then(function (r) { return r.json(); }),
      fetchOk(root + "tblib/index.json").then(function (r) { return r.json(); }),
      fetchOk(root + "apps/data/index.json").then(function (r) { return r.json(); }).catch(function () { return []; })
    ]);
    var files = ["tb.py"]
      .concat(lists[0].map(function (f) { return "tbcore/" + f; }))
      .concat(lists[1].map(function (f) { return "tblib/" + f; }))
      .concat(lists[2].map(function (f) { return "apps/data/" + f; }));
    var blobs = await Promise.all(files.map(function (f) {
      return fetchOk(root + f).then(function (r) { return r.arrayBuffer(); });
    }));
    files.forEach(function (f, i) {
      py.FS.mkdirTree((HOME + f).replace(/\/[^\/]*$/, ""));
      py.FS.writeFile(HOME + f, new Uint8Array(blobs[i]));
    });
    py.runPython("import os, sys\nos.chdir('/home/pyodide')\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
    py.runPython(PY_HELPER);
    pyRun = py.globals.get("_tb_web_run");
    engineName = String(py.runPython("_tb_engine_name()"));
    pyodide = py;
    return engineName;
  }

  // onOutput(lines), onAsk(prompt) -> string, or null when there is no way
  // to answer. Returns null when the program ends normally, else the error
  // text, or the markers "STOPPED" / "NEEDS_INPUT".
  async function run(source, onOutput, onAsk, timeLimit) {
    await loadPackagesFor(source);
    try {
      var err = pyRun(String(source), function (lines) {
        var list = lines.toJs ? lines.toJs() : lines;
        if (lines.destroy) lines.destroy();
        onOutput(list);
      }, onAsk, timeLimit || 0);
      return err == null ? null : String(err);
    } catch (e) {
      return "Oops! Something went wrong while running: " + (e && e.message ? e.message : e);
    }
  }

  self.TBEngine = {
    load: load, run: run,
    setInterruptBuffer: function (buf) { if (pyodide) pyodide.setInterruptBuffer(buf); }
  };

  // ----------------------------------------------------------- worker side
  if (typeof WorkerGlobalScope === "undefined" || !(self instanceof WorkerGlobalScope)) return;

  // control (shared with the page): Int32 [0] interrupt flag (Pyodide's
  // interrupt buffer, 2 = SIGINT), [1] answer state (0 waiting, 1 answered),
  // [2] answer length in bytes; the answer's UTF-8 bytes start at byte 12.
  var control = null, flags = null, answerBytes = null;
  var loading = null;
  var MAX_LINES = 10000;

  function ask(prompt) {
    if (!control) return null;
    Atomics.store(flags, 1, 0);
    self.postMessage({ type: "ask", prompt: prompt });
    while (Atomics.load(flags, 1) === 0) {
      if (Atomics.load(flags, 0)) return "";  // Stop pressed: the interrupt fires next
      Atomics.wait(flags, 1, 0, 100);
    }
    var n = Atomics.load(flags, 2);
    return new TextDecoder().decode(answerBytes.slice(0, n));
  }

  self.onmessage = function (e) {
    var msg = e.data || {};
    if (msg.type === "init") {
      if (msg.control) {
        control = msg.control;
        flags = new Int32Array(control, 0, 3);
        answerBytes = new Uint8Array(control, 12);
      }
      loading = load(msg.root).then(function (name) {
        if (control) pyodide.setInterruptBuffer(new Uint8Array(control, 0, 1));
        self.postMessage({ type: "ready", engine: name });
      }, function (err) {
        self.postMessage({ type: "failed", error: String(err && err.message ? err.message : err) });
      });
    } else if (msg.type === "run") {
      // Only the first MAX_LINES lines go to the page: a loop that prints
      // forever would otherwise bury it in messages (and delay Stop).
      var sent = 0;
      loading.then(function () {
        return run(msg.source, function (lines) {
          if (sent > MAX_LINES) return;
          var room = MAX_LINES - sent;
          if (lines.length > room) {
            lines = lines.slice(0, room);
            lines.push("(TB stopped showing output after " + MAX_LINES + " lines. The program is still running.)");
          }
          sent += lines.length;
          self.postMessage({ type: "out", lines: lines });
        }, ask, 0);
      }).then(function (err) {
        self.postMessage({
          type: "done", id: msg.id,
          error: err === "STOPPED" || err === "NEEDS_INPUT" ? null : err,
          stopped: err === "STOPPED", needsInput: err === "NEEDS_INPUT"
        });
      });
    }
  };
})();

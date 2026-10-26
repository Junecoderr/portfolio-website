// Runs TB programs in the browser.
//
//   TB.load()  -> Promise that resolves once Pyodide and tb.py are ready
//   TB.run(source, { onOutput(line), onAsk(prompt, recent) -> string })
//              recent = lines printed since the last question. While a
//              program runs the page can't repaint, so the default ask()
//              shows them inside the prompt box for context.
//              -> Promise<{ ok: boolean, error: string | null }>
//
// Pyodide (Python compiled to WebAssembly, about 10 MB) is served from
// vendor/pyodide/ on this same site; tb.py, tbcore/ (the language) and
// tblib/ (the standard library, with tblib/data/) from the site root. Nothing
// is fetched from anywhere else. Programs run on the page's main thread so
// ask() can use the browser's built-in (synchronous) prompt box.
(function () {
  "use strict";

  // Work out the site root from this script's own URL so pages in any
  // folder can use it.
  var here = (document.currentScript && document.currentScript.src) || location.href;
  var SITE_ROOT = new URL("../../", here).href;
  var PYODIDE_URL = SITE_ROOT + "vendor/pyodide/";
  var TIME_LIMIT_SECONDS = 10;

  // The engine: TB 3.0's bytecode VM (the default everywhere). Pyodide has
  // no threads, and the VM needs none: generators are suspended frames, and
  // tasks (async fn, parallel) run one after another (tbcore/tasks.py).
  // The time limit uses the interpreter's trace hook, which the VM calls on
  // every loop turn and function call, so even `while true { }` stops after
  // 10 seconds.
  var ENGINE = "vm";

  var PY_HELPER = [
    "import os, sys, time",
    "os.environ['TB_ENGINE'] = " + JSON.stringify(ENGINE),
    "import tb",
    "",
    "class _TBTimeout(BaseException):",
    "    pass",
    "",
    "# Keep the VM's fast (untraced) bytecode even though a trace hook is set:",
    "# its loop back-edges and function calls still call the hook, so the time",
    "# limit stops any endless loop (even `while true { }`) at full speed.",
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
    "def _tb_web_run(source, on_output, on_ask, time_limit):",
    "    deadline = [time.monotonic() + time_limit]",
    "    ticks = [0]",
    "    def hook(event, line, file, env):",
    "        ticks[0] += 1",
    "        if ticks[0] % 512 == 0 and time.monotonic() > deadline[0]:",
    "            raise _TBTimeout()",
    "    def output(text):",
    "        on_output(str(text))",
    "    def ask(prompt):",
    "        answer = on_ask(str(prompt))",
    "        deadline[0] = time.monotonic() + time_limit",
    "        return '' if answer is None else str(answer)",
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
    "    except _TBTimeout:",
    "        return ('Oops! The program ran for more than ' + str(int(time_limit)) +",
    "                ' seconds, so TB stopped it. Is there a while loop that never ends?')",
    "    except RecursionError:",
    "        return 'Oops! The program went too deep (too many functions inside functions).'",
    "    except Exception as err:",
    "        return 'Oops! Something went wrong inside TB itself: ' + type(err).__name__ + ': ' + str(err)",
    "    finally:",
    "        real_set(old_limit)",
  ].join("\n");

  // Pyodide packages that live outside its standard library zip, vendored
  // next to it by scripts/fetch_pyodide.py and loaded the first time a
  // program imports a TB library that needs them.
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

  var loading = null;
  var queue = Promise.resolve();
  var pyRun = null;
  var pyodideRef = null;
  var engineName = ENGINE;

  async function loadPackagesFor(source) {
    var wanted = packagesFor(source);
    if (!wanted.length || !pyodideRef) return;
    try {
      await pyodideRef.loadPackage(wanted, { messageCallback: function () {}, errorCallback: function () {} });
    } catch (e) {
      // Not vendored? The library gives its own friendly error when it runs.
    }
    wanted.forEach(function (p) { loadedPackages[p] = true; });
    // hashlib may already be imported (by the hash library) without OpenSSL;
    // reload it so scrypt and pbkdf2_hmac appear.
    if (wanted.indexOf("hashlib") >= 0) {
      try {
        pyodideRef.runPython("import importlib, sys\nif 'hashlib' in sys.modules: importlib.reload(sys.modules['hashlib'])");
      } catch (e) { /* keep going; crypto reports what is missing */ }
    }
  }

  function addScript(src) {
    return new Promise(function (resolve, reject) {
      if (typeof window.loadPyodide === "function") return resolve();
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { resolve(); };
      s.onerror = function () {
        reject(new Error("Could not load " + src + ". Was the site built with scripts/fetch_pyodide.py?"));
      };
      document.head.appendChild(s);
    });
  }

  function fetchBytes(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("Could not load " + url + " (HTTP " + r.status + ").");
      return r.arrayBuffer();
    }).then(function (b) { return new Uint8Array(b); });
  }
  function fetchJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("Could not load " + url + " (HTTP " + r.status + ").");
      return r.json();
    });
  }

  function load() {
    if (loading) return loading;
    loading = (async function () {
      await addScript(PYODIDE_URL + "pyodide.js");
      var pyodide = await window.loadPyodide({ indexURL: PYODIDE_URL, stdout: function () {}, stderr: function () {} });
      // tb.py, the standard library (tblib/) and the sample data the
      // guides use (apps/data/) all go into Pyodide's in-memory file system,
      // laid out like the real project, so `import logs` and
      // logs.read("apps/data/auth.log") work just as on a laptop.
      var HOME = "/home/pyodide/";
      // tbcore/ is the language itself (lexer, parser, compiler, VM, ...);
      // build_site.py lists its files in tbcore/index.json.
      var lists = await Promise.all([
        fetchJSON(SITE_ROOT + "tbcore/index.json"),
        fetchJSON(SITE_ROOT + "tblib/index.json"),
        fetchJSON(SITE_ROOT + "apps/data/index.json").catch(function () { return []; })
      ]);
      var files = ["tb.py"]
        .concat(lists[0].map(function (f) { return "tbcore/" + f; }))
        .concat(lists[1].map(function (f) { return "tblib/" + f; }))
        .concat(lists[2].map(function (f) { return "apps/data/" + f; }));
      var blobs = await Promise.all(files.map(function (f) { return fetchBytes(SITE_ROOT + f); }));
      files.forEach(function (f, i) {
        var dir = (HOME + f).replace(/\/[^\/]*$/, "");
        pyodide.FS.mkdirTree(dir);
        pyodide.FS.writeFile(HOME + f, blobs[i]);
      });
      pyodide.runPython("import os, sys\nos.chdir('/home/pyodide')\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
      pyodide.runPython(PY_HELPER);
      pyRun = pyodide.globals.get("_tb_web_run");
      engineName = String(pyodide.runPython("_tb_engine_name()"));
      pyodideRef = pyodide;
      return pyodide;
    })();
    loading.catch(function () { loading = null; });
    return loading;
  }

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

  function run(source, opts) {
    opts = opts || {};
    var userOutput = typeof opts.onOutput === "function" ? opts.onOutput : function () {};
    var recent = [];
    var onOutput = function (line) {
      recent.push(line);
      if (recent.length > 50) recent.shift();
      userOutput(line);
    };
    var onAsk = typeof opts.onAsk === "function" ? opts.onAsk : defaultAsk;
    var job = queue.then(async function () {
      try {
        await load();
      } catch (e) {
        return { ok: false, error: "TB could not start in this browser: " + (e && e.message ? e.message : e) };
      }
      await loadPackagesFor(source);
      // Give the browser one frame to paint "Running..." before we block.
      await new Promise(function (r) { setTimeout(r, 16); });
      try {
        var err = pyRun(String(source), onOutput, function (p) {
          var shown = recent;
          recent = [];
          var a = onAsk(p, shown);
          return a == null ? "" : String(a);
        }, TIME_LIMIT_SECONDS);
        return err == null ? { ok: true, error: null } : { ok: false, error: String(err) };
      } catch (e) {
        return { ok: false, error: "Oops! Something went wrong while running: " + (e && e.message ? e.message : e) };
      }
    });
    queue = job.catch(function () {});
    return job;
  }

  window.TB = {
    load: load, run: run, promptText: promptText, ask: defaultAsk,
    isLoaded: function () { return !!pyRun; },
    engine: function () { return engineName; }
  };
})();

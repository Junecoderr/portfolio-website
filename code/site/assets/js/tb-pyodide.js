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
// vendor/pyodide/ on this same site, and tb.py from the site root. Nothing
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

  var PY_HELPER = [
    "import sys, time",
    "import tb",
    "",
    "class _TBTimeout(BaseException):",
    "    pass",
    "",
    "def _tb_web_run(source, on_output, on_ask, time_limit):",
    "    deadline = [time.monotonic() + time_limit]",
    "    calls = [0]",
    "    def tracer(frame, event, arg):",
    "        calls[0] += 1",
    "        if calls[0] % 2000 == 0 and time.monotonic() > deadline[0]:",
    "            raise _TBTimeout()",
    "        return None",
    "    def output(text):",
    "        on_output(str(text))",
    "    def ask(prompt):",
    "        answer = on_ask(str(prompt))",
    "        deadline[0] = time.monotonic() + time_limit",
    "        return '' if answer is None else str(answer)",
    "    old_limit = sys.getrecursionlimit()",
    "    real_set = sys.setrecursionlimit",
    "    sys.settrace(tracer)",
    "    try:",
    "        tb.run(source, output=output, input_fn=ask)",
    "        return None",
    "    except tb.TBError as err:",
    "        return str(err)",
    "    except _TBTimeout:",
    "        return ('Oops! The program ran for more than ' + str(int(time_limit)) +",
    "                ' seconds, so TB stopped it. Is there a while loop that never ends?')",
    "    except RecursionError:",
    "        return 'Oops! The program went too deep (too many functions inside functions).'",
    "    except Exception as err:",
    "        return 'Oops! Something went wrong inside TB itself: ' + type(err).__name__ + ': ' + str(err)",
    "    finally:",
    "        sys.settrace(None)",
    "        real_set(old_limit)",
  ].join("\n");

  var loading = null;
  var queue = Promise.resolve();
  var pyRun = null;

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

  function load() {
    if (loading) return loading;
    loading = (async function () {
      await addScript(PYODIDE_URL + "pyodide.js");
      var pyodide = await window.loadPyodide({ indexURL: PYODIDE_URL, stdout: function () {}, stderr: function () {} });
      var res = await fetch(SITE_ROOT + "tb.py", { cache: "no-cache" });
      if (!res.ok) throw new Error("Could not load tb.py (HTTP " + res.status + ").");
      var source = await res.text();
      pyodide.FS.writeFile("/home/pyodide/tb.py", source);
      pyodide.runPython("import sys\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
      pyodide.runPython(PY_HELPER);
      pyRun = pyodide.globals.get("_tb_web_run");
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

  window.TB = { load: load, run: run, promptText: promptText, ask: defaultAsk, isLoaded: function () { return !!pyRun; } };
})();

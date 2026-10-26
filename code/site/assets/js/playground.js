// The playground: a textarea editor with line numbers and a highlighted
// overlay (the textarea's own text is transparent and sits on top), an example
// picker (examples/index.json), Run (button or Ctrl/Cmd+Enter) and an output
// panel. Edits are kept per example in this browser so a refresh never loses
// work; Reset brings back the original.
(function () {
  "use strict";

  var DRAFT_KEY = "tb-playground-drafts";
  var code = document.getElementById("pg-code");
  var gutter = document.getElementById("pg-gutter");
  var picker = document.getElementById("pg-example");
  var runBtn = document.getElementById("pg-run");
  var resetBtn = document.getElementById("pg-reset");
  var clearBtn = document.getElementById("pg-clear");
  var output = document.getElementById("pg-output");
  var status = document.getElementById("pg-status");
  var about = document.getElementById("pg-about");
  var editedNote = document.getElementById("pg-edited");
  var hl = document.getElementById("pg-highlight");
  var area = document.getElementById("pg-area");
  var shareBtn = document.getElementById("pg-share");
  var shareOut = document.getElementById("pg-share-out");
  var shareLink = document.getElementById("pg-share-link");
  var shareMsg = document.getElementById("pg-share-msg");
  if (!window.TBHighlight && area) area.classList.add("no-highlight");

  var examples = [];
  var current = null;      // name of the loaded example, or null for shared code
  var original = "";       // its original text, for Reset
  var running = false;

  if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) {
    var hint = document.querySelector(".ide-hint-out");
    if (hint) hint.innerHTML = "Press <kbd>⌘</kbd> + <kbd>Enter</kbd> to run.";
  }

  // ------------------------------------------------------------- drafts
  function readDrafts() {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function writeDraft(name, text) {
    if (!name) return;
    try {
      var d = readDrafts();
      if (text === original) delete d[name]; else d[name] = text;
      localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
    } catch (e) { /* storage unavailable: fine */ }
  }

  // ------------------------------------------------------------- gutter
  var lastLines = 0;
  function renderGutter(errorLine) {
    var lines = code.value.split("\n").length;
    if (lines === lastLines && errorLine === undefined) return;
    lastLines = lines;
    var html = "";
    for (var i = 1; i <= lines; i++) html += '<span data-line="' + i + '">' + i + "</span>";
    gutter.innerHTML = html;
    syncScroll();
  }
  function markError(line) {
    var old = gutter.querySelector(".is-error");
    if (old) old.classList.remove("is-error");
    if (!line) return;
    var el = gutter.querySelector('[data-line="' + line + '"]');
    if (el) el.classList.add("is-error");
  }
  function syncScroll() {
    gutter.scrollTop = code.scrollTop;
    if (hl) { hl.scrollTop = code.scrollTop; hl.scrollLeft = code.scrollLeft; }
  }
  function paintHighlight() {
    if (!hl || !window.TBHighlight) return;
    // The extra newline keeps the overlay as tall as the textarea's content.
    hl.innerHTML = window.TBHighlight.toHTML(code.value) + "\n";
    syncScroll();
  }

  // ------------------------------------------------------------- editing
  var saveTimer = null;
  code.addEventListener("input", function () {
    renderGutter();
    paintHighlight();
    markError(null);
    updateEdited();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { writeDraft(current, code.value); }, 400);
  });
  code.addEventListener("scroll", syncScroll);

  var tabTraps = true;
  code.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      run();
      return;
    }
    if (e.key === "Escape") { tabTraps = false; return; }
    if (e.key === "Tab" && tabTraps && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      var s = code.selectionStart, en = code.selectionEnd, v = code.value;
      if (e.shiftKey) {
        var ls = v.lastIndexOf("\n", s - 1) + 1;
        if (v.slice(ls, ls + 2) === "  ") {
          code.setRangeText("", ls, ls + 2, "preserve");
          code.selectionStart = code.selectionEnd = Math.max(ls, s - 2);
        }
      } else {
        code.setRangeText("  ", s, en, "end");
      }
      code.dispatchEvent(new Event("input"));
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      // Keep the indentation of the current line, one step more after "{".
      var pos = code.selectionStart;
      var val = code.value;
      var lineStart = val.lastIndexOf("\n", pos - 1) + 1;
      var line = val.slice(lineStart, pos);
      var indent = (/^[ \t]*/.exec(line) || [""])[0];
      if (/\{\s*$/.test(line)) indent += "  ";
      if (indent) {
        e.preventDefault();
        code.setRangeText("\n" + indent, pos, code.selectionEnd, "end");
        code.dispatchEvent(new Event("input"));
      }
    }
  });
  code.addEventListener("blur", function () { tabTraps = true; });
  code.addEventListener("focus", function () { tabTraps = true; });

  function setCode(text) {
    code.value = text;
    lastLines = -1;
    renderGutter();
    paintHighlight();
    markError(null);
    code.scrollTop = 0;
  }

  // ------------------------------------------------------------- examples
  function siteUrl(path) { return new URL(path, location.href).href; }

  function describe(name) {
    for (var i = 0; i < examples.length; i++) if (examples[i].name === name) return examples[i];
    return null;
  }

  function showAbout(ex, edited) {
    var text = ex && ex.description ? ex.description : "";
    if (ex && ex.interactive) text += (text ? " " : "") + "This one asks you questions, so a little box will pop up while it runs.";
    about.textContent = text;
  }

  // The "this is your edited version" note, shown whenever an example's
  // code differs from the original.
  function updateEdited() {
    if (editedNote) editedNote.hidden = !(current && code.value !== original);
  }

  function loadExample(name, pushUrl) {
    // Only ask the server for examples we know exist (no failing requests).
    if (!describe(name)) {
      var fallback = examples[0] ? examples[0].name : "for_tanisha";
      var message = "There's no example called \"" + name + "\", so here is \"" + (describe(fallback) || { title: fallback }).title + "\" instead.";
      if (name === fallback) {
        about.textContent = "The example programs couldn't be loaded. You can still type your own program.";
        return Promise.resolve();
      }
      return loadExample(fallback, true).then(function () { about.textContent = message; });
    }
    return fetch(siteUrl("examples/" + encodeURIComponent(name) + ".tb"), { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("not found");
        return r.text();
      })
      .then(function (text) {
        current = name;
        original = text;
        var draft = readDrafts()[name];
        var edited = typeof draft === "string" && draft !== text;
        setCode(edited ? draft : text);
        picker.value = name;
        showAbout(describe(name), edited);
        updateEdited();
        if (pushUrl) {
          var url = new URL(location.href);
          url.searchParams.set("example", name);
          url.hash = "";
          history.replaceState(null, "", url.href);
        }
      })
      .catch(function () {
        about.textContent = "That example couldn't be loaded. You can still type your own program.";
      });
  }

  function fillPicker(list, withCustom) {
    picker.innerHTML = "";
    list.forEach(function (ex) {
      var opt = document.createElement("option");
      opt.value = ex.name;
      opt.textContent = ex.title + (ex.interactive ? " (asks you things)" : "");
      picker.appendChild(opt);
    });
    if (withCustom) {
      var opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "Your code";
      picker.insertBefore(opt, picker.firstChild);
      picker.value = "";
    }
  }

  picker.addEventListener("change", function () {
    if (picker.value) loadExample(picker.value, true);
  });

  resetBtn.addEventListener("click", function () {
    if (current) {
      writeDraft(current, original);
      setCode(original);
      showAbout(describe(current), false);
      updateEdited();
    } else {
      setCode(original);
    }
    code.focus();
  });

  clearBtn.addEventListener("click", function () {
    output.textContent = "";
    markError(null);
  });

  // ------------------------------------------------------------- running
  function setStatus(text, busy) {
    status.innerHTML = "";
    if (!text) return;
    if (busy) {
      var sp = document.createElement("span");
      sp.className = "spinner";
      sp.setAttribute("aria-hidden", "true");
      status.appendChild(sp);
    }
    status.appendChild(document.createTextNode(text));
  }

  function span(cls, text) {
    var s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    return s;
  }

  function run() {
    if (running) return;
    running = true;
    runBtn.disabled = true;
    output.textContent = "";
    markError(null);
    var ready = window.TB.isLoaded();
    setStatus(ready ? "Running..." : "Getting TB ready. The first time takes a few seconds...", true);
    window.TB.load()
      .then(function () {
        setStatus("Running...", true);
        return window.TB.run(code.value, {
          onOutput: function (line) { output.appendChild(document.createTextNode(line + "\n")); },
          onAsk: function (prompt, recent) {
            var answer = window.TB.ask(prompt, recent);
            output.appendChild(span("out-ask", prompt));
            output.appendChild(span("out-answer", answer));
            output.appendChild(document.createTextNode("\n"));
            return answer;
          }
        });
      })
      .then(function (result) {
        if (!result.ok) {
          output.appendChild(span("out-error", result.error));
          var m = /On line (\d+)/.exec(result.error || "");
          if (m) markError(Number(m[1]));
          setStatus("");
        } else {
          if (!output.textContent) output.appendChild(span("out-done", "(The program finished without printing anything.)"));
          setStatus("");
        }
        output.scrollTop = output.scrollHeight;
      })
      .catch(function (e) {
        output.appendChild(span("out-error", "TB could not start in this browser: " + (e && e.message ? e.message : e)));
        setStatus("");
      })
      .then(function () {
        running = false;
        runBtn.disabled = false;
      });
  }
  runBtn.addEventListener("click", run);

  // ------------------------------------------------------------- share
  if (shareBtn) {
    shareBtn.addEventListener("click", function () {
      var url = new URL(location.href);
      url.search = "";
      url.hash = "code=" + encodeURIComponent(code.value);
      var link = url.href;
      shareOut.hidden = false;
      shareLink.value = link;
      var done = function (copied) {
        shareMsg.textContent = copied ? "Link copied." : "Copy this link to share it.";
        if (!copied) { shareLink.focus(); shareLink.select(); }
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(link).then(function () { done(true); }, function () { done(false); });
      } else {
        done(false);
      }
    });
  }

  // ------------------------------------------------------------- start
  var params = new URLSearchParams(location.search);
  var wanted = params.get("example");
  var shared = /^#code=/.test(location.hash) ? (function () {
    try { return decodeURIComponent(location.hash.slice(6)); } catch (e) { return null; }
  })() : null;

  var indexReady = fetch(siteUrl("examples/index.json"), { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : []; })
    .catch(function () { return []; })
    .then(function (list) {
      examples = Array.isArray(list) && list.length ? list : [{ name: "for_tanisha", title: "For Tanisha", description: "", interactive: false }];
    });

  indexReady.then(function () {
    if (shared != null && !wanted) {
      current = null;
      original = shared;
      setCode(shared);
      fillPicker(examples, true);
      about.textContent = "This is the code you opened from another page. Pick an example to load something else.";
    } else {
      fillPicker(examples);
      return loadExample(wanted || "for_tanisha", false);
    }
  });

  // Start downloading Pyodide straight away so Run feels quick.
  setStatus("Getting TB ready (about 10 MB, only the first time)...", true);
  window.TB.load().then(
    function () { if (!running) setStatus(""); },
    function (e) { setStatus("TB could not start in this browser: " + (e && e.message ? e.message : e)); }
  );
})();

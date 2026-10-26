// Home page: tabbed windows (hero samples, tour, install) and the hero's
// live Run button. Pyodide is only downloaded when someone presses Run.
(function () {
  "use strict";

  var here = (document.currentScript && document.currentScript.src) || location.href;
  var JS_DIR = new URL("./", here).href;

  // Accessible tabs: arrow keys move between tabs, Home/End jump.
  function setupTabs(list, onSelect) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    var vertical = list.getAttribute("aria-orientation") === "vertical";
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
      if (onSelect) onSelect(tab);
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t, false); });
      t.addEventListener("keydown", function (e) {
        var next = vertical ? ["ArrowDown", "ArrowRight"] : ["ArrowRight"];
        var prev = vertical ? ["ArrowUp", "ArrowLeft"] : ["ArrowLeft"];
        var j = null;
        if (next.indexOf(e.key) >= 0) j = (i + 1) % tabs.length;
        else if (prev.indexOf(e.key) >= 0) j = (i - 1 + tabs.length) % tabs.length;
        else if (e.key === "Home") j = 0;
        else if (e.key === "End") j = tabs.length - 1;
        if (j !== null) { e.preventDefault(); select(tabs[j], true); }
      });
    });
  }

  // ---------------------------------------------------------------- hero
  var hero = document.getElementById("hero-window");
  var out = document.getElementById("hero-out");
  var runBtn = document.getElementById("hero-run");
  var current = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // The output area holds either a <pre> (program output) or a screenshot
  // (desktop apps can't open windows in the browser).
  function freshPre(fileName) {
    out.textContent = "";
    var pre = el("pre", "");
    pre.appendChild(el("span", "prompt", "$ tb " + fileName));
    pre.appendChild(document.createTextNode("\n"));
    out.appendChild(pre);
    return pre;
  }

  function showStatic(tab) {
    current = tab;
    var panel = document.getElementById(tab.getAttribute("aria-controls"));
    var shot = panel.getAttribute("data-shot");
    if (shot) {
      out.textContent = "";
      var fig = el("figure", "hero-shot");
      var img = document.createElement("img");
      img.src = shot;
      img.alt = panel.getAttribute("data-shot-alt") || "";
      fig.appendChild(img);
      fig.appendChild(el("figcaption", "", "Desktop app: run it with the TB app on your computer."));
      out.appendChild(fig);
      runBtn.hidden = true;
      return;
    }
    runBtn.hidden = false;
    var pre = freshPre(tab.getAttribute("data-file"));
    var text = panel.getAttribute("data-output") || "";
    if (panel.hasAttribute("data-error")) pre.appendChild(el("span", "out-error", text));
    else pre.appendChild(document.createTextNode(text));
  }

  var tbLoading = null;
  function ensureTB() {
    if (window.TB) return Promise.resolve(window.TB);
    if (tbLoading) return tbLoading;
    tbLoading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = JS_DIR + "tb-pyodide.js";
      s.onload = function () { resolve(window.TB); };
      s.onerror = function () { tbLoading = null; reject(new Error("Could not load tb-pyodide.js")); };
      document.head.appendChild(s);
    });
    return tbLoading;
  }

  if (hero && out && runBtn) {
    var heroTabs = hero.querySelector('[role="tablist"]');
    setupTabs(heroTabs, showStatic);
    showStatic(heroTabs.querySelector('[aria-selected="true"]'));

    runBtn.addEventListener("click", function () {
      if (runBtn.disabled || !current) return;
      var panel = document.getElementById(current.getAttribute("aria-controls"));
      var source = panel.querySelector("code").textContent;
      var label = runBtn.lastChild;
      runBtn.disabled = true;
      var pre = freshPre(current.getAttribute("data-file"));
      var waiting = el("span", "prompt", (window.TB && window.TB.isLoaded()) ? "Running..." : "Starting TB in your browser (about 10 MB, first time only)...");
      pre.appendChild(waiting);
      ensureTB()
        .then(function (TB) { return TB.load().then(function () { return TB; }); })
        .then(function (TB) {
          waiting.remove();
          return TB.run(source, {
            onOutput: function (line) { pre.appendChild(document.createTextNode(line + "\n")); },
            onAsk: function (prompt, recent) { return TB.ask(prompt, recent); }
          });
        })
        .then(function (result) {
          if (!result.ok) pre.appendChild(el("span", "out-error", result.error));
        })
        .catch(function (e) {
          if (waiting.parentNode) waiting.remove();
          pre.appendChild(el("span", "out-error", "TB could not start: " + (e && e.message ? e.message : e)));
        })
        .then(function () { runBtn.disabled = false; if (label) label.textContent = "Run again"; });
    });
  }

  // ------------------------------------------------------ other tab lists
  var lists = document.querySelectorAll('[role="tablist"]');
  for (var i = 0; i < lists.length; i++) {
    if (hero && hero.contains(lists[i])) continue;
    setupTabs(lists[i]);
  }
})();

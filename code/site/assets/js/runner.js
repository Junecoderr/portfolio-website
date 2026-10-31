// Colours every TB code block and adds a Run button under each
// <pre class="tb-code" data-runnable> block. Pyodide is only downloaded the
// first time someone presses Run.
//
//   <pre class="tb-code" data-runnable><code>say("hi")</code></pre>
//   <pre class="tb-code" data-runnable data-example="guess"><code>...</code></pre>
//       -> also gets an "Open in playground" link (playground.html?example=guess)
//   <pre class="tb-code" data-example="guess"><code>...</code></pre>
//       -> only the "Open in playground" link
(function () {
  "use strict";

  var here = (document.currentScript && document.currentScript.src) || location.href;
  var JS_DIR = new URL("./", here).href;
  var SITE_ROOT = new URL("../../", here).href;

  // Colouring lives in highlight.js (window.TBHighlight); use it if present.
  function highlight(codeEl) {
    if (window.TBHighlight) window.TBHighlight.highlight(codeEl);
  }

  // --------------------------------------------------------------- loader
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

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.6-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>';

  function playgroundHref(name) {
    return SITE_ROOT + "playground.html?example=" + encodeURIComponent(name);
  }

  function setupBlock(pre, index) {
    if (pre.getAttribute("data-runner-ready")) return;
    pre.setAttribute("data-runner-ready", "1");
    var code = pre.querySelector("code") || pre;
    var runnable = pre.hasAttribute("data-runnable");
    var example = pre.getAttribute("data-example");
    if (!runnable && !example) return;

    var wrap = el("div", "runner");
    var bar = el("div", "runner-bar");
    wrap.appendChild(bar);

    if (runnable) {
      var source = code.textContent;
      var outId = "tb-output-" + index;
      var btn = el("button", "button button-run");
      btn.type = "button";
      btn.innerHTML = PLAY_ICON + "<span>Run</span>";
      btn.setAttribute("aria-controls", outId);
      var status = el("span", "runner-status");
      status.setAttribute("role", "status");
      var output = el("div", "output");
      output.id = outId;
      output.setAttribute("aria-live", "polite");
      output.setAttribute("aria-label", "Program output");
      output.hidden = true;

      bar.appendChild(btn);

      var STOP_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>';
      var running = false;
      btn.addEventListener("click", function () {
        // While it runs, the same button is Stop.
        if (running) {
          status.textContent = "Stopping...";
          if (window.TB) window.TB.stop();
          return;
        }
        running = true;
        btn.innerHTML = STOP_ICON + "<span>Stop</span>";
        output.hidden = false;
        wrap.classList.add("has-output");
        output.textContent = "";
        var firstLoad = !(window.TB && window.TB.isLoaded());
        status.textContent = firstLoad ? "Waking TB up (first time takes a few seconds)..." : "Running...";
        ensureTB()
          .then(function (TB) {
            return TB.load().then(function () {
              status.textContent = "Running...";
              return TB.run(source, {
                onOutput: function (line) { output.appendChild(document.createTextNode(line + "\n")); },
                onAsk: function (prompt, recent) { return TB.askInline(output, prompt, recent); }
              });
            });
          })
          .then(function (result) {
            if (!result.ok) output.appendChild(el("span", result.stopped ? "out-done" : "out-error", result.error));
            status.textContent = result.ok ? "Done." : result.stopped ? "Stopped." : "Stopped with a mistake.";
            if (!output.textContent) output.appendChild(el("span", "out-done", "(The program finished without printing anything.)"));
          })
          .catch(function (e) {
            output.appendChild(el("span", "out-error", "TB could not start: " + (e && e.message ? e.message : e)));
            status.textContent = "";
          })
          .then(function () {
            running = false;
            btn.innerHTML = PLAY_ICON + "<span>Run</span>";
          });
      });

      var tryIt = el("a", "button button-ghost", example ? "Open in playground" : "Try changing it");
      tryIt.href = example ? playgroundHref(example) : SITE_ROOT + "playground.html#code=" + encodeURIComponent(source);
      bar.appendChild(tryIt);
      bar.appendChild(status);
      wrap.appendChild(output);
    } else {
      var link = el("a", "button button-run", "Open in playground");
      link.href = playgroundHref(example);
      bar.appendChild(link);
    }

    pre.parentNode.insertBefore(wrap, pre.nextSibling);
    // Keep the code block and its bar visually joined.
    pre.style.borderBottomLeftRadius = "0";
    pre.style.borderBottomRightRadius = "0";
    pre.style.marginBottom = "0";
  }

  function init() {
    var blocks = document.querySelectorAll("pre.tb-code");
    for (var i = 0; i < blocks.length; i++) {
      highlight(blocks[i].querySelector("code") || blocks[i]);
      setupBlock(blocks[i], i);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

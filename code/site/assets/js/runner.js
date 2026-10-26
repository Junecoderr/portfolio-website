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

  // ------------------------------------------------------------ highlighter
  var KEYWORDS = /^(let|fn|return|if|else|while|for|in|and|or|not|true|false|nil)$/;
  var BUILTINS = /^(say|len|str|num|range|push|ask|care)$/;

  function tokenize(src) {
    var out = [];
    var i = 0;
    var n = src.length;
    while (i < n) {
      var c = src[i];
      var start = i;
      if (c === "#") {
        while (i < n && src[i] !== "\n") i++;
        out.push(["com", src.slice(start, i)]);
      } else if (c === '"' || c === "'") {
        i++;
        while (i < n && src[i] !== c && src[i] !== "\n") {
          if (src[i] === "\\") i++;
          i++;
        }
        i = Math.min(n, i + 1);
        out.push(["str", src.slice(start, i)]);
      } else if (/[0-9]/.test(c)) {
        while (i < n && /[0-9.]/.test(src[i])) i++;
        out.push(["num", src.slice(start, i)]);
      } else if (/[A-Za-z_]/.test(c)) {
        while (i < n && /[A-Za-z0-9_]/.test(src[i])) i++;
        var word = src.slice(start, i);
        if (KEYWORDS.test(word)) out.push(["key", word]);
        else if (BUILTINS.test(word)) out.push(["fn", word]);
        else out.push(["", word]);
      } else {
        i++;
        out.push(["", c]);
      }
    }
    return out;
  }

  function highlight(codeEl) {
    if (codeEl.getAttribute("data-highlighted")) return;
    var src = codeEl.textContent;
    var frag = document.createDocumentFragment();
    var tokens = tokenize(src);
    for (var t = 0; t < tokens.length; t++) {
      var kind = tokens[t][0];
      var text = tokens[t][1];
      if (!kind) {
        frag.appendChild(document.createTextNode(text));
      } else {
        var span = document.createElement("span");
        span.className = "tok-" + kind;
        span.textContent = text;
        frag.appendChild(span);
      }
    }
    codeEl.textContent = "";
    codeEl.appendChild(frag);
    codeEl.setAttribute("data-highlighted", "1");
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

      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        btn.disabled = true;
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
                onAsk: function (prompt, recent) {
                  var answer = window.TB.ask(prompt, recent);
                  output.appendChild(el("span", "out-ask", prompt));
                  output.appendChild(el("span", "out-answer", answer));
                  output.appendChild(document.createTextNode("\n"));
                  return answer;
                }
              });
            });
          })
          .then(function (result) {
            if (!result.ok) output.appendChild(el("span", "out-error", result.error));
            status.textContent = result.ok ? "Done." : "Stopped with a mistake.";
            if (!output.textContent) output.appendChild(el("span", "out-done", "(The program finished without printing anything.)"));
          })
          .catch(function (e) {
            output.appendChild(el("span", "out-error", "TB could not start: " + (e && e.message ? e.message : e)));
            status.textContent = "";
          })
          .then(function () { btn.disabled = false; });
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

  window.TBHighlight = { tokenize: tokenize, highlight: highlight };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

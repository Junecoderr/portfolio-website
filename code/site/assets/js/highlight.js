// A tiny syntax highlighter for TB. No libraries.
// It colours every <pre class="tb-code"><code> on the page at runtime (the
// HTML source stays plain text, so tests can compare it with the .tb files).
//   TBHighlight.tokenize(src) -> [[kind, text], ...]
//   TBHighlight.toHTML(src)   -> escaped HTML with <span class="tok-..."> wrappers
//   TBHighlight.highlight(codeElement)
// Kinds: com, str, key, num, fn (built-in), method (.name( ), punc ({ } [ ]),
// interp (the { } around an f-string expression).
(function () {
  "use strict";

  var KEYWORDS = { let: 1, fn: 1, "return": 1, "if": 1, "else": 1, "while": 1, "for": 1, "in": 1,
    "true": 1, "false": 1, nil: 1, and: 1, or: 1, not: 1,
    "import": 1, as: 1, "try": 1, "catch": 1, "throw": 1, "break": 1, "continue": 1 };
  var BUILTINS = { say: 1, len: 1, str: 1, num: 1, range: 1, push: 1, ask: 1, care: 1,
    type: 1, keys: 1, "int": 1, round: 1, min: 1, max: 1, sum: 1, sorted: 1, exit: 1 };

  function isDigit(c) { return c >= "0" && c <= "9"; }
  function isStart(c) { return (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_"; }
  function isPart(c) { return isStart(c) || isDigit(c); }

  function tokenize(src) {
    var out = [];
    var i = 0, n = src.length;
    function push(kind, text) {
      if (!text) return;
      var last = out[out.length - 1];
      if (!kind && last && !last[0]) last[1] += text; else out.push([kind, text]);
    }
    function prevSignificant() {
      for (var k = out.length - 1; k >= 0; k--) {
        var t = out[k][1];
        if (out[k][0] === "" && /^\s*$/.test(t)) continue;
        if (out[k][0] === "") { var tr = t.replace(/\s+$/, ""); return tr.charAt(tr.length - 1); }
        return null;
      }
      return null;
    }
    while (i < n) {
      var c = src[i], start = i;
      if (c === "#") {
        while (i < n && src[i] !== "\n") i++;
        push("com", src.slice(start, i));
      } else if ((c === "f" || c === "F") && (src[i + 1] === '"' || src[i + 1] === "'") && !(i > 0 && isPart(src[i - 1]))) {
        // f-string: literal parts are strings, {expr} parts are highlighted code.
        var q = src[i + 1];
        i += 2;
        var lit = src.slice(start, i);
        while (i < n && src[i] !== q && src[i] !== "\n") {
          if (src[i] === "\\") { lit += src.slice(i, i + 2); i += 2; continue; }
          if (src[i] === "{" && src[i + 1] === "{") { lit += "{{"; i += 2; continue; }
          if (src[i] === "}" && src[i + 1] === "}") { lit += "}}"; i += 2; continue; }
          if (src[i] === "{") {
            push("str", lit); lit = "";
            var depth = 1, j = i + 1;
            while (j < n && depth > 0 && src[j] !== "\n") {
              if (src[j] === "{") depth++;
              else if (src[j] === "}") depth--;
              if (depth > 0) j++;
            }
            push("interp", "{");
            var inner = tokenize(src.slice(i + 1, j));
            for (var t = 0; t < inner.length; t++) push(inner[t][0], inner[t][1]);
            if (j < n && src[j] === "}") { push("interp", "}"); j++; }
            i = j;
            continue;
          }
          lit += src[i]; i++;
        }
        if (i < n && src[i] === q) { lit += q; i++; }
        push("str", lit);
      } else if (c === '"' || c === "'") {
        i++;
        while (i < n && src[i] !== c && src[i] !== "\n") { if (src[i] === "\\") i++; i++; }
        i = Math.min(n, i + 1);
        push("str", src.slice(start, i));
      } else if (isDigit(c)) {
        while (i < n && (isDigit(src[i]) || src[i] === ".")) i++;
        push("num", src.slice(start, i));
      } else if (isStart(c)) {
        while (i < n && isPart(src[i])) i++;
        var word = src.slice(start, i);
        var afterDot = prevSignificant() === ".";
        var k2 = i; while (k2 < n && src[k2] === " ") k2++;
        var called = src[k2] === "(";
        if (afterDot) push(called ? "method" : "", word);
        else if (KEYWORDS.hasOwnProperty(word)) push("key", word);
        else if (BUILTINS.hasOwnProperty(word) && called) push("fn", word);
        else push("", word);
      } else if (c === "{" || c === "}" || c === "[" || c === "]") {
        i++;
        push("punc", c);
      } else {
        i++;
        push("", c);
      }
    }
    return out;
  }

  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  function toHTML(src) {
    var tokens = tokenize(src), html = "";
    for (var t = 0; t < tokens.length; t++) {
      var kind = tokens[t][0], text = esc(tokens[t][1]);
      html += kind ? '<span class="tok-' + kind + '">' + text + "</span>" : text;
    }
    return html;
  }

  function highlight(codeEl) {
    if (!codeEl || codeEl.getAttribute("data-highlighted")) return;
    var src = codeEl.textContent;
    var frag = document.createDocumentFragment();
    var tokens = tokenize(src);
    for (var t = 0; t < tokens.length; t++) {
      if (!tokens[t][0]) { frag.appendChild(document.createTextNode(tokens[t][1])); continue; }
      var span = document.createElement("span");
      span.className = "tok-" + tokens[t][0];
      span.textContent = tokens[t][1];
      frag.appendChild(span);
    }
    codeEl.textContent = "";
    codeEl.appendChild(frag);
    codeEl.setAttribute("data-highlighted", "1");
  }

  function highlightAll(root) {
    var blocks = (root || document).querySelectorAll("pre.tb-code code, pre.tb-code-plain code");
    for (var i = 0; i < blocks.length; i++) highlight(blocks[i]);
  }

  window.TBHighlight = { tokenize: tokenize, toHTML: toHTML, highlight: highlight, highlightAll: highlightAll };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { highlightAll(); });
  else highlightAll();
})();

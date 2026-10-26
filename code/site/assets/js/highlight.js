// A tiny syntax highlighter for TB. No libraries.
// It colours every <pre class="tb-code"><code> on the page at runtime (the
// HTML source stays plain text, so tests can compare it with the .tb files).
//   TBHighlight.tokenize(src) -> [[kind, text], ...]
//   TBHighlight.toHTML(src)   -> escaped HTML with <span class="tok-..."> wrappers
//   TBHighlight.highlight(codeElement)
// Kinds: com, str, key, num, fn (built-in), method (.name( ), punc ({ } [ ]),
// interp (the { } around an f-string expression), spec (its :format part).
// Understands TB 3.0: b"" r"" f"" (and rb, rf) prefixes, """triple""" text,
// 0x / 0b / 0o numbers with _ separators, and the new keywords.
(function () {
  "use strict";

  // Reserved words (TB 3.0) plus the statement words that are special where
  // a statement starts (import, try, break, defer, assert, ...).
  var KEYWORDS = {};
  ("let fn return if else while for in true false nil and or not import as try catch throw " +
   "break continue class self super static extends match const export from yield async await " +
   "is enum finally then defer assert done").split(" ").forEach(function (w) { KEYWORDS[w] = 1; });
  var BUILTINS = {};
  ("say len str num range push ask care type keys int float format round min max sum sorted exit")
    .split(" ").forEach(function (w) { BUILTINS[w] = 1; });
  var PREFIXES = { f: 1, r: 1, b: 1, rf: 1, fr: 1, rb: 1, br: 1 };

  function isDigit(c) { return c >= "0" && c <= "9"; }
  function isHex(c) { return isDigit(c) || (c >= "a" && c <= "f") || (c >= "A" && c <= "F"); }
  function isStart(c) { return (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_"; }
  function isPart(c) { return isStart(c) || isDigit(c); }

  // End of a number starting at i: 0x1F, 0b1010, 0o17, 1_000, 2.5e-3 (not the
  // ".." of a range).
  function numberEnd(src, i) {
    var n = src.length;
    if (src[i] === "0" && /[xXbBoO]/.test(src[i + 1] || "")) {
      var kind = src[i + 1].toLowerCase(), j = i + 2;
      while (j < n && (src[j] === "_" || (kind === "x" ? isHex(src[j]) : kind === "b" ? /[01]/.test(src[j]) : /[0-7]/.test(src[j])))) j++;
      return j;
    }
    while (i < n && (isDigit(src[i]) || src[i] === "_")) i++;
    if (src[i] === "." && isDigit(src[i + 1] || "")) {
      i++;
      while (i < n && (isDigit(src[i]) || src[i] === "_")) i++;
    }
    if ((src[i] === "e" || src[i] === "E") && (isDigit(src[i + 1] || "") || (/[+-]/.test(src[i + 1] || "") && isDigit(src[i + 2] || "")))) {
      i += 2;
      while (i < n && isDigit(src[i])) i++;
    }
    return i;
  }

  // Inside an f-string hole starting just after "{": the index of the
  // closing "}" and of a top-level "!r" or ":" format spec (or -1).
  function scanHole(src, i, multiline) {
    var n = src.length, depth = 0, spec = -1;
    while (i < n) {
      var c = src[i];
      if (c === "\n" && !multiline) break;
      if (spec < 0 && (c === '"' || c === "'")) {
        var q = c; i++;
        while (i < n && src[i] !== q && src[i] !== "\n") { if (src[i] === "\\") i++; i++; }
        i++; continue;
      }
      if (spec < 0 && (c === "(" || c === "[" || c === "{")) depth++;
      else if (spec < 0 && (c === ")" || c === "]")) depth--;
      else if (c === "}") { if (depth === 0) return { end: i, spec: spec }; if (spec < 0) depth--; }
      else if (depth === 0 && spec < 0 && (c === ":" || (c === "!" && src[i + 1] === "r"))) spec = i;
      i++;
    }
    return { end: i, spec: spec };
  }

  function tokenize(src) {
    var out = [];
    var i = 0, n = src.length;
    function push(kind, text) {
      if (!text) return;
      var last = out[out.length - 1];
      if (last && last[0] === kind && (!kind || kind === "str")) last[1] += text; else out.push([kind, text]);
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
    // A text literal at i (after any prefix of length plen).
    function readString(plen) {
      var start = i;
      var prefix = src.slice(i, i + plen).toLowerCase();
      var isF = prefix.indexOf("f") >= 0, isRaw = prefix.indexOf("r") >= 0;
      i += plen;
      var q = src[i];
      var triple = src[i + 1] === q && src[i + 2] === q;
      var close = triple ? q + q + q : q;
      i += close.length;
      var lit = src.slice(start, i);
      while (i < n) {
        if (src.substr(i, close.length) === close) { lit += close; i += close.length; break; }
        if (src[i] === "\n" && !triple) break;
        if (src[i] === "\\" && !isRaw) { lit += src.slice(i, i + 2); i += 2; continue; }
        if (isF && src[i] === "{" && src[i + 1] === "{") { lit += "{{"; i += 2; continue; }
        if (isF && src[i] === "}" && src[i + 1] === "}") { lit += "}}"; i += 2; continue; }
        if (isF && src[i] === "{") {
          push("str", lit); lit = "";
          var hole = scanHole(src, i + 1, triple);
          var exprEnd = hole.spec >= 0 ? hole.spec : hole.end;
          push("interp", "{");
          var inner = tokenize(src.slice(i + 1, exprEnd));
          for (var t = 0; t < inner.length; t++) push(inner[t][0], inner[t][1]);
          if (hole.spec >= 0) push("spec", src.slice(hole.spec, hole.end));
          if (hole.end < n && src[hole.end] === "}") { push("interp", "}"); i = hole.end + 1; }
          else i = hole.end;
          continue;
        }
        lit += src[i]; i++;
      }
      push("str", lit);
    }
    while (i < n) {
      var c = src[i], start = i;
      if (c === "#") {
        while (i < n && src[i] !== "\n") i++;
        push("com", src.slice(start, i));
      } else if (c === '"' || c === "'") {
        readString(0);
      } else if (isDigit(c) && !(i > 0 && isPart(src[i - 1]))) {
        i = numberEnd(src, i);
        push("num", src.slice(start, i));
      } else if (isStart(c)) {
        while (i < n && isPart(src[i])) i++;
        var word = src.slice(start, i);
        if (PREFIXES.hasOwnProperty(word.toLowerCase()) && (src[i] === '"' || src[i] === "'")) {
          i = start;
          readString(word.length);
          continue;
        }
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

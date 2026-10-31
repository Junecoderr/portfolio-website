// Site-wide search. The index (search-index.json) is built with the site by
// scripts/search_index.py; nothing leaves this site. The header's search link
// opens a box under the header (or press /); arrow keys pick a result, Enter
// opens it, Escape closes. search.html shows every result for ?q=.
(function () {
  "use strict";

  var toggle = document.querySelector(".search-toggle");
  if (!toggle) return;
  var base = toggle.href; // .../search.html, so relative URLs work at any depth
  var index = null;
  var loading = null;

  function load() {
    if (index) return Promise.resolve(index);
    if (!loading) {
      loading = fetch(new URL("search-index.json", base).href)
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
          index = data.map(function (e) {
            e.tl = e.t.toLowerCase();
            e.hay = (e.t + " " + e.p + " " + e.x).toLowerCase();
            return e;
          });
          return index;
        })
        .catch(function (err) { loading = null; throw err; });
    }
    return loading;
  }

  function search(query, limit) {
    var q = query.trim().toLowerCase();
    var terms = q.split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    var found = [];
    for (var i = 0; i < index.length; i++) {
      var e = index[i];
      var score = 0;
      for (var k = 0; k < terms.length; k++) {
        var t = terms[k];
        if (e.hay.indexOf(t) < 0) { score = -1; break; }
        if (e.tl.indexOf(t) >= 0) score += 10;
        if (e.tl.indexOf(t) === 0 || e.tl.indexOf("." + t) >= 0) score += 5;
        if (e.p.toLowerCase().indexOf(t) >= 0) score += 3;
        score += 1;
      }
      if (score < 0) continue;
      if (e.tl === q || e.tl.indexOf(q + "(") >= 0 || e.tl.indexOf("." + q + "(") >= 0) score += 30;
      if (e.u.indexOf("#") < 0) score += 2; // a whole page beats one of its sections
      found.push([score, e]);
    }
    found.sort(function (a, b) { return b[0] - a[0] || a[1].t.length - b[1].t.length; });
    return found.slice(0, limit).map(function (x) { return x[1]; });
  }

  function href(entry) { return new URL(entry.u, base).href; }

  function resultBody(entry, el) {
    var title = document.createElement("span");
    title.className = "search-title";
    title.textContent = entry.t;
    var meta = document.createElement("span");
    meta.className = "search-meta";
    meta.textContent = entry.k + (entry.p && entry.p !== entry.t ? " · " + entry.p : "");
    el.appendChild(title);
    el.appendChild(meta);
    if (entry.x) {
      var text = document.createElement("span");
      text.className = "search-text";
      text.textContent = entry.x;
      el.appendChild(text);
    }
  }

  // The results page (search.html).
  var pageList = document.getElementById("search-page-results");
  if (pageList) {
    var pageInput = document.getElementById("search-page-q");
    var status = document.getElementById("search-page-status");
    var q = new URLSearchParams(location.search).get("q") || "";
    pageInput.value = q;
    if (q.trim()) {
      status.textContent = "Searching...";
      load().then(function () {
        var hits = search(q, 60);
        status.textContent = hits.length ? hits.length + (hits.length === 60 ? "+" : "") + " results for “" + q + "”."
          : "Nothing found for “" + q + "”. Try a shorter word.";
        hits.forEach(function (entry) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = href(entry);
          resultBody(entry, a);
          li.appendChild(a);
          pageList.appendChild(li);
        });
      }, function () { status.textContent = "The search index did not load. Try again later."; });
    } else {
      pageInput.focus();
    }
  }

  // The box under the header.
  var header = toggle.closest(".site-header");
  var panel = null, input, list, hint, results = [], active = -1, timer = 0;

  function build() {
    panel = document.createElement("div");
    panel.className = "search-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Search the site");
    var form = document.createElement("form");
    form.className = "search-box";
    form.setAttribute("role", "search");
    form.action = base;
    form.method = "get";
    input = document.createElement("input");
    input.type = "search";
    input.name = "q";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "Search the guide, book, cookbook and library";
    input.setAttribute("aria-label", "Search");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", "search-list");
    input.setAttribute("aria-autocomplete", "list");
    var close = document.createElement("button");
    close.type = "button";
    close.className = "search-close";
    close.textContent = "Esc";
    close.setAttribute("aria-label", "Close search");
    form.appendChild(input);
    form.appendChild(close);
    list = document.createElement("ul");
    list.id = "search-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Results");
    hint = document.createElement("p");
    hint.className = "search-hint";
    hint.setAttribute("role", "status");
    hint.setAttribute("aria-live", "polite");
    panel.appendChild(form);
    panel.appendChild(list);
    panel.appendChild(hint);
    (header || document.body).appendChild(panel);

    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(update, 60);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!results.length) return;
        var step = e.key === "ArrowDown" ? 1 : -1;
        mark((active + step + results.length) % results.length);
      } else if (e.key === "Enter") {
        if (active >= 0 && results[active]) {
          e.preventDefault();
          location.href = href(results[active]);
        } // else the form opens search.html?q=...
      } else if (e.key === "Escape") {
        e.preventDefault();
        hide();
      }
    });
    close.addEventListener("click", hide);
    panel.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !panel.contains(e.target) && !toggle.contains(e.target)) hide(true);
    });
  }

  function mark(n) {
    active = n;
    var items = list.children;
    for (var i = 0; i < items.length; i++) {
      var on = i === n;
      items[i].setAttribute("aria-selected", on ? "true" : "false");
      items[i].classList.toggle("is-active", on);
      if (on) items[i].scrollIntoView({ block: "nearest" });
    }
    if (n >= 0) input.setAttribute("aria-activedescendant", "search-opt-" + n);
    else input.removeAttribute("aria-activedescendant");
  }

  function update() {
    var q = input.value;
    if (!q.trim()) {
      results = [];
      list.textContent = "";
      hint.textContent = "Type to search. Press / from any page.";
      input.setAttribute("aria-expanded", "false");
      mark(-1);
      return;
    }
    load().then(function () {
      if (input.value !== q) return;
      results = search(q, 8);
      list.textContent = "";
      results.forEach(function (entry, n) {
        var li = document.createElement("li");
        li.id = "search-opt-" + n;
        li.setAttribute("role", "option");
        var a = document.createElement("a");
        a.href = href(entry);
        a.tabIndex = -1;
        resultBody(entry, a);
        li.appendChild(a);
        list.appendChild(li);
      });
      input.setAttribute("aria-expanded", results.length ? "true" : "false");
      hint.textContent = results.length ? "Enter opens the result. Press Enter with no result picked to see all."
        : "Nothing found. Try a shorter word.";
      mark(results.length ? 0 : -1);
    }, function () { hint.textContent = "The search index did not load. Try again later."; });
  }

  function show() {
    if (!panel) build();
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    input.focus();
    input.select();
    update();
  }

  function hide(keepFocus) {
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    if (keepFocus !== true) toggle.focus();
  }

  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", function (e) {
    if (pageList) return; // already on the results page: just go there
    e.preventDefault();
    if (panel && !panel.hidden) hide(); else show();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    e.preventDefault();
    if (pageList) { document.getElementById("search-page-q").focus(); return; }
    show();
  });
})();

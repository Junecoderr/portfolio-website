// The TB Package Index pages (packages.html and packages/*.html).
//
// Each page's container says which page it is (data-registry-page="list" |
// "view" | "publish" | "account" | "admin"). Data comes from /api/registry/*;
// without the API (local previews, tests) the list and package pages fall back
// to the bundled packages in packages/bundled.json, and the signed-in pages
// say the index isn't reachable. Every bit of package data is put on the page
// with textContent / createElement, never as HTML: READMEs go through a small
// Markdown renderer that builds DOM nodes, so raw HTML in a README shows as text.
(function () {
  "use strict";

  var root = document.querySelector("[data-registry-page]");
  if (!root || !window.fetch) return;
  var kind = root.getAttribute("data-registry-page");
  var base = root.getAttribute("data-base") || "";
  var API = "/api/registry/";
  var format = window.Intl && Intl.NumberFormat ? new Intl.NumberFormat(undefined) : null;
  var dateFormat = window.Intl && Intl.DateTimeFormat ? new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }) : null;

  function num(n) { return format ? format.format(n) : String(n); }
  function day(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return dateFormat ? dateFormat.format(d) : d.toISOString().slice(0, 10);
  }
  function $(sel, from) { return (from || root).querySelector(sel); }
  function $all(sel, from) { return Array.prototype.slice.call((from || root).querySelectorAll(sel)); }

  /** el("a", {href: "x", className: "y"}, ["text", child]) */
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === "className") node.className = value;
        else if (key === "text") node.textContent = value;
        else node.setAttribute(key, value === true ? "" : String(value));
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
  function show(node, on) { if (node) node.hidden = !on; }

  function ApiError(message, status) { this.message = message; this.status = status; }

  function api(path, options) {
    options = options || {};
    var init = { method: options.method || "GET", credentials: "same-origin", headers: { Accept: "application/json" } };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    return fetch(API + path, init).then(function (res) {
      var type = res.headers.get("content-type") || "";
      if (type.indexOf("application/json") < 0) throw new ApiError("offline", 0);
      return res.json().then(function (data) {
        if (!res.ok) throw new ApiError((data && data.error) || "The package index said " + res.status + ".", res.status);
        return data;
      });
    }, function () { throw new ApiError("offline", 0); });
  }

  function bundled() {
    var url = root.getAttribute("data-bundled");
    return fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      var out = {};
      (data.packages || []).forEach(function (p) {
        var versions = {};
        versions[p.version] = { sha256: p.sha256 };
        out[p.name] = {
          description: p.description, version: p.version, keywords: p.keywords || [], license: p.license,
          owner: p.owner || "tb", downloads: null, versions: versions, readme: p.readme, created: p.created,
          dependencies: p.dependencies || {}, entry: p.entry
        };
      });
      return out;
    });
  }

  function viewHref(name) { return base + "view.html?name=" + encodeURIComponent(name); }
  function importName(name) { return name.replace(/^tb-/, "").replace(/-/g, "_"); }

  function message(text, isError) {
    var box = $("[data-registry-message]");
    if (!box) return;
    box.textContent = text;
    box.classList.toggle("registry-error", Boolean(isError));
    show(box, Boolean(text));
  }

  // ------------------------------------------------------------ safe Markdown

  var SAFE_URL = /^(https?:\/\/|#|mailto:)/i;

  function inline(text) {
    var frag = document.createDocumentFragment();
    var patterns = [
      { re: /`([^`]+)`/, make: function (m) { return el("code", { text: m[1] }); } },
      { re: /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/, make: function (m) {
        return el("span", { className: "registry-img-alt", text: "[image" + (m[1] ? ": " + m[1] : "") + "]" });
      } },
      { re: /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/, make: function (m) {
        if (!SAFE_URL.test(m[2])) return el("span", null, [inline(m[1])]);
        return el("a", { href: m[2], rel: "nofollow ugc noopener noreferrer" }, [inline(m[1])]);
      } },
      { re: /<(https?:\/\/[^>\s]+)>/, make: function (m) { return el("a", { href: m[1], rel: "nofollow ugc noopener noreferrer", text: m[1] }); } },
      { re: /\*\*([^*]+)\*\*|__([^_]+)__/, make: function (m) { return el("strong", null, [inline(m[1] || m[2])]); } },
      { re: /~~([^~]+)~~/, make: function (m) { return el("del", null, [inline(m[1])]); } },
      { re: /\*([^*\s][^*]*)\*|(?:^|\b)_([^_\s][^_]*)_(?:\b|$)/, make: function (m) { return el("em", null, [inline(m[1] || m[2])]); } }
    ];
    var rest = text;
    while (rest) {
      var best = null;
      patterns.forEach(function (p) {
        var m = p.re.exec(rest);
        if (m && (!best || m.index < best.m.index)) best = { m: m, p: p };
      });
      if (!best) { frag.appendChild(document.createTextNode(rest)); break; }
      if (best.m.index > 0) frag.appendChild(document.createTextNode(rest.slice(0, best.m.index)));
      frag.appendChild(best.p.make(best.m));
      rest = rest.slice(best.m.index + best.m[0].length);
    }
    return frag;
  }

  function splitRow(line) {
    var cells = [];
    var cell = "";
    var s = line.trim().replace(/^\|/, "");
    var inCode = false;
    for (var k = 0; k < s.length; k++) {
      if (s[k] === "`") inCode = !inCode;
      if (s[k] === "\\" && s[k + 1] === "|") { cell += "|"; k++; }
      else if (s[k] === "|" && !inCode) { cells.push(cell.trim()); cell = ""; }
      else cell += s[k];
    }
    if (cell.trim()) cells.push(cell.trim());
    return cells;
  }

  function markdown(text, depth) {
    depth = depth || 0;
    var frag = document.createDocumentFragment();
    if (depth > 4) { frag.appendChild(el("p", { text: text })); return frag; }
    var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    var i = 0;
    var listRe = /^(\s{0,3})([-*+]|\d{1,9}[.)])\s+(.*)$/;
    function isBlockStart(line) {
      return /^\s{0,3}(#{1,6}\s|```|~~~|>|([-*_])(\s*\2){2,}\s*$)/.test(line) || listRe.test(line);
    }
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }
      var fence = line.match(/^\s{0,3}(```|~~~)\s*([\w+-]*)/);
      if (fence) {
        var code = [];
        i++;
        while (i < lines.length && lines[i].trim().indexOf(fence[1]) !== 0) { code.push(lines[i]); i++; }
        i++;
        var codeEl = el("code", { text: code.join("\n") });
        frag.appendChild(el("pre", { className: fence[2] === "tb" ? "md-code tb-code" : "md-code" }, [codeEl]));
        continue;
      }
      var heading = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (heading) {
        var level = Math.min(6, heading[1].length + 1);
        frag.appendChild(el("h" + level, null, [inline(heading[2])]));
        i++;
        continue;
      }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { frag.appendChild(el("hr")); i++; continue; }
      if (/^\s{0,3}>/.test(line)) {
        var quote = [];
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) { quote.push(lines[i].replace(/^\s{0,3}>\s?/, "")); i++; }
        frag.appendChild(el("blockquote", null, [markdown(quote.join("\n"), depth + 1)]));
        continue;
      }
      if (line.indexOf("|") >= 0 && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        var head = splitRow(line);
        i += 2;
        var tbody = el("tbody");
        while (i < lines.length && lines[i].indexOf("|") >= 0 && lines[i].trim()) {
          var cells = splitRow(lines[i]);
          tbody.appendChild(el("tr", null, head.map(function (_, k) { return el("td", null, [inline(cells[k] || "")]); })));
          i++;
        }
        var table = el("table", null, [el("thead", null, [el("tr", null, head.map(function (h) { return el("th", { scope: "col" }, [inline(h)]); }))]), tbody]);
        frag.appendChild(el("div", { className: "registry-table-wrap" }, [table]));
        continue;
      }
      var item = line.match(listRe);
      if (item) {
        var ordered = /\d/.test(item[2]);
        var list = el(ordered ? "ol" : "ul");
        while (i < lines.length) {
          var m = lines[i].match(listRe);
          if (!m || /\d/.test(m[2]) !== ordered || m[1].length > item[1].length + 1) break;
          var body = [m[3]];
          i++;
          while (i < lines.length && lines[i].trim()) {
            var next = lines[i];
            var nm = next.match(listRe);
            if (nm && nm[1].length <= m[1].length + 1) break; // the next item
            if (/^\s{2,}/.test(next)) { body.push(next.replace(/^\s{2,4}/, "")); i++; continue; } // nested
            if (isBlockStart(next)) break;
            body.push(next); // a lazy continuation line
            i++;
          }
          var li = el("li");
          if (body.length === 1) li.appendChild(inline(body[0]));
          else li.appendChild(markdown(body.join("\n"), depth + 1));
          list.appendChild(li);
          while (i < lines.length && !lines[i].trim() && i + 1 < lines.length && listRe.test(lines[i + 1])) i++;
        }
        frag.appendChild(list);
        continue;
      }
      var para = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) { para.push(lines[i].trim()); i++; }
      if (!para.length) { para.push(line.trim()); i++; }
      frag.appendChild(el("p", null, [inline(para.join(" "))]));
    }
    return frag;
  }

  // ------------------------------------------------------------ list page

  function card(name, p) {
    var meta = el("p", { className: "registry-card-meta" }, [el("span", { text: "by " + (p.owner || "tb") })]);
    if (typeof p.downloads === "number") meta.appendChild(el("span", { text: num(p.downloads) + (p.downloads === 1 ? " download" : " downloads") }));
    var tags = (p.keywords || []).length ? el("ul", { className: "registry-tags", "aria-label": "Keywords" }, p.keywords.map(function (k) { return el("li", { text: k }); })) : null;
    return el("li", { className: "pkg-card registry-card" }, [
      el("h3", null, [el("a", { href: viewHref(name) }, [el("code", { text: name })]), " ", el("span", { className: "pkg-version", text: p.version || "" })]),
      el("p", { text: p.description || "" }),
      meta,
      tags
    ]);
  }

  function listPage() {
    var list = $("[data-registry-list]");
    var status = $("[data-registry-status]");
    var input = $("#pkg-q");
    var form = $("[data-registry-search]");
    var all = null;
    var live = false;

    function draw(entries, query) {
      clear(list);
      entries.forEach(function (e) { list.appendChild(card(e.name, e)); });
      var count = entries.length + (entries.length === 1 ? " package" : " packages");
      status.textContent = query ? count + " matching “" + query + "”" : count;
      if (!entries.length && query) list.appendChild(el("li", { className: "registry-empty", text: "Nothing matches. Try another word, or publish the package you were looking for." }));
    }
    function sorted(map) {
      return Object.keys(map).map(function (n) { var p = map[n]; p.name = n; return p; }).sort(function (a, b) {
        return (b.downloads || 0) - (a.downloads || 0) || (a.name < b.name ? -1 : 1);
      });
    }
    function localSearch(query) {
      var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
      return sorted(all).filter(function (p) {
        return terms.every(function (t) {
          return p.name.indexOf(t) >= 0 || (p.description || "").toLowerCase().indexOf(t) >= 0 ||
            (p.keywords || []).some(function (k) { return k.indexOf(t) >= 0; });
        });
      });
    }
    var pending = 0;
    function search(query) {
      query = query.trim();
      if (history.replaceState) history.replaceState(null, "", query ? "?q=" + encodeURIComponent(query) : location.pathname);
      if (!all) return;
      if (!query) { draw(sorted(all)); return; }
      if (!live) { draw(localSearch(query), query); return; }
      var ticket = ++pending;
      api("search?q=" + encodeURIComponent(query)).then(function (data) {
        if (ticket === pending) draw(data.results || [], query);
      }, function () { if (ticket === pending) draw(localSearch(query), query); });
    }
    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { search(input.value); }, 250);
    });
    form.addEventListener("submit", function (event) { event.preventDefault(); search(input.value); });

    api("index").then(function (data) {
      all = data.packages || {};
      live = true;
    }, function () {
      return bundled().then(function (packages) { all = packages; }, function () { all = null; });
    }).then(function () {
      var q = new URLSearchParams(location.search).get("q") || "";
      if (q) input.value = q;
      if (all) search(q);
    });
  }

  // ------------------------------------------------------------ package page

  function viewPage() {
    var name = new URLSearchParams(location.search).get("name") || "";
    var title = $("[data-pkg-title]");
    var description = $("[data-pkg-description]");
    if (!/^[a-z][a-z0-9-]{1,38}[a-z0-9]$/.test(name)) {
      title.textContent = "No such package";
      description.textContent = "The address doesn't name a package.";
      return;
    }
    title.textContent = name;
    document.title = name + " · TB Package Index";

    function fromBundled() {
      return bundled().then(function (packages) {
        var p = packages[name];
        if (!p) throw new ApiError("There's no package called '" + name + "'.", 404);
        return {
          name: name, description: p.description, owner: p.owner, latest: p.version, keywords: p.keywords,
          license: p.license, downloads: null, readme: p.readme, status: "approved",
          versions: [{ version: p.version, status: "approved", sha256: p.versions[p.version].sha256, created: p.created, downloads: null, dependencies: p.dependencies }]
        };
      });
    }

    api("packages/" + encodeURIComponent(name)).catch(function (err) {
      if (err.status === 0) return fromBundled();
      throw err;
    }).then(function (p) {
      description.textContent = p.description || "";
      var keywords = $("[data-pkg-keywords]");
      (p.keywords || []).forEach(function (k) { keywords.appendChild(el("li", null, [el("a", { href: "../packages.html?q=" + encodeURIComponent(k), text: k })])); });
      if (p.status && p.status !== "approved") message("This package is " + p.status + ": only you and the index admins can see it.", false);
      $("[data-pkg-install]").textContent = "tb add " + name;
      $("[data-pkg-import]").textContent = 'import "' + name + '" as ' + importName(name);
      var latest = (p.versions || []).filter(function (v) { return v.version === p.latest; })[0] || (p.versions || [])[0];
      var facts = $("[data-pkg-facts]");
      function fact(label, value) {
        if (value === null || value === undefined || value === "") return;
        facts.appendChild(el("dt", { text: label }));
        facts.appendChild(el("dd", null, [value]));
      }
      fact("Latest", p.latest || "none (every version is yanked)");
      fact("Owner", p.owner || "");
      fact("License", p.license || "not given");
      if (typeof p.downloads === "number") fact("Downloads", num(p.downloads));
      if (latest && latest.dependencies && Object.keys(latest.dependencies).length) {
        fact("Needs", Object.keys(latest.dependencies).map(function (d) {
          var v = latest.dependencies[d];
          return d + " " + (typeof v === "string" ? v : (v.version || v.git || v.zip || ""));
        }).join(", "));
      }
      if (latest && latest.sha256) fact("sha256 (" + latest.version + ")", el("code", { className: "registry-hash", text: latest.sha256 }));

      var rows = $("[data-pkg-versions]");
      (p.versions || []).forEach(function (v) {
        var label = [el("code", { text: v.version })];
        if (v.status === "yanked") label.push(" ", el("span", { className: "registry-badge registry-badge-warn", title: v.yankReason || "", text: "yanked" }));
        else if (v.status && v.status !== "approved") label.push(" ", el("span", { className: "registry-badge", text: v.status }));
        rows.appendChild(el("tr", null, [
          el("th", { scope: "row" }, label),
          el("td", { text: v.created ? day(v.created) : "" }),
          el("td", { text: typeof v.downloads === "number" ? num(v.downloads) : "–" })
        ]));
      });
      var readme = $("[data-pkg-readme]");
      if (p.readme) readme.appendChild(markdown(p.readme));
      else readme.appendChild(el("p", { className: "registry-empty", text: "This package has no README." }));
      if (window.TBHighlight && typeof window.TBHighlight.highlightAll === "function") window.TBHighlight.highlightAll(readme);
      show($("[data-pkg-body]"), true);
      reportForm(name, p.latest);
    }, function (err) {
      title.textContent = err.status === 404 ? "No such package" : name;
      description.textContent = err.status === 404 ? "There's no package called “" + name + "” in the index." : err.message;
    });
  }

  function reportForm(name, version) {
    var form = $("[data-report-form]");
    if (!form) return;
    api("me").then(function (me) {
      if (!me.signedIn) return;
      show($("[data-report-signin]"), false);
      show(form, true);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var status = $("[data-form-status]", form);
        status.textContent = "Sending…";
        api("packages/" + encodeURIComponent(name) + "/report", { method: "POST", body: { reason: form.reason.value, version: version } })
          .then(function (r) { status.textContent = r.message; form.reason.value = ""; }, function (err) { status.textContent = err.message; });
      });
    }, function () { /* no API: the sign-in hint stays */ });
  }

  // ------------------------------------------------------------ signed-in pages

  var ERRORS = {
    "signin-unavailable": "Sign-in isn't switched on yet. Check back soon.",
    state: "That sign-in link expired or came from somewhere else. Please sign in again.",
    denied: "Sign-in was cancelled.",
    github: "GitHub didn't confirm the sign-in. Please try again.",
    unavailable: "The package index couldn't finish signing you in. Please try again in a minute."
  };

  /** Show the right data-auth-* block; resolves with /me when signed in. */
  function auth() {
    var blocks = {};
    ["unknown", "offline", "unavailable", "out", "in"].forEach(function (k) { blocks[k] = $all("[data-auth-" + k + "]"); });
    function only(k) { Object.keys(blocks).forEach(function (b) { blocks[b].forEach(function (n) { show(n, b === k); }); }); }
    return api("me").then(function (me) {
      if (me.signedIn) {
        only("in");
        $all("[data-user-login]").forEach(function (n) { n.textContent = me.user.login; });
        $all("[data-sign-out]").forEach(function (b) {
          b.addEventListener("click", function () {
            api("auth/logout", { method: "POST", body: {} }).then(function () { location.reload(); }, function () { location.reload(); });
          });
        });
        return me;
      }
      only(me.setup && me.setup.signIn === false ? "unavailable" : "out");
      return null;
    }, function () { only("offline"); return null; });
  }

  function publishPage() {
    var code = new URLSearchParams(location.search).get("error");
    if (code) {
      var box = $("[data-registry-error]");
      box.textContent = ERRORS[code] || "Sign-in didn't work. Please try again.";
      show(box, true);
    }
    auth().then(function (me) {
      if (!me) return;
      var list = $("[data-token-list]");
      function load() {
        api("tokens").then(function (data) {
          clear(list);
          if (!data.tokens.length) { list.appendChild(el("li", { className: "registry-empty", text: "No tokens yet." })); return; }
          data.tokens.forEach(function (t) {
            var button = el("button", { className: "link-button", type: "button", text: "Revoke" });
            button.addEventListener("click", function () {
              button.disabled = true;
              api("tokens/" + encodeURIComponent(t.id), { method: "DELETE" }).then(load, function (err) { button.disabled = false; alert(err.message); });
            });
            list.appendChild(el("li", null, [el("strong", { text: t.name }), " ", el("span", { className: "registry-muted", text: "made " + day(t.created) }), " ", button]));
          });
        });
      }
      load();
      var form = $("[data-token-form]");
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var status = $("[data-form-status]", form);
        status.textContent = "";
        api("tokens", { method: "POST", body: { name: form.name.value } }).then(function (data) {
          $("[data-new-token-value]").textContent = data.token;
          show($("[data-new-token]"), true);
          form.name.value = "";
          load();
        }, function (err) { status.textContent = err.message; });
      });
      var copy = $("[data-copy-token]");
      copy.addEventListener("click", function () {
        var value = $("[data-new-token-value]").textContent;
        if (navigator.clipboard) navigator.clipboard.writeText(value).then(function () { copy.textContent = "Copied"; });
      });
    });
  }

  function badge(status) {
    var cls = { approved: "registry-badge-ok", pending: "registry-badge-wait", rejected: "registry-badge-bad", yanked: "registry-badge-warn" }[status] || "";
    var label = { approved: "published", pending: "waiting for review" }[status] || status;
    return el("span", { className: "registry-badge " + cls, text: label });
  }

  function accountPage() {
    auth().then(function (me) {
      if (!me) return;
      var box = $("[data-account-list]");
      function load() {
        api("account/packages").then(function (data) {
          clear(box);
          if (!data.packages.length) {
            box.appendChild(el("p", null, ["You haven't published anything yet. ", el("a", { href: "publish.html", text: "How to publish" }), "."]));
            return;
          }
          data.packages.forEach(function (p) {
            var rows = el("tbody");
            p.versions.forEach(function (v) {
              var actions = el("td");
              if (v.status === "approved" || (v.status === "yanked" && v.yankedBy !== "admin")) {
                var yank = v.status === "approved";
                var button = el("button", { className: "link-button", type: "button", text: yank ? "Yank" : "Undo yank" });
                button.addEventListener("click", function () {
                  var reason = yank ? window.prompt("Why are you yanking " + p.name + " " + v.version + "? (shown to users)", "") : "";
                  if (reason === null) return;
                  api("packages/" + encodeURIComponent(p.name) + "/" + encodeURIComponent(v.version) + "/" + (yank ? "yank" : "unyank"), { method: "POST", body: { reason: reason } })
                    .then(load, function (err) { message(err.message, true); });
                });
                actions.appendChild(button);
              }
              var note = v.reason || v.yankReason;
              rows.appendChild(el("tr", null, [
                el("th", { scope: "row" }, [el("code", { text: v.version })]),
                el("td", null, [badge(v.status), note ? el("span", { className: "registry-reason", text: " " + note }) : null]),
                el("td", { text: day(v.created) }),
                actions
              ]));
            });
            var heading = p.status === "approved" ? el("a", { href: viewHref(p.name) }, [el("code", { text: p.name })]) : el("code", { text: p.name });
            box.appendChild(el("section", { className: "registry-panel" }, [
              el("h2", null, [heading, " ", badge(p.status)]),
              p.description ? el("p", { text: p.description }) : null,
              el("div", { className: "registry-table-wrap" }, [el("table", { className: "registry-table" }, [
                el("thead", null, [el("tr", null, ["Version", "Status", "Uploaded", ""].map(function (h) { return el("th", { scope: "col", text: h }); }))]),
                rows
              ])])
            ]));
          });
        }, function (err) { message(err.message, true); });
      }
      load();
    });
  }

  function adminPage() {
    auth().then(function (me) {
      if (!me) return;
      if (!me.admin) { message("You're signed in, but you aren't an index admin.", true); return; }
      show($("[data-admin-body]"), true);
      var queueBox = $("[data-admin-queue]");
      var reportsBox = $("[data-admin-reports]");
      var auditBox = $("[data-admin-audit]");
      function act(path, body) {
        return api(path, { method: "POST", body: body || {} }).then(function () { load(); }, function (err) { message(err.message, true); });
      }
      function load() {
        api("admin/queue").then(function (data) {
          clear(queueBox); clear(reportsBox); clear(auditBox);
          if (!data.queue.length) queueBox.appendChild(el("p", { className: "registry-empty", text: "Nothing is waiting. ✓" }));
          data.queue.forEach(function (item) {
            var reason = el("input", { type: "text", maxlength: "500", placeholder: "Reason (shown to the author)", "aria-label": "Reason for rejecting " + item.name + " " + item.version });
            var approve = el("button", { className: "button button-primary", type: "button", text: "Approve" });
            var reject = el("button", { className: "button", type: "button", text: "Reject" });
            var path = "admin/packages/" + encodeURIComponent(item.name) + "/" + encodeURIComponent(item.version) + "/";
            approve.addEventListener("click", function () { approve.disabled = true; act(path + "approve"); });
            reject.addEventListener("click", function () {
              if (reason.value.trim().length < 3) { reason.focus(); message("Give the author a reason first.", true); return; }
              reject.disabled = true;
              act(path + "reject", { reason: reason.value });
            });
            var readme = el("details", { className: "registry-admin-readme" }, [el("summary", { text: "README" }), el("div", { className: "md-page" }, [markdown(item.readme || "(none)")])]);
            var deps = Object.keys(item.dependencies || {}).map(function (d) { var v = item.dependencies[d]; return d + " " + (typeof v === "string" ? v : JSON.stringify(v)); }).join(", ");
            queueBox.appendChild(el("article", { className: "registry-panel registry-review" }, [
              el("h3", null, [el("code", { text: item.name + "@" + item.version }), " ", item.newPackage ? el("span", { className: "registry-badge registry-badge-wait", text: "new package" }) : null]),
              el("p", { text: item.description || "(no description)" }),
              item.flags && item.flags.length ? el("ul", { className: "registry-flags" }, item.flags.map(function (f) { return el("li", { text: f }); })) : null,
              el("dl", { className: "registry-facts" }, [
                el("dt", { text: "Owner" }), el("dd", { text: item.owner + (item.publisher && item.publisher !== item.owner ? " (uploaded by " + item.publisher + ")" : "") }),
                el("dt", { text: "Uploaded" }), el("dd", { text: day(item.created) }),
                el("dt", { text: "Size" }), el("dd", { text: num(item.size) + " bytes, " + item.files.length + " files, entry " + item.entry }),
                el("dt", { text: "License" }), el("dd", { text: item.license || "not given" }),
                el("dt", { text: "Needs" }), el("dd", { text: deps || "nothing" }),
                el("dt", { text: "sha256" }), el("dd", null, [el("code", { className: "registry-hash", text: item.sha256 })])
              ]),
              el("details", null, [el("summary", { text: "Files" }), el("ul", { className: "registry-files" }, item.files.map(function (f) { return el("li", null, [el("code", { text: f })]); }))]),
              readme,
              el("p", null, [el("a", { href: API + "packages/" + encodeURIComponent(item.name) + "/" + encodeURIComponent(item.version) + "/download", text: "Download the zip to inspect it" })]),
              el("div", { className: "registry-review-actions" }, [approve, reason, reject])
            ]));
          });
          if (!data.reports.length) reportsBox.appendChild(el("p", { className: "registry-empty", text: "No open reports." }));
          data.reports.forEach(function (r) {
            var dismiss = el("button", { className: "link-button", type: "button", text: "Dismiss" });
            dismiss.addEventListener("click", function () { act("admin/reports/" + encodeURIComponent(r.id) + "/dismiss"); });
            reportsBox.appendChild(el("div", { className: "registry-panel" }, [
              el("p", null, [el("a", { href: viewHref(r.name) }, [el("code", { text: r.name + (r.version ? "@" + r.version : "") })]), " reported by " + r.by + " on " + day(r.at)]),
              el("blockquote", { text: r.reason }),
              dismiss
            ]));
          });
          data.audit.forEach(function (a) {
            auditBox.appendChild(el("li", { text: day(a.at) + ": " + a.by + " " + a.action + " " + a.name + (a.version ? "@" + a.version : "") + (a.reason ? " (" + a.reason + ")" : "") }));
          });
        }, function (err) { message(err.message, true); });
      }
      load();
      var form = $("[data-admin-action]");
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var name = form.name.value.trim();
        var version = form.version.value.trim();
        var action = form.action.value;
        var status = $("[data-form-status]", form);
        var path;
        if (action === "remove-package") path = "admin/packages/" + encodeURIComponent(name) + "/remove";
        else if (!version) { status.textContent = "Give a version."; return; }
        else if (action === "remove-version") path = "admin/packages/" + encodeURIComponent(name) + "/" + encodeURIComponent(version) + "/remove";
        else path = "packages/" + encodeURIComponent(name) + "/" + encodeURIComponent(version) + "/" + action;
        if (action.indexOf("remove") === 0 && !window.confirm("Really " + action.replace("-", " the ") + " " + name + (version && action !== "remove-package" ? "@" + version : "") + "? This can't be undone.")) return;
        api(path, { method: "POST", body: { reason: form.reason.value } }).then(function (r) {
          status.textContent = r.message || "Done.";
          load();
        }, function (err) { status.textContent = err.message; });
      });
    });
  }

  var pages = { list: listPage, view: viewPage, publish: publishPage, account: accountPage, admin: adminPage };
  if (pages[kind]) pages[kind]();
  window.TBRegistry = { markdown: markdown };
})();

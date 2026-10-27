// Site statistics: counts this page view (no cookies, nothing about you is
// stored) and fills in the visit and download numbers.
//
//   data-stat="visits" | "downloads" | "dl:<asset file name>"   gets the number
//   data-stat-wrap                                            shown once its number arrives
//   data-stats                                                shown once any number inside arrives
//
// Everything starts hidden, so with no API (local previews, tests) the page
// simply has no numbers. Local previews never count or ask: add ?stats to
// the address to fetch the numbers anyway (e.g. under `vercel dev`).
(function () {
  "use strict";
  if (!window.fetch) return;

  var host = location.hostname;
  var local = location.protocol === "file:" || host === "localhost" || host === "127.0.0.1" ||
    host === "[::1]" || host === "" || /\.localhost$/.test(host);
  var robot = !!navigator.webdriver;

  function countView() {
    if (robot || local) return Promise.resolve();
    return fetch("/api/hit", {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: location.pathname })
    }).catch(function () { /* counting is best effort */ });
  }

  var format = window.Intl && Intl.NumberFormat ? new Intl.NumberFormat(undefined) : null;
  function show(n) { return format ? format.format(n) : String(n); }

  function valueFor(key, stats) {
    if (key === "visits") return stats.visits;
    var dl = stats.downloads;
    if (!dl || typeof dl !== "object") return null;
    if (key === "downloads") return dl.total;
    if (key.indexOf("dl:") === 0) return dl.assets ? dl.assets[key.slice(3)] : null;
    return null;
  }

  function fill(stats) {
    Array.prototype.forEach.call(document.querySelectorAll("[data-stat]"), function (el) {
      var n = valueFor(el.getAttribute("data-stat"), stats || {});
      if (typeof n !== "number" || !isFinite(n) || n < 0) return;
      el.textContent = show(n);
      var wrap = el.closest("[data-stat-wrap]");
      if (wrap) wrap.hidden = false;
      var box = el.closest("[data-stats]");
      if (box) box.hidden = false;
    });
  }

  var forced = /[?&]stats\b/.test(location.search);
  countView().then(function () {
    if ((local && !forced) || !document.querySelector("[data-stat]")) return null;
    return fetch("/api/stats", { headers: { Accept: "application/json" } }).then(function (r) {
      return r.ok ? r.json() : null;
    });
  }).then(function (stats) {
    if (stats) fill(stats);
  }).catch(function () { /* no numbers: the page looks the same without them */ });
})();

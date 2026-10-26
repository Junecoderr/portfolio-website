// Light / dark mode, plus the small bits of page chrome every page shares
// (mobile menu, version badge, current nav link). Runs in <head> so the page
// never flashes the wrong theme. With no saved choice we follow the system
// setting (prefers-color-scheme).
(function () {
  var KEY = "tb-theme";
  var root = document.documentElement;
  root.classList.add("js");

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function save(value) {
    try { localStorage.setItem(KEY, value); } catch (e) { /* private mode: fine */ }
  }
  function systemDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  function current() {
    var t = root.getAttribute("data-theme");
    if (t === "light" || t === "dark") return t;
    return systemDark() ? "dark" : "light";
  }

  var initial = saved();
  if (initial === "light" || initial === "dark") root.setAttribute("data-theme", initial);

  var SUN = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/></svg>';
  var MOON = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="currentColor"><path d="M20.5 14.6A8.6 8.6 0 0 1 9.4 3.5a.6.6 0 0 0-.8-.7A9.6 9.6 0 1 0 21.2 15.4a.6.6 0 0 0-.7-.8z"/></svg>';

  function paint(button) {
    var dark = current() === "dark";
    // Show what you will switch TO.
    button.innerHTML = dark ? SUN : MOON;
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.title = dark ? "Switch to light mode" : "Switch to dark mode";
  }

  function setupMenu() {
    var header = document.querySelector(".site-header");
    var toggle = document.querySelector(".nav-toggle");
    if (!header || !toggle) return;
    function set(open) {
      header.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    toggle.addEventListener("click", function () { set(!header.classList.contains("nav-open")); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && header.classList.contains("nav-open")) { set(false); toggle.focus(); }
    });
    var links = header.querySelectorAll(".site-nav a");
    for (var i = 0; i < links.length; i++) links[i].addEventListener("click", function () { set(false); });
  }

  function fillVersion() {
    var cfg = window.TB_CONFIG;
    if (!cfg || !cfg.version) return;
    var els = document.querySelectorAll("[data-version]");
    for (var i = 0; i < els.length; i++) els[i].textContent = cfg.version;
  }

  function markCurrentNav() {
    var nav = document.querySelector(".site-nav");
    if (!nav || nav.querySelector('[aria-current="page"]')) return;
    var page = location.pathname.split("/").pop() || "index.html";
    var links = nav.querySelectorAll("a");
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute("href") === page) links[i].setAttribute("aria-current", "page");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    markCurrentNav();
    setupMenu();
    fillVersion();
    var buttons = document.querySelectorAll(".theme-toggle");
    for (var i = 0; i < buttons.length; i++) {
      (function (button) {
        paint(button);
        button.addEventListener("click", function () {
          var next = current() === "dark" ? "light" : "dark";
          root.setAttribute("data-theme", next);
          save(next);
          for (var j = 0; j < buttons.length; j++) paint(buttons[j]);
        });
      })(buttons[i]);
    }
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () { for (var j = 0; j < buttons.length; j++) paint(buttons[j]); };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  });
})();

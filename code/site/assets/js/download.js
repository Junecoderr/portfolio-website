// Download page: point every link at the GitHub release for this version (tag tb-vX.Y.Z), show the
// file for this computer first, and open the matching install steps.
(function () {
  "use strict";
  var cfg = window.TB_CONFIG || { repo: "OWNER/REPO", version: "1.0.0" };
  var base = "https://github.com/" + cfg.repo + "/releases/download/tb-v" + cfg.version + "/";

  var OS = {
    windows: { name: "Windows", file: "tb-windows-x64.exe", note: "For Windows 10 and 11 laptops (64-bit).", tab: "windows" },
    mac: { name: "Mac", file: "tb-macos-arm64.zip", note: "For Macs with Apple silicon (M1 and newer). On an Intel Mac, use the Python version below.", tab: "mac" },
    linux: { name: "Linux", file: "tb-linux-x64.tar.gz", note: "For 64-bit Intel or AMD computers.", tab: "linux" }
  };

  function detect() {
    var uad = navigator.userAgentData;
    var platform = (uad && uad.platform) || "";
    var ua = navigator.userAgent || "";
    var mobile = (uad && uad.mobile) || /Android|iPhone|iPad|iPod|Mobile/i.test(ua) ||
      (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    var s = (platform + " " + ua).toLowerCase();
    var os = "windows";
    if (/android|cros|chrome os/.test(s)) os = "windows";
    else if (/mac|iphone|ipad|ipod/.test(s)) os = "mac";
    else if (/linux|x11/.test(s)) os = "linux";
    else if (/win/.test(s)) os = "windows";
    return { os: os, mobile: !!mobile };
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  $all("[data-download]").forEach(function (a) { a.href = base + a.getAttribute("data-download"); });
  $all("[data-version]").forEach(function (e) { e.textContent = cfg.version; });

  var found = detect();
  var info = OS[found.os];
  var link = $("[data-primary-link]");
  if (link) {
    link.setAttribute("data-download", info.file);
    link.href = base + info.file;
    $("[data-primary-file]").textContent = info.file;
  }
  var nameEl = $("[data-os-name]");
  if (nameEl) nameEl.textContent = info.name;
  var noteEl = $("[data-os-note]");
  if (noteEl) noteEl.textContent = info.note + " Version " + cfg.version + ".";
  if (found.mobile) {
    var m = $("[data-mobile-note]");
    if (m) m.hidden = false;
  }

  // Accessible tabs for the install steps (all panels stay visible without JS).
  var tablist = $(".os-tabs");
  if (!tablist) return;
  var tabs = $all('[role="tab"]', tablist);
  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { select(t, false); });
    t.addEventListener("keydown", function (e) {
      var j = null;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = tabs.length - 1;
      if (j !== null) { e.preventDefault(); select(tabs[j], true); }
    });
  });
  tablist.hidden = false;
  select(document.getElementById("tab-" + info.tab), false);

  // Links like #python or #verify inside a hidden panel are fine; links to a
  // panel itself (#panel-mac) open that tab.
  function fromHash() {
    var m = /^#panel-(windows|mac|linux)$/.exec(location.hash);
    if (m) select(document.getElementById("tab-" + m[1]), false);
  }
  window.addEventListener("hashchange", fromHash);
  fromHash();
})();

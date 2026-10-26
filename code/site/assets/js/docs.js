// Learn page: collapsible contents on small screens, an "On this page" list,
// and highlighting of the section you are reading.
(function () {
  "use strict";

  // Contents toggle (mobile).
  var sidebar = document.querySelector(".docs-sidebar");
  var toggle = document.querySelector(".docs-nav-toggle");
  if (sidebar && toggle) {
    toggle.addEventListener("click", function () {
      var open = !sidebar.classList.contains("open");
      sidebar.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    sidebar.addEventListener("click", function (e) {
      if (e.target.closest("a") && window.matchMedia("(max-width: 59.99rem)").matches) {
        sidebar.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  // "On this page": every section heading, with the reference cards nested.
  var main = document.querySelector(".docs-main");
  var list = document.getElementById("on-this-page-list");
  var sections = main ? Array.prototype.slice.call(main.querySelectorAll(":scope > section[id]")) : [];
  if (list) {
    sections.forEach(function (sec) {
      var h = sec.querySelector("h2");
      if (!h) return;
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#" + sec.id;
      a.textContent = h.textContent.replace(/^\d+\.\s*/, function (m) { return m.trim() + " "; });
      li.appendChild(a);
      list.appendChild(li);
      if (sec.id === "reference") {
        var cards = sec.querySelectorAll(".sig[id]");
        for (var i = 0; i < cards.length; i++) {
          var sli = document.createElement("li");
          sli.className = "sub";
          var sa = document.createElement("a");
          sa.href = "#" + cards[i].id;
          sa.textContent = cards[i].querySelector(".sig-name").textContent;
          sli.appendChild(sa);
          list.appendChild(sli);
        }
      }
    });
  }

  // Scroll spy.
  if (!("IntersectionObserver" in window) || !sections.length) return;
  var links = Array.prototype.slice.call(document.querySelectorAll('.docs-sidebar a[href^="#"], .on-this-page a[href^="#"]'));
  function mark(id) {
    links.forEach(function (a) {
      var on = a.getAttribute("href") === "#" + id;
      a.classList.toggle("is-active", on);
      if (on) a.setAttribute("aria-current", "location"); else a.removeAttribute("aria-current");
    });
  }
  var visible = {};
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
    for (var i = 0; i < sections.length; i++) {
      if (visible[sections[i].id]) { mark(sections[i].id); return; }
    }
  }, { rootMargin: "-80px 0px -60% 0px" });
  sections.forEach(function (s) { observer.observe(s); });
})();

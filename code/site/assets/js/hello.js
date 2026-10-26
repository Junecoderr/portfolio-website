// The one moment of motion: the greeting "prints" itself like program output.
(function () {
  var h = document.getElementById("hello");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var text = h.textContent;
  if (reduce) return;
  h.setAttribute("aria-label", text);
  var inner = document.createElement("span");
  inner.setAttribute("aria-hidden", "true");
  for (var i = 0; i < text.length; i++) {
    var s = document.createElement("span");
    s.className = "ch";
    s.textContent = text[i];
    s.style.animationDelay = (0.25 + i * 0.045) + "s";
    inner.appendChild(s);
  }
  var caret = document.createElement("span");
  caret.className = "caret";
  inner.appendChild(caret);
  h.textContent = "";
  h.appendChild(inner);
})();

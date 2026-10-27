// The "Visualize" drop-downs on the library reference (scripts/visuals/).
// * A picture kept in a <template> is put in place the first time its
//   drop-down opens, so long pages stay light.
// * Sliders: <input type="range" data-fv-param="width"> sets a value; parts
//   of the drawing follow it through data-fv-bind (JSON list of operations)
//   or by showing the matching pre-drawn variant (data-fv-variant="p:v").
// * Legend chips are buttons: a click pins the highlight (aria-pressed);
//   hover and focus highlighting is plain CSS (visuals.css).
// No inline scripts or handlers: the site's CSP forbids them.
(function () {
  "use strict";

  var SVGNS = "http://www.w3.org/2000/svg";

  function fmt(v) {
    var n = Number(v);
    if (isNaN(n)) return String(v);
    return String(Math.round(n * 100) / 100);
  }

  function sum(base, ks, state) {
    var total = Number(base) || 0;
    for (var k in ks) {
      if (Object.prototype.hasOwnProperty.call(ks, k) && state[k] !== undefined) total += ks[k] * Number(state[k]);
    }
    return total;
  }

  function applyBind(el, ops, state) {
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      var kind = op[0];
      if (kind === "text") {
        el.textContent = op[1].replace(/\{(\w+)\}/g, function (m, k) { return state[k] === undefined ? m : fmt(state[k]); });
      } else if (kind === "translate") {
        el.setAttribute("transform", "translate(" + sum(op[1][0], op[1][1], state) + " " + sum(op[2][0], op[2][1], state) + ")");
      } else if (kind === "scale") {
        el.setAttribute("transform", "translate(" + op[1] + " " + op[2] + ") scale(" + Math.max(0.1, sum(0, op[3], state)) + ")");
      } else if (kind === "arc") {
        var cx = op[1], cy = op[2], r = op[3];
        var frac = Math.min(Math.max(sum(0, op[4], state), 0.001), 0.999);
        var ang = -Math.PI / 2 + frac * 2 * Math.PI;
        var ex = cx + r * Math.cos(ang), ey = cy + r * Math.sin(ang);
        el.setAttribute("d", "M" + cx + " " + (cy - r) + " A" + r + " " + r + " 0 " + (frac > 0.5 ? 1 : 0) + " 1 " + ex.toFixed(1) + " " + ey.toFixed(1));
      } else {
        el.setAttribute(kind, Math.max(kind === "width" || kind === "height" ? 0 : -1e9, sum(op[1], op[2], state)).toFixed(1));
      }
    }
  }

  function apply(fv) {
    var state = fv._fvState;
    var vars = fv.querySelectorAll("[data-fv-variant]");
    for (var i = 0; i < vars.length; i++) {
      var key = vars[i].getAttribute("data-fv-variant");
      var at = key.indexOf(":");
      var p = key.slice(0, at), v = key.slice(at + 1);
      if (state[p] === undefined) continue;
      var on = String(state[p]) === v;
      if (vars[i].namespaceURI === SVGNS) {
        if (on) vars[i].removeAttribute("display"); else vars[i].setAttribute("display", "none");
      } else {
        vars[i].hidden = !on;
      }
    }
    // A picture takes the height of the slider position on show.
    var shown = fv.querySelectorAll("g[data-fv-h]:not([display])");
    for (var h = 0; h < shown.length; h++) {
      var svg = shown[h].ownerSVGElement;
      if (svg) {
        var vb = svg.getAttribute("viewBox").split(" ");
        vb[3] = shown[h].getAttribute("data-fv-h");
        svg.setAttribute("viewBox", vb.join(" "));
      }
    }
    var binds = fv.querySelectorAll("[data-fv-bind]");
    for (var j = 0; j < binds.length; j++) {
      var el = binds[j];
      if (!el._fvOps) {
        try { el._fvOps = JSON.parse(el.getAttribute("data-fv-bind")); } catch (e) { el._fvOps = []; }
      }
      applyBind(el, el._fvOps, state);
    }
  }

  function readInput(input) {
    var values = input.getAttribute("data-fv-values");
    if (values) {
      var list = JSON.parse(values);
      var labels = input.getAttribute("data-fv-labels");
      var idx = Math.round(Number(input.value));
      return { value: list[idx], shown: labels ? JSON.parse(labels)[idx] : list[idx] };
    }
    return { value: Number(input.value), shown: fmt(input.value) };
  }

  function setup(details) {
    var fv = details.querySelector(":scope > .fv");
    if (!fv || fv._fvState) return;
    fv._fvState = {};
    var tpl = fv.querySelector("template.fv-tpl");
    if (tpl) {
      tpl.parentNode.insertBefore(tpl.content.cloneNode(true), tpl);
      var wait = tpl.parentNode.querySelector(".fv-wait");
      if (wait) wait.remove();
      tpl.remove();
    }
    var inputs = fv.querySelectorAll("input[data-fv-param]");
    Array.prototype.forEach.call(inputs, function (input) {
      var out = input.parentNode.querySelector("output");
      var unit = out ? out.textContent.replace(/^[-\d.,\w"#]+/, "") : "";
      function update() {
        var r = readInput(input);
        fv._fvState[input.getAttribute("data-fv-param")] = r.value;
        if (out) out.textContent = r.shown + unit;
        apply(fv);
      }
      var r0 = readInput(input);
      fv._fvState[input.getAttribute("data-fv-param")] = r0.value;
      if (out) out.textContent = r0.shown + unit;
      input.addEventListener("input", update);
      input.addEventListener("change", update);
    });
    fv.addEventListener("click", function (e) {
      var chip = e.target.closest(".fv-chip");
      if (!chip || !fv.contains(chip)) return;
      var on = chip.getAttribute("aria-pressed") !== "true";
      var chips = fv.querySelectorAll(".fv-chip");
      for (var i = 0; i < chips.length; i++) chips[i].setAttribute("aria-pressed", "false");
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
    apply(fv);
  }

  var all = document.querySelectorAll("details.fn-visual");
  Array.prototype.forEach.call(all, function (d) {
    if (d.open) setup(d);
    d.addEventListener("toggle", function () { if (d.open) setup(d); });
  });

  // A link to a widget or window method (#win-resize) opens its picture.
  function openFromHash() {
    var id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    var el = document.getElementById(id);
    if (el && el.matches("details.fn-visual")) {
      el.open = true;
      setup(el);
    }
  }
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
})();

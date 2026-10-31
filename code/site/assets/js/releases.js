// The Releases page: the "Copy address" button for the feed (scripts/changelog.py writes the page).
(function () {
  var button = document.querySelector("[data-copy-feed]");
  var field = document.querySelector("[data-feed-url]");
  var status = document.querySelector("[data-copy-status]");
  if (!button || !field) return;
  button.hidden = false;
  field.addEventListener("focus", function () { field.select(); });
  button.addEventListener("click", function () {
    function done(ok) {
      status.textContent = ok ? "Copied. Paste it into your feed reader." : "Select the address and copy it.";
      if (!ok) field.select();
    }
    function fallback() {
      // Older or locked-down browsers: copy the selected text the old way.
      field.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      done(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(field.value).then(function () { done(true); }, fallback);
    } else {
      fallback();
    }
  });
})();

/* Runs synchronously in <head>, before first paint, so there is no flash of the wrong theme and the
   intro splash can be decided before anything is shown. External file: the CSP forbids inline scripts.
   Everything is wrapped: storage can be blocked (private windows) and the page must still work. */
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var q = new URLSearchParams(location.search);
    var theme = q.get("theme");
    if (theme === "light" || theme === "dark") { localStorage.setItem("markbook.theme", theme); }
    else { theme = localStorage.getItem("markbook.theme"); }
    if (theme === "light" || theme === "dark") { root.setAttribute("data-theme", theme); }

    var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    var seen = sessionStorage.getItem("markbook.splash");
    var forced = q.get("splash") === "1";
    if (!reduce && (forced || (!seen && location.pathname === "/"))) { root.classList.add("splash-on"); }
  } catch (e) { /* storage blocked: default theme, no splash */ }
})();

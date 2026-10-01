/* Progressive enhancement only: every page works without JavaScript except live
   progress, which falls back to a manual reload. No inline scripts (strict CSP). */
(function () {
  "use strict";
  var KEY = "markbook.reviewer";
  function safeGet() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function safeSet(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* storage blocked: fine */ } }

  // Reviewer name: remembered per browser, stamped on every decision for the audit log.
  var nameInput = document.getElementById("reviewer-name");
  if (nameInput) {
    nameInput.value = safeGet();
    nameInput.addEventListener("input", function () { safeSet(nameInput.value.trim()); });
  }
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { ev.preventDefault(); return; }
    var name = (nameInput && nameInput.value.trim()) || safeGet() || "web";
    form.querySelectorAll(".reviewer-field").forEach(function (f) { f.value = name; });
  });

  // Record that this reviewer opened this submission (same-origin, name + ids + server time only);
  // `markbook stats` uses it as a session boundary. Failure is silent and harmless.
  var beacon = document.getElementById("view-beacon");
  if (beacon && window.fetch) {
    var who = (nameInput && nameInput.value.trim()) || safeGet() || "web";
    fetch(beacon.dataset.url, { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "reviewer=" + encodeURIComponent(who.slice(0, 60)) }).catch(function () {});
  }

  // j / k move through submissions, unless the user is typing.
  document.addEventListener("keydown", function (ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var t = ev.target && ev.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
    var link = ev.key === "j" ? document.getElementById("next") : ev.key === "k" ? document.getElementById("prev") : null;
    if (link) { window.location.href = link.href; }
  });

  // Live progress for a running grade.
  var box = document.getElementById("progress");
  if (box) {
    var count = document.getElementById("count"), bar = document.getElementById("bar"), active = document.getElementById("active");
    (function poll() {
      fetch(box.dataset.url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.state === "done" || s.state === "failed") { window.location.href = box.dataset.doneUrl; return; }
          count.textContent = s.done + " / " + s.total;
          bar.max = s.total; bar.value = s.done;
          active.textContent = s.active && s.active.length ? "Working on: " + s.active.join(", ") : "Finishing up…";
          setTimeout(poll, 1000);
        })
        .catch(function () { setTimeout(poll, 3000); });
    })();
  }
})();

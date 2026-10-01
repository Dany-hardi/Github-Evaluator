/* Markbook web UI: progressive enhancement only. Every page works without JavaScript except live
   progress (which falls back to a manual reload). No inline scripts or styles (strict CSP). */
(function () {
  "use strict";
  var root = document.documentElement;
  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function store(k, v) { try { if (v === undefined) { return localStorage.getItem(k) || ""; } localStorage.setItem(k, v); } catch (e) { return ""; } return ""; }
  function typing(el) { var t = el && el.tagName; return t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || (el && el.isContentEditable); }

  /* ── theme ─────────────────────────────────────────────────────────────── */
  var toggle = $("#theme-toggle");
  function effectiveTheme() {
    var t = root.getAttribute("data-theme");
    if (t) { return t; }
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  if (toggle) {
    toggle.setAttribute("aria-pressed", effectiveTheme() === "dark" ? "true" : "false");
    toggle.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      store("markbook.theme", next);
      toggle.setAttribute("aria-pressed", next === "dark" ? "true" : "false");
    });
  }

  /* ── intro splash: once per session, skippable, never blocks the page ──── */
  var splash = $("#splash");
  if (splash && root.classList.contains("splash-on")) {
    var done = false;
    var finish = function () {
      if (done) { return; }
      done = true;
      try { sessionStorage.setItem("markbook.splash", "1"); } catch (e) { /* fine */ }
      root.classList.remove("splash-on");
    };
    splash.addEventListener("click", finish);
    document.addEventListener("keydown", finish, { once: true });
    splash.addEventListener("animationend", function (e) { if (e.animationName === "splash-out") { finish(); } });
    setTimeout(finish, 3200);   /* safety net if animationend never fires */
  }

  /* ── reviewer name, confirmations ──────────────────────────────────────── */
  var nameInput = $("#reviewer-name");
  if (nameInput) {
    nameInput.value = store("markbook.reviewer");
    nameInput.addEventListener("input", function () { store("markbook.reviewer", nameInput.value.trim()); });
  }
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { ev.preventDefault(); return; }
    var name = (nameInput && nameInput.value.trim()) || store("markbook.reviewer") || "web";
    $all(".reviewer-field", form).forEach(function (f) { f.value = name; });
    var busy = $("[data-busy]", form);                      /* stop double submits on slow actions */
    if (busy) { setTimeout(function () { busy.disabled = true; busy.textContent = busy.getAttribute("data-busy"); }, 0); }
  });

  /* ── session boundary for `markbook stats` (same-origin; name + ids only) ─ */
  var beacon = $("#view-beacon");
  if (beacon && window.fetch) {
    var who = (nameInput && nameInput.value.trim()) || store("markbook.reviewer") || "web";
    fetch(beacon.dataset.url, { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "reviewer=" + encodeURIComponent(who.slice(0, 60)) }).catch(function () {});
  }

  /* ── copy-to-clipboard buttons ─────────────────────────────────────────── */
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-copy]");
    if (!b) { return; }
    var text = b.getAttribute("data-copy");
    var ok = function () { var old = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = old; }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(ok, function () {}); }
  });

  /* ── filter + sort for tables and cards ────────────────────────────────── */
  var filter = $("#filter");
  if (filter) {
    var items = $all("[data-filter]");
    var empty = $("#filter-empty");
    filter.addEventListener("input", function () {
      var q = filter.value.trim().toLowerCase(), shown = 0;
      items.forEach(function (el) {
        var hit = !q || el.getAttribute("data-filter").indexOf(q) !== -1;
        el.hidden = !hit; if (hit) { shown++; }
      });
      if (empty) { empty.hidden = shown !== 0; }
    });
  }
  $all("table[data-sortable]").forEach(function (table) {
    var tbody = $("tbody", table);
    $all("th[data-sort]", table).forEach(function (th) {
      var btn = document.createElement("button");
      btn.type = "button"; btn.textContent = th.textContent;
      th.textContent = ""; th.appendChild(btn);
      btn.addEventListener("click", function () {
        var idx = th.cellIndex, num = th.getAttribute("data-sort") === "num";
        var dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
        $all("th", table).forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
        var rows = $all("tr", tbody);
        rows.sort(function (a, b) {
          var x = a.cells[idx].getAttribute("data-v") || a.cells[idx].textContent.trim();
          var y = b.cells[idx].getAttribute("data-v") || b.cells[idx].textContent.trim();
          if (num) { x = parseFloat(x); y = parseFloat(y); x = isNaN(x) ? -Infinity : x; y = isNaN(y) ? -Infinity : y; return (x - y) * dir; }
          return x.localeCompare(y) * dir;
        }).forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });

  /* ── drop zones show the chosen file ───────────────────────────────────── */
  $all(".drop").forEach(function (zone) {
    var input = $("input[type=file]", zone), picked = $(".picked", zone);
    if (!input) { return; }
    input.addEventListener("change", function () { if (picked) { picked.textContent = input.files[0] ? "✓ " + input.files[0].name : ""; } });
    ["dragenter", "dragover"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (n) { zone.addEventListener(n, function () { zone.classList.remove("over"); }); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event("change")); }
    });
  });

  /* ── keyboard: j/k move through submissions, / filters, ? shows help ───── */
  var help = $("#help");
  document.addEventListener("keydown", function (ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey || typing(ev.target)) { return; }
    if (ev.key === "j" || ev.key === "k") {
      var link = $(ev.key === "j" ? "#next" : "#prev");
      if (link) { window.location.href = link.href; }
    } else if (ev.key === "/" && filter) { ev.preventDefault(); filter.focus(); }
    else if (ev.key === "?" && help && help.showModal) { ev.preventDefault(); help.showModal(); }
  });
  var helpBtn = $("#help-open");
  if (helpBtn && help && help.showModal) { helpBtn.addEventListener("click", function () { help.showModal(); }); }
  $all("[data-close]", help || document).forEach(function (b) { b.addEventListener("click", function () { help.close(); }); });

  /* ── live progress for a running grade ─────────────────────────────────── */
  var box = $("#progress");
  if (box) {
    var count = $("#count"), arc = $("#arc"), pct = $("#pct"), active = $("#active"), chips = $("#active-chips");
    (function poll() {
      fetch(box.dataset.url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.state === "done" || s.state === "failed") { window.location.href = box.dataset.doneUrl; return; }
          var p = s.total ? Math.round(100 * s.done / s.total) : 0;
          count.textContent = s.done + " / " + s.total;
          if (arc) { arc.setAttribute("stroke-dasharray", p + " 100"); }
          if (pct) { pct.textContent = p + "%"; }
          if (chips) {
            chips.textContent = "";
            (s.active || []).forEach(function (id) { var c = document.createElement("span"); c.className = "chip"; c.textContent = id; chips.appendChild(c); });
          }
          if (active) { active.textContent = s.active && s.active.length ? "Marking " + s.active.length + " submission" + (s.active.length === 1 ? "" : "s") + " right now" : "Finishing up…"; }
          setTimeout(poll, 1000);
        })
        .catch(function () { setTimeout(poll, 3000); });
    })();
  }
})();

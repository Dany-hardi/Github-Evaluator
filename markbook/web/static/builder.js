/* The rubric form: tick what to check, and the spec is written for you.
   The browser sends CHOICES as JSON; the server turns them into YAML and validates them with the same
   validator the CLI uses. No inline scripts (strict CSP). */
(function () {
  "use strict";
  var root = document.getElementById("builder");
  if (!root) { return; }
  var form = root.closest("form");
  var catalog = JSON.parse(root.getAttribute("data-catalog") || "{}");
  var allowPrepare = root.getAttribute("data-allow-prepare") === "1";
  function $(s, c) { return (c || document).querySelector(s); }
  function $all(s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); }
  var CODE_KINDS = { build: 1, output: 1, tests: 1 };
  var mode = "builder", timer = null, lastOk = null, seq = 0, touched = false;   /* don't scold before the first edit */

  /* ── reading the form into a model ─────────────────────────────────────── */
  function val(el) { return el ? el.value : ""; }
  function cardsOn() { return $all(".check-card", root).filter(function (c) { return $(".check-on", c).checked; }); }

  function caseRows(card) {
    return $all(".case-row", card).map(function (r) {
      return { name: val($(".c-name", r)), args: val($(".c-args", r)), stdin: val($(".c-stdin", r)),
               expected: val($(".c-expected", r)), hidden: $(".c-hidden", r).checked };
    });
  }

  function collect() {
    var m = { name: val($("#b-name")), scale: val($("#b-scale")), pass_mark: val($("#b-pass")),
              language: val($("#b-language")), image: val($("#b-image")), timeout: val($("#b-timeout")), criteria: [] };
    if ($("#b-packages")) { m.extra_packages = val($("#b-packages")); }
    if ($("#b-deadline-on").checked) {
      m.deadline_local = val($("#b-deadline-at")); m.utc_offset = val($("#b-offset"));
      m.late_percent_per_day = val($("#b-late")); m.late_max_percent = val($("#b-late-max"));
    }
    cardsOn().forEach(function (card) {
      var item = { kind: card.getAttribute("data-kind"), points: val($(".pts", card)) };
      $all(".f", card).forEach(function (f) {
        item[f.getAttribute("data-name")] = f.type === "checkbox" ? f.checked : f.value;
      });
      var cases = $(".cases", card);
      if (cases) { item.cases = caseRows(card); }
      m.criteria.push(item);
    });
    return m;
  }

  /* ── writing a model back into the form (editing a template, or a failed submit) ── */
  function addCase(card, c) {
    var row = document.importNode($("#tpl-case").content, true);
    if (c) {
      $(".c-name", row).value = c.name || ""; $(".c-args", row).value = c.args || "";
      $(".c-stdin", row).value = c.stdin || ""; $(".c-expected", row).value = c.expected || c.stdout || "";
      $(".c-hidden", row).checked = !!c.hidden;
    }
    $(".case-list", card).appendChild(row);
  }
  function ensureCaseRow(card) { if (!$(".case-row", card)) { addCase(card); } }

  function newManualCard() {
    var node = document.importNode($("#tpl-manual").content, true);
    var cards = $all('.check-card[data-kind="manual"]', root);
    cards[cards.length - 1].insertAdjacentElement("afterend", node.firstElementChild);
    var added = $all('.check-card[data-kind="manual"]', root).pop();
    $(".check-on", added).checked = true;
    return added;
  }

  function restore(m) {
    if (!m || typeof m !== "object") { return; }
    function set(sel, v) { var el = $(sel); if (el && v !== undefined && v !== null) { el.value = v; } }
    set("#b-name", m.name); set("#b-pass", m.pass_mark); set("#b-language", m.language); set("#b-image", m.image);
    set("#b-timeout", m.timeout); set("#b-packages", m.extra_packages);
    if (m.scale !== undefined && m.scale !== "") { set("#b-scale", m.scale); $("#b-scale").removeAttribute("data-auto"); }
    var on = !!(m.deadline_local || m.late_percent_per_day);
    $("#b-deadline-on").checked = !!m.deadline_local;
    if (m.deadline_local) { set("#b-deadline-at", m.deadline_local); set("#b-offset", m.utc_offset); set("#b-late", m.late_percent_per_day); set("#b-late-max", m.late_max_percent); }
    $all(".check-card", root).forEach(function (c) {
      if (c.getAttribute("data-removable")) { c.remove(); } else { $(".check-on", c).checked = false; $all(".case-row", c).forEach(function (r) { r.remove(); }); }
    });
    var seenManual = 0;
    (m.criteria || []).forEach(function (item) {
      var card;
      if (item.kind === "manual") { seenManual += 1; card = seenManual === 1 ? $('.check-card[data-kind="manual"]', root) : newManualCard(); }
      else { card = $('.check-card[data-kind="' + item.kind + '"]', root); }
      if (!card) { return; }
      $(".check-on", card).checked = true;
      if (item.points !== undefined) { $(".pts", card).value = item.points; }
      $all(".f", card).forEach(function (f) {
        var v = item[f.getAttribute("data-name")];
        if (v === undefined) { return; }
        if (f.type === "checkbox") { f.checked = !!v; } else { f.value = Array.isArray(v) ? v.join("\n") : v; }
      });
      if ($(".cases", card)) { (item.cases || []).forEach(function (c) { addCase(card, c); }); }
    });
    refresh();
  }

  /* ── UI state that follows the form ───────────────────────────────────── */
  function refresh() {
    cardsOn().forEach(function (c) { if ($(".cases", c)) { ensureCaseRow(c); } });
    $all(".check-card", root).forEach(function (c) {
      var on = $(".check-on", c).checked;
      c.classList.toggle("is-on", on);
      $(".check-body", c).hidden = !on;
      $(".check-help", c).hidden = on;
      $(".pts", c).disabled = !on;
    });
    var needsCode = cardsOn().some(function (c) { return CODE_KINDS[c.getAttribute("data-kind")]; });
    $("#b-runs").hidden = !needsCode;
    var lang = val($("#b-language"));
    $("#b-image-row").hidden = lang !== "custom";
    if ($("#b-packages-row")) { $("#b-packages-row").hidden = !(lang === "python" || lang === "node"); }
    $("#b-deadline").hidden = !$("#b-deadline-on").checked;
    var info = (catalog.languages || {})[lang] || {};
    $all("[data-hint-from]", root).forEach(function (f) { f.placeholder = info[f.getAttribute("data-hint-from")] || "required"; });
    var total = cardsOn().reduce(function (s, c) { return s + (parseFloat(val($(".pts", c))) || 0); }, 0);
    var scale = $("#b-scale");
    if (scale.getAttribute("data-auto") && total > 0) { scale.value = +total.toFixed(2); }
    $("#total").textContent = (+total.toFixed(2)) + " point" + (total === 1 ? "" : "s") + " in total" +
      (parseFloat(val(scale)) && Math.abs(parseFloat(val(scale)) - total) > 1e-9 ? ", scaled to a grade out of " + val(scale) : "");
  }

  /* ── live preview from the server (the one source of truth for validity) ── */
  function fieldFor(path) {
    if (path.indexOf("criteria.") !== 0) { return $('[data-field="' + path + '"]', root); }
    var parts = path.split("."), id = parts[1], what = parts[2];
    var seen = {}, card = null;
    cardsOn().some(function (c) {
      var k = c.getAttribute("data-kind"); seen[k] = (seen[k] || 0) + 1;
      if ((seen[k] === 1 ? k : k + "-" + seen[k]) === id) { card = c; return true; }
      return false;
    });
    if (!card) { return null; }
    if (what === "points") { return $(".pts", card); }
    return $('[data-name="' + (what || "title") + '"]', card) || $(".check-on", card);
  }

  function showProblems(list) {
    var ul = $("#problems");
    ul.textContent = "";
    $all("[aria-invalid]", root).forEach(function (el) { el.removeAttribute("aria-invalid"); });
    (list || []).forEach(function (p) {
      var li = document.createElement("li"); li.textContent = p.message; ul.appendChild(li);
      var el = p.field ? fieldFor(p.field) : null;
      if (el) { el.setAttribute("aria-invalid", "true"); }
    });
  }

  function preview() {
    var mine = ++seq;
    fetch("/api/rubric/preview", { method: "POST", headers: { "Content-Type": "application/json" },
                                   credentials: "same-origin", body: JSON.stringify(collect()) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (mine !== seq) { return; }                                 /* a newer request is in flight */
        lastOk = !!d.ok;
        $("#yaml-preview").textContent = d.ok ? d.yaml
          : (touched ? "The rubric is not ready yet. Fix what is listed above and the YAML appears here."
                     : "Fill in the form and the rubric appears here.");
        showProblems(touched ? d.problems : []);
      })
      .catch(function () { lastOk = null; });
  }
  function schedule() { refresh(); clearTimeout(timer); timer = setTimeout(preview, 250); }

  function edited() { touched = true; schedule(); }
  root.addEventListener("input", edited);
  root.addEventListener("change", edited);

  /* A one-line field inside a form submits it on Enter: that would start grading. Don't. */
  root.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && ev.target.tagName === "INPUT" && ev.target.type !== "checkbox") { ev.preventDefault(); }
  });

  root.addEventListener("click", function (ev) {
    var t = ev.target;
    if (t.classList.contains("add-case")) { addCase(t.closest(".check-card")); schedule(); }
    else if (t.classList.contains("remove-case")) { t.closest(".case-row").remove(); schedule(); }
    else if (t.classList.contains("remove-card")) { t.closest(".check-card").remove(); schedule(); }
    else if (t.id === "add-manual") { newManualCard(); schedule(); }
  });

  /* ── copy / download / templates ───────────────────────────────────────── */
  $("#copy-yaml").addEventListener("click", function () {
    var text = $("#yaml-preview").textContent, b = this, old = b.textContent;
    if (lastOk && navigator.clipboard) { navigator.clipboard.writeText(text).then(function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = old; }, 1400); }); }
  });
  $("#download-yaml").addEventListener("click", function () {
    var f = document.createElement("form"), i = document.createElement("input");
    f.method = "post"; f.action = "/rubric/spec.yaml"; i.type = "hidden"; i.name = "builder_json"; i.value = JSON.stringify(collect());
    f.appendChild(i); document.body.appendChild(f); f.submit(); f.remove();
  });
  $("#save-template").addEventListener("click", function () {
    var msg = $("#template-msg"), name = val($("#template-name"));
    fetch("/api/rubric/template", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
                                    body: JSON.stringify({ model: collect(), name: name }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (x.ok) {
          msg.textContent = "Saved as " + x.d.file + ". Find it under “A saved template”.";
          var sel = $("#preset");
          if (sel) { var o = document.createElement("option"); o.value = x.d.file; o.textContent = x.d.file; sel.appendChild(o); }
        } else { msg.textContent = (x.d.problems && x.d.problems[0] && x.d.problems[0].message) || "Could not save."; showProblems(x.d.problems); }
      })
      .catch(function () { msg.textContent = "Could not reach the server."; });
  });

  var preset = $("#preset"), editBtn = $("#edit-template");
  if (preset && editBtn) {
    var editable = JSON.parse(preset.getAttribute("data-editable") || "[]");
    preset.addEventListener("change", function () { editBtn.disabled = editable.indexOf(preset.value) === -1; });
    editBtn.addEventListener("click", function () {
      fetch("/api/rubric/template/" + encodeURIComponent(preset.value), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) { restore(d.model); setMode("builder"); schedule(); });
    });
  }

  /* ── the three ways to define a rubric ─────────────────────────────────── */
  function setMode(next) {
    mode = next;
    ["builder", "template", "yaml"].forEach(function (m) {
      var on = m === next;
      $("#panel-" + m).hidden = !on;
      $("#tab-" + m).setAttribute("aria-selected", on ? "true" : "false");
    });
    if (preset) { preset.disabled = next !== "template"; }
  }
  ["builder", "template", "yaml"].forEach(function (m) {
    $("#tab-" + m).addEventListener("click", function () { setMode(m); });
  });

  form.addEventListener("submit", function (ev) {
    var hidden = $("#builder_json");
    hidden.value = "";
    if (mode !== "builder") { return; }
    if (lastOk === false) {                                           /* we already know it is not valid: say so now */
      ev.preventDefault();
      touched = true; preview();
      $("#problems").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    hidden.value = JSON.stringify(collect());
  });

  /* ── start-up ──────────────────────────────────────────────────────────── */
  var off = -new Date().getTimezoneOffset();               /* minutes east of UTC */
  var sign = off < 0 ? "-" : "+", a = Math.abs(off);
  $("#b-offset").value = sign + ("0" + Math.floor(a / 60)).slice(-2) + ":" + ("0" + (a % 60)).slice(-2);
  $("#b-scale").addEventListener("input", function () { this.removeAttribute("data-auto"); });
  var saved = root.getAttribute("data-state");
  if (saved) { try { restore(JSON.parse(saved)); } catch (e) { /* unreadable draft: start fresh */ } }
  setMode("builder");
  refresh();
  preview();
})();

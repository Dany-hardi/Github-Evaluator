// Drives the REAL builder.js against the REAL /runs/new page and server (real fetch, real preview endpoint).
const { JSDOM, VirtualConsole } = require("jsdom");
const BASE = process.env.BASE;
let failures = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) failures++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function load() {
  const vc = new VirtualConsole(); vc.on("jsdomError", e => console.log("  [jsdom error]", e.message.slice(0, 160)));
  const dom = await JSDOM.fromURL(BASE + "/runs/new?splash=0", { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.Element.prototype.scrollIntoView = function () {};
      w.matchMedia = w.matchMedia || (() => ({ matches: false }));
      w.fetch = (u, o) => fetch(new URL(u, BASE), { ...o, headers: { ...(o && o.headers), Origin: BASE } });
    } });
  await new Promise(r => dom.window.addEventListener("load", r)); await sleep(100);
  return dom;
}
const fire = (w, el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
function setv(w, el, v) { el.value = v; fire(w, el, "input"); fire(w, el, "change"); }
async function settle() { await sleep(700); }

(async () => {
  const d = await load(), w = d.window, doc = w.document;
  const $ = s => doc.querySelector(s);
  ok(/Fill in the form/.test($("#yaml-preview").textContent) && !$("#problems").textContent.trim(), "pristine page: no scolding before the first edit");

  setv(w, $("#b-name"), "Blog API"); await settle();
  const yaml1 = $("#yaml-preview").textContent;
  ok(/name: Blog API/.test(yaml1) && /type: file_exists/.test(yaml1), "typing a name produces the YAML live");
  ok($("#total").textContent.includes("9"), "total follows the ticked checks: " + $("#total").textContent.trim());

  const card = k => doc.querySelector(`.check-card[data-kind="${k}"]`);
  const hist = card("history"); hist.querySelector(".check-on").checked = true; fire(w, hist.querySelector(".check-on"), "change"); await settle();
  ok(/type: git/.test($("#yaml-preview").textContent) && $("#total").textContent.includes("10"), "ticking Git history adds it (10 points)");
  ok(hist.classList.contains("is-on"), "ticked card is highlighted");

  setv(w, $("#b-name"), ""); await settle();
  ok(/Give the assignment a name/.test($("#problems").textContent) && $("#b-name").getAttribute("aria-invalid") === "true", "empty name: problem listed and field flagged");
  ok(/not ready/.test($("#yaml-preview").textContent), "invalid: preview says so");

  // Enter in a text input must not submit the form
  let submitted = false; $("#newrun").addEventListener("submit", () => { submitted = true; });
  const ev = new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
  $("#b-name").dispatchEvent(ev);
  ok(ev.defaultPrevented && !submitted, "Enter inside a field does not start the run");

  // invalid submit is blocked
  const ev2 = new w.Event("submit", { bubbles: true, cancelable: true }); $("#newrun").dispatchEvent(ev2);
  ok(ev2.defaultPrevented, "submitting an invalid rubric is blocked client-side");

  // valid submit fills builder_json
  setv(w, $("#b-name"), "Lab <1>"); await settle();
  const ev3 = new w.Event("submit", { bubbles: true, cancelable: true }); $("#newrun").dispatchEvent(ev3);
  const model = JSON.parse($("#builder_json").value || "null");
  ok(model && model.name === "Lab <1>" && model.criteria.map(c => c.kind).join() === "files,readme,history,manual", "builder_json carries choices only: " + ($("#builder_json").value || "").slice(0, 90));
  ok(!/"run"|"command"/.test($("#builder_json").value), "no commands are sent from the browser");

  // add another manual criterion, then remove it
  const before = doc.querySelectorAll('.check-card[data-kind="manual"]').length;
  $("#add-manual").click(); await settle();
  const after = doc.querySelectorAll('.check-card[data-kind="manual"]').length;
  ok(after === before + 1, "'Add another manual criterion' adds a card");

  // output cases repeater
  const out = card("output"); out.querySelector(".check-on").checked = true; fire(w, out.querySelector(".check-on"), "change");
  const addCase = out.querySelector(".add-case"); 
  ok(!!addCase, "output card has an add-case button");
  if (addCase) { addCase.click(); ok(out.querySelectorAll(".case-row").length >= 2, "add-case adds a case row"); }

  // tabs
  $("#tab-yaml").click();
  ok(!$("#panel-yaml").hidden && $("#panel-builder").hidden && $("#tab-yaml").getAttribute("aria-selected") === "true", "YAML tab shows its panel");
  $("#tab-builder").click();
  ok(!$("#panel-builder").hidden, "back to the form");

  out.querySelector(".check-on").checked = false; fire(w, out.querySelector(".check-on"), "change");
  // template save then appears in the picker
  setv(w, $("#b-name"), "Saved one"); setv(w, $("#template-name"), "ui test " + Date.now()); await settle();
  $("#save-template").click(); await sleep(800);
  ok(/saved/i.test($("#template-msg").textContent), "saving a template confirms: " + $("#template-msg").textContent.trim());

  d.window.close();
  process.exit(failures ? 1 : 0);
})().catch(e => { console.log("FAIL crashed: " + e.stack); process.exit(1); });

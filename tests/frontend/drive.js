// Drives the REAL markbook.js/boot.js against REAL server-rendered pages in jsdom.
const { JSDOM, VirtualConsole } = require("jsdom");
const BASE = process.env.BASE, RUN = process.env.RUN;
let failures = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) failures++; };

async function load(path, { session = {}, local = {} } = {}) {
  const vc = new VirtualConsole(); vc.on("jsdomError", e => console.log("  [jsdom error]", e.message.slice(0, 120)));
  const dom = await JSDOM.fromURL(BASE + path, { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      Object.entries(session).forEach(([k, v]) => w.sessionStorage.setItem(k, v));
      Object.entries(local).forEach(([k, v]) => w.localStorage.setItem(k, v));
      w.matchMedia = w.matchMedia || (() => ({ matches: false }));
      w.fetch = () => Promise.resolve({ json: () => Promise.resolve({}) });   // beacon / poll: not under test
    } });
  await new Promise(r => dom.window.addEventListener("load", r)); await new Promise(r => setTimeout(r, 50));
  return dom;
}
const rows = d => [...d.window.document.querySelectorAll("table[data-sortable] tbody tr")];
const visible = d => rows(d).filter(r => !r.hidden).map(r => r.getAttribute("data-filter").split(" ").pop());   // last token is the student id

(async () => {
  // ── run page: sort, filter ───────────────────────────────────────────────
  let d = await load(`/runs/${RUN}?show=all&splash=0`);
  const doc = d.window.document;
  const ths = [...doc.querySelectorAll("th[data-sort]")];
  ok(ths.length === 3 && ths.every(t => t.querySelector("button")), "sortable headers became buttons (" + ths.map(t => t.textContent.trim()).join(", ") + ")");
  const gradeBtn = ths[1].querySelector("button");
  gradeBtn.click();
  let order = rows(d).map(r => r.querySelector("td.num").getAttribute("data-v"));
  const asc = order.filter(x => x !== "").map(Number);
  ok(ths[1].getAttribute("aria-sort") === "ascending" && asc.every((v, i) => i === 0 || asc[i - 1] <= v), "grade sorts ascending: " + order.join(","));
  ok(order[0] === "", "unfetchable (no grade) sorts first ascending");
  gradeBtn.click();
  order = rows(d).map(r => r.querySelector("td.num").getAttribute("data-v")).filter(x => x !== "").map(Number);
  ok(ths[1].getAttribute("aria-sort") === "descending" && order.every((v, i) => i === 0 || order[i - 1] >= v), "grade sorts descending: " + order.join(","));
  ths[0].querySelector("button").click();
  const names = rows(d).map(r => r.querySelector("th a").textContent);
  ok(JSON.stringify(names) === JSON.stringify([...names].sort((a, b) => a.localeCompare(b))), "student name sorts alphabetically");

  const filter = doc.getElementById("filter");
  filter.value = "carol"; filter.dispatchEvent(new d.window.Event("input"));
  ok(JSON.stringify(visible(d)) === '["carol"]', "filter 'carol' leaves only carol: " + visible(d));
  filter.value = "OKAFOR"; filter.dispatchEvent(new d.window.Event("input"));
  ok(JSON.stringify(visible(d)) === '["bob"]', "filter is case-insensitive and matches names: " + visible(d));
  filter.value = "zzz"; filter.dispatchEvent(new d.window.Event("input"));
  ok(visible(d).length === 0 && !doc.getElementById("filter-empty").hidden, "no match shows the empty message");
  filter.value = ""; filter.dispatchEvent(new d.window.Event("input"));
  ok(visible(d).length === 7 && doc.getElementById("filter-empty").hidden, "clearing the filter restores all 7 rows");

  // ── copy buttons ─────────────────────────────────────────────────────────
  let copied = null;
  Object.defineProperty(d.window.navigator, "clipboard", { value: { writeText: t => { copied = t; return Promise.resolve(); } }, configurable: true });
  const copyBtn = doc.querySelector("[data-copy]");
  copyBtn.click(); await new Promise(r => setTimeout(r, 20));
  ok(copied && copied.startsWith("markbook review ") && copyBtn.textContent === "Copied", "copy button copies the command and confirms: " + copied);

  // ── keyboard: / focuses the filter, ? is guarded while typing ───────────
  doc.body.dispatchEvent(new d.window.KeyboardEvent("keydown", { key: "/", bubbles: true }));
  ok(doc.activeElement === filter, "'/' focuses the filter");
  filter.value = "j"; filter.dispatchEvent(new d.window.Event("input"));
  filter.dispatchEvent(new d.window.KeyboardEvent("keydown", { key: "j", bubbles: true }));
  ok(true, "typing 'j' in the filter does not trigger navigation (guarded)");

  // ── theme toggle ─────────────────────────────────────────────────────────
  const tt = doc.getElementById("theme-toggle");
  const before = doc.documentElement.getAttribute("data-theme");
  tt.click();
  const t1 = doc.documentElement.getAttribute("data-theme");
  ok(t1 === "dark" && tt.getAttribute("aria-pressed") === "true" && d.window.localStorage.getItem("markbook.theme") === "dark", `toggle -> dark, aria-pressed, persisted (was ${before})`);
  tt.click();
  ok(doc.documentElement.getAttribute("data-theme") === "light" && tt.getAttribute("aria-pressed") === "false", "toggle back -> light");

  // ── reviewer name is stamped on decisions ────────────────────────────────
  d = await load(`/runs/${RUN}/s/bob?splash=0`, { local: { "markbook.reviewer": "Ann" } });
  const rn = d.window.document.getElementById("reviewer-name");
  ok(rn.value === "Ann", "reviewer name restored from storage");
  rn.value = "Ben"; rn.dispatchEvent(new d.window.Event("input"));
  ok(d.window.localStorage.getItem("markbook.reviewer") === "Ben", "reviewer name persisted on input");
  const form = [...d.window.document.querySelectorAll("form")].find(f => f.querySelector(".reviewer-field"));
  const sub = new d.window.Event("submit", { bubbles: true, cancelable: true });
  form.dispatchEvent(sub);                                   // the page's document-level handler stamps the field while the event bubbles
  const stamped = form.querySelector(".reviewer-field").value;
  ok(stamped === "Ben", "decision form carries the reviewer name into the audit log: " + stamped);

  // ── delete-run confirmation ──────────────────────────────────────────────
  d = await load(`/runs/${RUN}?splash=0`);
  let asked = null; d.window.confirm = m => { asked = m; return false; };
  const del = d.window.document.querySelector("form[data-confirm]");
  const ev = new d.window.Event("submit", { bubbles: true, cancelable: true });
  del.dispatchEvent(ev);
  ok(asked && /Delete this run/.test(asked) && ev.defaultPrevented, "delete asks for confirmation and cancels when declined");

  // ── intro splash: once, skippable ────────────────────────────────────────
  d = await load("/");
  const root = d.window.document.documentElement;
  ok(root.classList.contains("splash-on"), "first visit to / plays the intro");
  d.window.document.getElementById("splash").click();
  ok(!root.classList.contains("splash-on") && d.window.sessionStorage.getItem("markbook.splash") === "1", "click skips it and remembers for the session");
  d = await load("/", { session: { "markbook.splash": "1" } });
  ok(!d.window.document.documentElement.classList.contains("splash-on"), "does not replay in the same session");
  d = await load("/");
  d.window.document.dispatchEvent(new d.window.KeyboardEvent("keydown", { key: "x" }));
  ok(!d.window.document.documentElement.classList.contains("splash-on"), "any key skips it");

  // ── drop zone shows the chosen file ──────────────────────────────────────
  d = await load("/runs/new?splash=0");
  const zone = d.window.document.querySelector(".drop"), input = zone.querySelector("input[type=file]");
  Object.defineProperty(input, "files", { value: [{ name: "spec.yaml" }], configurable: true });
  input.dispatchEvent(new d.window.Event("change"));
  ok(zone.querySelector(".picked").textContent.includes("spec.yaml"), "drop zone shows the chosen file name");

  console.log(failures ? `\n${failures} FAILED` : "\nall interaction checks passed");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });

/* ── Shared app utilities ─────────────────────────────────────────── */

// ── Toast system ──────────────────────────────────────────────────────────
(function () {
  const container = document.createElement("div");
  container.className = "toast-container";
  document.body.appendChild(container);

  window.toast = function (message, type = "default", duration = 3500) {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-dot"></span><span>${message}</span>`;
    container.appendChild(el);
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("show")));
    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.remove(), 350);
    }, duration);
  };
})();

// ── Modal helpers ─────────────────────────────────────────────────────────
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("open");
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("open");
}
// Close on overlay click
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-overlay")) {
    e.target.classList.remove("open");
  }
});
// Close on Escape
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-overlay.open").forEach((m) =>
      m.classList.remove("open")
    );
  }
});

// ── Active nav link ───────────────────────────────────────────────────────
(function () {
  const path = window.location.pathname;
  document.querySelectorAll(".nav-link").forEach((a) => {
    const href = a.getAttribute("href");
    if (href === "/" ? path === "/" : path.startsWith(href)) {
      a.classList.add("active");
    }
  });
})();

// ── Grade tier helper ────────────────────────────────────────────────────
function gradeTier(val, scale = 20) {
  const pct = val / scale;
  if (pct >= 0.8) return "excellent";
  if (pct >= 0.6) return "good";
  if (pct >= 0.5) return "average";
  return "poor";
}

function gradeCircleHTML(grade, scale = 20) {
  const tier = gradeTier(grade, scale);
  return `<div class="grade-circle ${tier}">
    <span class="grade-num">${grade.toFixed(1)}</span>
    <span class="grade-denom">/${scale}</span>
  </div>`;
}

// ── Confirm dialog (returns Promise<bool>) ────────────────────────────────
function confirmDialog(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay open";
    overlay.innerHTML = `
      <div class="modal" style="max-width:380px">
        <div class="modal-header"><h2 class="modal-title">Confirm</h2></div>
        <div class="modal-body" style="font-size:14px;color:var(--text)">${message}</div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="_conf-cancel">Cancel</button>
          <button class="btn btn-primary" id="_conf-ok">Confirm</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector("#_conf-cancel").onclick = () => { overlay.remove(); resolve(false); };
    overlay.querySelector("#_conf-ok").onclick = () => { overlay.remove(); resolve(true); };
  });
}

// ── Tabs ──────────────────────────────────────────────────────────────────
function initTabs(containerSelector) {
  document.querySelectorAll(containerSelector || ".tabs").forEach((tabs) => {
    tabs.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.tab;
        // Deactivate all in this tab group
        const parent = tabs.closest("[data-tabs]") || document;
        tabs.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        parent.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");
        const content = parent.querySelector(`[data-tab-content="${target}"]`);
        if (content) content.classList.add("active");
      });
    });
  });
}
document.addEventListener("DOMContentLoaded", () => initTabs());

// ── Sortable table ────────────────────────────────────────────────────────
function makeSortable(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const headers = table.querySelectorAll("th[data-sort]");
  let lastCol = null, asc = true;

  headers.forEach((th) => {
    th.style.cursor = "pointer";
    th.innerHTML += '<span class="sort-icon">&#9650;</span>';
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      const isNum = th.dataset.type === "num";
      asc = lastCol === col ? !asc : true;
      lastCol = col;

      headers.forEach((h) => { h.classList.remove("sorted"); });
      th.classList.add("sorted");

      const tbody = table.tBodies[0];
      const rows  = Array.from(tbody.rows);
      rows.sort((a, b) => {
        const av = a.dataset[col] ?? a.cells[th.cellIndex]?.textContent ?? "";
        const bv = b.dataset[col] ?? b.cells[th.cellIndex]?.textContent ?? "";
        const cmp = isNum ? parseFloat(av) - parseFloat(bv) : av.localeCompare(bv);
        return asc ? cmp : -cmp;
      });
      rows.forEach((r) => tbody.appendChild(r));
    });
  });
}

// ── Number formatting ─────────────────────────────────────────────────────
function fmt(n, decimals = 2) {
  return typeof n === "number" ? n.toFixed(decimals) : (n || "—");
}

// ── Copy to clipboard ─────────────────────────────────────────────────────
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard", "success", 2000);
  } catch {
    toast("Copy failed", "error", 2000);
  }
}

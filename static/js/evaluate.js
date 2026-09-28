/**
 * Evaluate page — student management, batch CSV import, rubric editor, form submit.
 */
document.addEventListener("DOMContentLoaded", () => {

  const students = [];   // [{name, matricule, code_url, doc_url}]

  // ── Rubric sliders ────────────────────────────────────────────────────────
  const sliders = {
    code:          document.getElementById("rubric-code"),
    execution:     document.getElementById("rubric-execution"),
    documentation: document.getElementById("rubric-documentation"),
  };

  function updateRubric() {
    const vals = {
      code:          parseInt(sliders.code?.value || 40),
      execution:     parseInt(sliders.execution?.value || 30),
      documentation: parseInt(sliders.documentation?.value || 30),
    };
    const total = vals.code + vals.execution + vals.documentation;

    if (document.getElementById("rubric-code-val"))
      document.getElementById("rubric-code-val").textContent = vals.code + "%";
    if (document.getElementById("rubric-execution-val"))
      document.getElementById("rubric-execution-val").textContent = vals.execution + "%";
    if (document.getElementById("rubric-documentation-val"))
      document.getElementById("rubric-documentation-val").textContent = vals.documentation + "%";

    const totalEl = document.getElementById("rubric-total");
    if (totalEl) {
      totalEl.textContent = total + "%";
      totalEl.style.color = total === 100 ? "var(--success)" : "var(--error)";
      totalEl.className   = total === 100 ? "badge badge-success" : "badge badge-error";
    }
  }

  Object.values(sliders).forEach((s) => {
    if (s) s.addEventListener("input", updateRubric);
  });
  updateRubric();

  // ── Helper: Normalize URLs ───────────────────────────────────────────────
  function normalizeUrl(url) {
    if (!url) return "";
    let trimmed = url.trim();
    if (!trimmed) return "";
    if (!/^https?:\/\//i.test(trimmed)) {
      trimmed = "https://" + trimmed;
    }
    return trimmed;
  }

  // ── Add student logic ────────────────────────────────────────────────────
  function addStudentFromInputs() {
    const nameEl = document.getElementById("s-name");
    const matEl  = document.getElementById("s-mat");
    const codeEl = document.getElementById("s-code");
    const docEl  = document.getElementById("s-doc");

    const name     = (nameEl?.value || "").trim();
    const matricule= (matEl?.value || "").trim();
    let code_url   = normalizeUrl(codeEl?.value || "");
    let doc_url    = normalizeUrl(docEl?.value || "");

    if (!code_url) {
      if (!name) toast("Please enter a Code Repository URL", "error");
      return false;
    }

    // Default student name if left blank
    const displayName = name || (matricule ? `Student (${matricule})` : `Student ${students.length + 1}`);

    // Deduplicate by code_url
    if (students.some((s) => s.code_url === code_url)) {
      toast("This repository URL has already been added to the list", "warning");
      return false;
    }

    students.push({
      name: displayName,
      matricule,
      code_url,
      doc_url,
    });

    renderStudentList();

    // Reset inputs
    if (nameEl) nameEl.value = "";
    if (matEl)  matEl.value  = "";
    if (codeEl) codeEl.value = "";
    if (docEl)  docEl.value  = "";

    nameEl?.focus();
    toast(`Added ${displayName}`, "success", 2000);
    return true;
  }

  // Bind Add Student Button
  const addBtn = document.getElementById("add-student-btn");
  if (addBtn) {
    addBtn.addEventListener("click", (e) => {
      e.preventDefault();
      addStudentFromInputs();
    });
  }

  // Bind Enter Key inside Student Input Box
  ["s-name", "s-mat", "s-code", "s-doc"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          addStudentFromInputs();
        }
      });
      // Also update start button enablement as user types
      el.addEventListener("input", updateStartBtn);
    }
  });

  // ── CSV batch import ──────────────────────────────────────────────────────
  const csvArea = document.getElementById("csv-import-area");
  const csvBtn  = document.getElementById("csv-import-btn");

  if (csvBtn) {
    csvBtn.addEventListener("click", async () => {
      const raw = csvArea?.value?.trim();
      if (!raw) { toast("Paste CSV data first", "warning"); return; }

      csvBtn.disabled = true;
      csvBtn.textContent = "Importing...";

      try {
        const res = await fetch("/api/parse-csv", {
          method: "POST",
          headers: { "Content-Type": "text/plain" },
          body: raw,
        });
        const data = await res.json();

        if (data.error) { toast(data.error, "error"); return; }

        let added = 0;
        for (const s of data.students) {
          const normCode = normalizeUrl(s.code_url);
          const normDoc  = normalizeUrl(s.doc_url);
          if (normCode && !students.find((x) => x.code_url === normCode)) {
            students.push({
              name:      s.name || `Student ${students.length + 1}`,
              matricule: s.matricule || "",
              code_url:  normCode,
              doc_url:   normDoc,
            });
            added++;
          }
        }

        renderStudentList();
        if (csvArea) csvArea.value = "";
        closeModal("csv-modal");

        if (data.errors && data.errors.length) {
          toast(`${added} imported, ${data.errors.length} skipped`, "warning");
        } else {
          toast(`${added} student(s) imported`, "success");
        }
      } catch (err) {
        toast("Import failed: " + err.message, "error");
      } finally {
        csvBtn.disabled = false;
        csvBtn.textContent = "Import";
      }
    });
  }

  // File upload for CSV
  const fileInput = document.getElementById("csv-file");
  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        if (csvArea) csvArea.value = ev.target.result;
      };
      reader.readAsText(file);
    });
  }

  // ── Render student table ──────────────────────────────────────────────────
  function renderStudentList() {
    const container = document.getElementById("student-list");
    const counter   = document.getElementById("student-count");
    if (!container) return;

    if (counter) counter.textContent = students.length;

    if (students.length === 0) {
      container.innerHTML = `
        <div class="student-list-empty" style="text-align:center;padding:var(--sp-6);color:var(--text-secondary);font-size:13px">
          No students added yet. Enter a repo URL above or import a CSV.
        </div>`;
      updateStartBtn();
      return;
    }

    container.innerHTML = students.map((s, i) => `
      <div class="student-row" id="student-row-${i}" style="display:grid;grid-template-columns:1fr 2fr 1.5fr auto;gap:var(--sp-3);align-items:center;padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--border)">
        <div>
          <div class="font-600">${escHtml(s.name)}</div>
          <div class="text-sm text-secondary">${escHtml(s.matricule || "—")}</div>
        </div>
        <div class="truncate text-sm font-mono" title="${escHtml(s.code_url)}">
          <a href="${escHtml(s.code_url)}" target="_blank" rel="noopener">${escHtml(s.code_url)}</a>
        </div>
        <div class="truncate text-sm font-mono text-secondary" title="${escHtml(s.doc_url || '')}">
          ${s.doc_url ? `<a href="${escHtml(s.doc_url)}" target="_blank" rel="noopener">${escHtml(s.doc_url)}</a>` : '<span class="text-secondary">—</span>'}
        </div>
        <button type="button" class="btn btn-icon sm" onclick="removeStudent(${i})" title="Remove">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>`).join("");

    updateStartBtn();
  }

  window.removeStudent = function (idx) {
    students.splice(idx, 1);
    renderStudentList();
  };

  function updateStartBtn() {
    const btn = document.getElementById("start-btn");
    const codeVal = (document.getElementById("s-code")?.value || "").trim();
    if (btn) {
      // Enable start button if students exist OR if a code URL is currently typed in the input
      btn.disabled = (students.length === 0 && !codeVal);
    }
  }

  // ── Form submission ───────────────────────────────────────────────────────
  const mainForm = document.getElementById("session-form");
  if (mainForm) {
    mainForm.addEventListener("submit", async (e) => {
      e.preventDefault();

      // Auto-add pending student from inputs if typed but not added yet
      const pendingCode = (document.getElementById("s-code")?.value || "").trim();
      if (pendingCode) {
        addStudentFromInputs();
      }

      if (students.length === 0) {
        toast("Please add at least one student code repository URL", "warning");
        document.getElementById("s-code")?.focus();
        return;
      }

      const codeW = parseInt(sliders.code?.value || 40);
      const execW = parseInt(sliders.execution?.value || 30);
      const docW  = parseInt(sliders.documentation?.value || 30);
      if (codeW + execW + docW !== 100) {
        toast("Rubric weights must sum to 100%", "error");
        return;
      }

      const sessionName = (document.getElementById("session-name")?.value || "").trim();
      const payload = {
        name:                    sessionName || `Evaluation — ${new Date().toLocaleDateString()}`,
        github_token:            (document.getElementById("gh-token")?.value || "").trim(),
        rubric_code:             codeW,
        rubric_execution:        execW,
        rubric_documentation:    docW,
        ai_feedback_enabled:     document.getElementById("ai-toggle")?.checked,
        plagiarism_enabled:      document.getElementById("plagiarism-toggle")?.checked,
        students,
      };

      const startBtn = document.getElementById("start-btn");
      startBtn.disabled = true;
      startBtn.textContent = "Creating Session...";

      try {
        const res = await fetch("/api/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (!res.ok) {
          toast(data.error || "Failed to create session", "error");
          startBtn.disabled = false;
          startBtn.textContent = "Start Evaluation";
          return;
        }

        // Start evaluation process
        startBtn.textContent = "Starting Evaluation...";
        const startRes = await fetch(`/api/sessions/${data.session_id}/start`, { method: "POST" });
        if (!startRes.ok) {
          const startErr = await startRes.json();
          toast(startErr.error || "Failed to start evaluation", "error");
          startBtn.disabled = false;
          startBtn.textContent = "Start Evaluation";
          return;
        }

        window.location.href = `/progress/${data.session_id}`;
      } catch (err) {
        toast("Error starting evaluation: " + err.message, "error");
        startBtn.disabled = false;
        startBtn.textContent = "Start Evaluation";
      }
    });
  }

  // ── Escape HTML ───────────────────────────────────────────────────────────
  function escHtml(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  // Initial render
  renderStudentList();
});

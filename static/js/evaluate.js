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
  const displays = {
    code:          document.getElementById("rubric-code-val"),
    execution:     document.getElementById("rubric-execution-val"),
    display_total: document.getElementById("rubric-total"),
  };

  function updateRubric(changed) {
    const vals = {
      code:          parseInt(sliders.code.value),
      execution:     parseInt(sliders.execution.value),
      documentation: parseInt(sliders.documentation.value),
    };
    const total = vals.code + vals.execution + vals.documentation;

    document.getElementById("rubric-code-val").textContent          = vals.code + "%";
    document.getElementById("rubric-execution-val").textContent      = vals.execution + "%";
    document.getElementById("rubric-documentation-val").textContent  = vals.documentation + "%";

    const totalEl = document.getElementById("rubric-total");
    if (totalEl) {
      totalEl.textContent = total + "%";
      totalEl.style.color = total === 100 ? "var(--success)" : "var(--error)";
    }
  }

  Object.values(sliders).forEach((s) => {
    if (s) s.addEventListener("input", () => updateRubric(s.id));
  });
  updateRubric();

  // ── Manual student add form ───────────────────────────────────────────────
  const addForm = document.getElementById("add-student-form");
  if (addForm) {
    addForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(addForm);
      const student = {
        name:      fd.get("name").trim(),
        matricule: fd.get("matricule").trim(),
        code_url:  fd.get("code_url").trim(),
        doc_url:   fd.get("doc_url").trim(),
      };
      if (!student.name || !student.code_url) {
        toast("Name and Code URL are required", "error");
        return;
      }
      students.push(student);
      renderStudentList();
      addForm.reset();
      document.getElementById("s-name")?.focus();
      toast(`${student.name} added`, "success", 2000);
    });
  }

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
          // Deduplicate by code_url
          if (!students.find((x) => x.code_url === s.code_url)) {
            students.push(s);
            added++;
          }
        }

        renderStudentList();
        if (csvArea) csvArea.value = "";
        closeModal("csv-modal");

        if (data.errors.length) {
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

  // ── File upload for CSV ───────────────────────────────────────────────────
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
        <div class="student-list-empty">
          No students added yet. Use the form or import a CSV.
        </div>`;
      updateStartBtn();
      return;
    }

    container.innerHTML = students.map((s, i) => `
      <div class="student-row" id="student-row-${i}">
        <div>
          <div class="font-600">${escHtml(s.name)}</div>
          <div class="text-sm text-secondary">${escHtml(s.matricule || "—")}</div>
        </div>
        <div class="truncate text-sm font-mono" title="${escHtml(s.code_url)}">
          ${escHtml(s.code_url)}
        </div>
        <div class="truncate text-sm font-mono text-secondary" title="${escHtml(s.doc_url || '')}">
          ${s.doc_url ? escHtml(s.doc_url) : '<span class="text-secondary">—</span>'}
        </div>
        <div></div>
        <button class="btn btn-icon sm" onclick="removeStudent(${i})" title="Remove">
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
    if (btn) btn.disabled = students.length === 0;
  }

  // ── Form submission ───────────────────────────────────────────────────────
  const mainForm = document.getElementById("session-form");
  if (mainForm) {
    mainForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (students.length === 0) { toast("Add at least one student", "warning"); return; }

      const codeW = parseInt(sliders.code?.value || 40);
      const execW = parseInt(sliders.execution?.value || 30);
      const docW  = parseInt(sliders.documentation?.value || 30);
      if (codeW + execW + docW !== 100) {
        toast("Rubric weights must sum to 100%", "error"); return;
      }

      const payload = {
        name:                    document.getElementById("session-name")?.value.trim(),
        github_token:            document.getElementById("gh-token")?.value.trim(),
        rubric_code:             codeW,
        rubric_execution:        execW,
        rubric_documentation:    docW,
        ai_feedback_enabled:     document.getElementById("ai-toggle")?.checked,
        plagiarism_enabled:      document.getElementById("plagiarism-toggle")?.checked,
        students,
      };

      if (!payload.name) { toast("Session name is required", "error"); return; }

      const startBtn = document.getElementById("start-btn");
      startBtn.disabled = true;
      startBtn.textContent = "Creating...";

      try {
        const res = await fetch("/api/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (!res.ok) { toast(data.error || "Failed to create session", "error"); return; }

        // Start evaluation
        await fetch(`/api/sessions/${data.session_id}/start`, { method: "POST" });

        window.location.href = `/progress/${data.session_id}`;
      } catch (err) {
        toast("Error: " + err.message, "error");
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

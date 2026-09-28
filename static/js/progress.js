/**
 * Progress page — SSE stream consumer, real-time UI updates.
 */
document.addEventListener("DOMContentLoaded", () => {
  const sessionId = document.getElementById("session-data")?.dataset.sessionId;
  if (!sessionId) return;

  const studentMap = {};   // id -> {el, data}
  const logEl = document.getElementById("log-stream");
  const progressBar  = document.getElementById("progress-bar");
  const progressText = document.getElementById("progress-text");
  const elapsedEl    = document.getElementById("elapsed");
  const viewResultsBtn = document.getElementById("view-results-btn");

  let total = 0, done = 0, startTime = Date.now();

  // ── Elapsed timer ─────────────────────────────────────────────────────────
  const timer = setInterval(() => {
    const secs = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(secs / 60).toString().padStart(2, "0");
    const s = (secs % 60).toString().padStart(2, "0");
    if (elapsedEl) elapsedEl.textContent = `${m}:${s}`;
  }, 1000);

  // ── SSE connection ────────────────────────────────────────────────────────
  const evtSource = new EventSource(`/api/sessions/${sessionId}/stream`);

  evtSource.onmessage = (e) => {
    const event = JSON.parse(e.data);
    handleEvent(event);
  };

  evtSource.onerror = () => {
    addLog("Connection to server lost. Refresh to check status.", "error");
    evtSource.close();
    clearInterval(timer);
  };

  // ── Event dispatcher ──────────────────────────────────────────────────────
  function handleEvent(ev) {
    switch (ev.type) {
      case "session_start":
        total = ev.total;
        updateProgress();
        addLog(`Evaluation started — ${total} student(s)`, "info");
        break;

      case "student_start":
        setStudentStatus(ev.student_id, "running");
        addLog(`Evaluating: ${ev.name}  (${ev.index + 1}/${ev.total})`, "info");
        break;

      case "log":
        addLog(ev.msg, "muted");
        break;

      case "student_done":
        setStudentStatus(ev.student_id, "complete");
        updateStudentGrade(ev.student_id, ev.grade_final);
        done++;
        updateProgress();
        addLog(`Done: grade ${ev.grade_final.toFixed(2)}/20`, "success");
        break;

      case "student_error":
        setStudentStatus(ev.student_id, "error");
        done++;
        updateProgress();
        addLog(`Error: ${ev.error}`, "error");
        break;

      case "plagiarism_start":
        addLog("Running plagiarism detection...", "info");
        break;

      case "plagiarism_done":
        addLog(`Plagiarism check complete — ${ev.alerts} alert(s) found`, ev.alerts > 0 ? "error" : "success");
        break;

      case "final_state":
      case "session_done":
        evtSource.close();
        clearInterval(timer);
        onComplete(ev);
        break;

      case "fatal_error":
        addLog("Fatal error: " + ev.error, "error");
        evtSource.close();
        clearInterval(timer);
        break;

      case "snapshot":
        // Already finished — render from snapshot
        if (ev.session?.status === "complete" || ev.session?.status === "error") {
          evtSource.close();
          clearInterval(timer);
          showResultsButton(sessionId);
          if (ev.students) {
            ev.students.forEach((s) => {
              setStudentStatus(s.id, s.status || "complete");
              if (s.grade_final) updateStudentGrade(s.id, s.grade_final);
            });
          }
          done = ev.session.completed_students || 0;
          total = ev.session.total_students || done;
          updateProgress();
        }
        break;

      case "heartbeat":
        break;  // keep-alive, no action
    }
  }

  // ── DOM helpers ───────────────────────────────────────────────────────────
  function setStudentStatus(id, status) {
    const row = document.getElementById(`sp-${id}`);
    if (!row) return;
    row.className = `student-progress-item ${status}`;
    const dot = row.querySelector(".status-dot");
    if (dot) dot.className = `status-dot ${status}`;
    const label = row.querySelector(".sp-status");
    if (label) {
      const labels = { running: "Evaluating...", complete: "Done", error: "Error", pending: "Pending" };
      label.textContent = labels[status] || status;
    }
  }

  function updateStudentGrade(id, grade) {
    const el = document.getElementById(`sp-grade-${id}`);
    if (!el) return;
    const tier = gradeTier(grade, 20);
    el.textContent = grade.toFixed(2) + "/20";
    el.className = `text-sm font-600 text-${tier === "excellent" || tier === "good" ? "success" : tier === "poor" ? "error" : "secondary"}`;
  }

  function updateProgress() {
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    if (progressBar) {
      progressBar.style.width = pct + "%";
      progressBar.classList.toggle("animated", done < total);
    }
    if (progressText) progressText.textContent = `${done} / ${total} complete (${pct}%)`;
  }

  function addLog(msg, type = "muted") {
    if (!logEl) return;
    const line = document.createElement("div");
    line.className = `log-line ${type}`;
    line.textContent = `[${timestamp()}] ${msg}`;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function timestamp() {
    return new Date().toLocaleTimeString("en-GB", { hour12: false });
  }

  function showResultsButton(sid) {
    if (viewResultsBtn) {
      viewResultsBtn.href = `/results/${sid}`;
      viewResultsBtn.classList.remove("hidden");
    }
  }

  function onComplete(ev) {
    const sid = ev.session?.id || sessionId;
    addLog("Evaluation complete.", "success");

    const avg = ev.session?.average_grade;
    if (avg !== undefined && avg !== null) {
      addLog(`Average grade: ${avg.toFixed(2)}/20`, "success");
    }

    const alerts = ev.alerts?.length || 0;
    if (alerts > 0) {
      addLog(`${alerts} potential plagiarism case(s) flagged`, "error");
    }

    showResultsButton(sid);
    updateProgress();

    // Update header status badge
    const statusBadge = document.getElementById("session-status-badge");
    if (statusBadge) {
      statusBadge.textContent = "Complete";
      statusBadge.className = "badge badge-success";
    }
  }

  // gradeTier is defined in app.js
});

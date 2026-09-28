"""
GitHub Evaluator 2.0 — Flask Web Application
"""
import csv
import io
import json
import os
import queue
import threading
import logging
from datetime import datetime

from flask import (Flask, render_template, request, jsonify,
                   Response, stream_with_context, redirect, url_for, send_file)

import database as db
import config as cfg
from src.evaluator import Evaluator
from src.export import to_csv, to_excel

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ge2-dev-secret-not-for-production")

# In-memory SSE queues keyed by session_id
_sse_queues: dict[str, queue.Queue] = {}
_sse_lock = threading.Lock()

# Per-session locks — prevent double-start race condition
_eval_locks: dict[str, threading.Lock] = {}
_eval_locks_guard = threading.Lock()


def _get_eval_lock(session_id: str) -> threading.Lock:
    with _eval_locks_guard:
        if session_id not in _eval_locks:
            _eval_locks[session_id] = threading.Lock()
        return _eval_locks[session_id]


# ── Startup ──────────────────────────────────────────────────────────────────

@app.before_request
def _ensure_db():
    db.init_db()


@app.context_processor
def _inject_key_status():
    """Make key presence flags available in every template (used by nav)."""
    return {
        "has_github": cfg.has_github_token(),
        "has_gemini": cfg.has_gemini_key(),
    }


# ── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sessions = db.list_sessions()
    settings = cfg.load_settings()
    total_students = sum(s.get("total_students", 0) for s in sessions)
    graded = [s for s in sessions if s.get("average_grade") is not None]
    avg = (
        round(sum(s["average_grade"] for s in graded) / len(graded), 2)
        if graded else None
    )
    pass_rate = None
    if graded:
        threshold = settings.get("passing_grade", 10)
        passing = [s for s in graded if s["average_grade"] >= threshold]
        pass_rate = round(len(passing) / len(graded) * 100, 1)

    return render_template("index.html",
                           sessions=sessions,
                           total_sessions=len(sessions),
                           total_students=total_students,
                           overall_avg=avg,
                           pass_rate=pass_rate,
                           settings=settings)


@app.route("/evaluate")
def evaluate():
    settings = cfg.load_settings()
    return render_template("evaluate.html", settings=settings)


@app.route("/progress/<session_id>")
def progress(session_id):
    session = db.get_session(session_id)
    if not session:
        return redirect(url_for("index"))
    # Embed student list so the template can render the sidebar
    session["students"] = db.list_students(session_id)
    return render_template("progress.html", session=session)


@app.route("/results/<session_id>")
def results(session_id):
    session = db.get_session(session_id)
    if not session:
        return redirect(url_for("index"))
    students = db.list_students(session_id)
    alerts   = db.list_plagiarism_alerts(session_id)
    settings = cfg.load_settings()

    sorted_students = sorted(students, key=lambda s: s.get("grade_final", 0), reverse=True)
    avg = (
        round(sum(s["grade_final"] for s in students if s.get("status") == "complete") /
              max(sum(1 for s in students if s.get("status") == "complete"), 1), 2)
        if students else 0
    )
    return render_template("results.html",
                           session=session,
                           students=sorted_students,
                           alerts=alerts,
                           avg=avg,
                           settings=settings)


@app.route("/results/<session_id>/student/<student_id>")
def student_detail(session_id, student_id):
    session = db.get_session(session_id)
    student = db.get_student(student_id)
    if not session or not student:
        return redirect(url_for("index"))
    settings = cfg.load_settings()
    return render_template("student.html",
                           session=session,
                           student=student,
                           settings=settings)


@app.route("/settings")
def settings_page():
    settings = cfg.load_settings()
    return render_template("settings.html", settings=settings)


# ── API — Settings ────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json(force=True)
    allowed = {
        "github_token", "gemini_api_key",
        "default_rubric_code", "default_rubric_execution", "default_rubric_documentation",
        "docker_enabled", "execution_timeout", "max_file_size_kb",
        "grade_scale", "passing_grade",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}

    # Route sensitive keys to .env; preferences to settings.json
    cfg.save_settings(filtered)

    return jsonify({
        "ok": True,
        "has_github_token": cfg.has_github_token(),
        "has_gemini_key":   cfg.has_gemini_key(),
    })


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = cfg.load_settings()
    # Never expose raw key values — only presence
    return jsonify({
        **{k: v for k, v in s.items() if k not in ("github_token", "gemini_api_key")},
        "has_github_token": cfg.has_github_token(),
        "has_gemini_key":   cfg.has_gemini_key(),
    })




# ── API — Sessions ────────────────────────────────────────────────────────────

@app.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    return jsonify(db.list_sessions())


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Session name is required"}), 400

    students_raw = data.get("students", [])
    if not students_raw:
        return jsonify({"error": "At least one student is required"}), 400

    rubric = {
        "code":          int(data.get("rubric_code", 40)),
        "execution":     int(data.get("rubric_execution", 30)),
        "documentation": int(data.get("rubric_documentation", 30)),
    }
    if sum(rubric.values()) != 100:
        return jsonify({"error": "Rubric weights must sum to 100"}), 400

    global_token = cfg.get_setting("github_token") or ""
    session_token = (data.get("github_token") or "").strip() or global_token

    sid = db.create_session(
        name=name,
        github_token=session_token,
        rubric=rubric,
        ai_feedback=bool(data.get("ai_feedback_enabled", False)),
        plagiarism=bool(data.get("plagiarism_enabled", True)),
    )

    for i, s in enumerate(students_raw):
        db.add_student(
            session_id=sid,
            name=s.get("name", f"Student {i+1}"),
            matricule=s.get("matricule", ""),
            code_url=s.get("code_url", ""),
            doc_url=s.get("doc_url", ""),
            position=i,
        )

    db.update_session(sid, total_students=len(students_raw))

    return jsonify({"session_id": sid}), 201


@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_get_session(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Not found"}), 404
    students = db.list_students(session_id)
    alerts   = db.list_plagiarism_alerts(session_id)
    return jsonify({"session": session, "students": students, "alerts": alerts})


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Not found"}), 404
    db.delete_session(session_id)
    return jsonify({"ok": True})


# ── API — Evaluation lifecycle ────────────────────────────────────────────────

@app.route("/api/sessions/<session_id>/start", methods=["POST"])
def api_start_evaluation(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Not found"}), 404

    # Acquire per-session lock — prevents double-start from rapid clicks
    eval_lock = _get_eval_lock(session_id)
    if not eval_lock.acquire(blocking=False):
        return jsonify({"error": "Evaluation already starting"}), 409

    # Re-read status inside lock to prevent TOCTOU race
    session = db.get_session(session_id)
    if session["status"] == "running":
        eval_lock.release()
        return jsonify({"error": "Already running"}), 409

    db.update_session(session_id, status="running", completed_students=0, failed_students=0)

    q: queue.Queue = queue.Queue()
    with _sse_lock:
        _sse_queues[session_id] = q

    settings = cfg.load_settings()

    def run():
        students = db.list_students(session_id)
        evaluator = Evaluator(
            session_id=session_id,
            github_token=session.get("github_token") or settings.get("github_token", ""),
            rubric={
                "code":          session["rubric_code"],
                "execution":     session["rubric_execution"],
                "documentation": session["rubric_documentation"],
            },
            ai_enabled=bool(session.get("ai_feedback_enabled")),
            ai_api_key=settings.get("gemini_api_key", ""),
            plagiarism_enabled=bool(session.get("plagiarism_enabled")),
            docker_enabled=bool(settings.get("docker_enabled", True)),
            execution_timeout=int(settings.get("execution_timeout", 10)),
            max_file_kb=int(settings.get("max_file_size_kb", 500)),
        )
        try:
            results, plague_alerts = evaluator.run(students, q)

            completed = 0
            failed    = 0
            for r in results:
                db.update_student(r["id"],
                    status               = r.get("status", "complete"),
                    grade_code           = r.get("grade_code", 0),
                    grade_execution      = r.get("grade_execution", 0),
                    grade_documentation  = r.get("grade_documentation", 0),
                    grade_final          = r.get("grade_final", 0),
                    code_files_count     = r.get("code_files_count", 0),
                    doc_files_count      = r.get("doc_files_count", 0),
                    compilation_success  = int(r.get("compilation_success", False)),
                    execution_success    = int(r.get("execution_success", False)),
                    ai_feedback          = r.get("ai_feedback", ""),
                    error_message        = r.get("error_message", ""),
                    analysis_time        = r.get("analysis_time", 0),
                    details              = r.get("details", {}),
                )
                if r.get("status") == "error":
                    failed += 1
                else:
                    completed += 1

            for alert in plague_alerts:
                db.add_plagiarism_alert(
                    session_id   = session_id,
                    s1_id        = alert["student1_id"],
                    s1_name      = alert["student1_name"],
                    s2_id        = alert["student2_id"],
                    s2_name      = alert["student2_name"],
                    score        = alert["score"],
                )

            final_grades = [r.get("grade_final", 0) for r in results
                            if r.get("status") == "complete"]
            avg = round(sum(final_grades) / len(final_grades), 2) if final_grades else None

            db.update_session(session_id,
                status               = "complete",
                completed_students   = completed,
                failed_students      = failed,
                average_grade        = avg)

        except Exception as exc:
            log.exception("Evaluation thread error")
            db.update_session(session_id, status="error")
            q.put({"type": "fatal_error", "error": str(exc)})
        finally:
            q.put({"type": "__END__"})

    def run_and_release():
        try:
            run()
        finally:
            eval_lock.release()

    t = threading.Thread(target=run_and_release, daemon=True, name=f"eval-{session_id[:8]}")
    t.start()

    return jsonify({"ok": True})


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.route("/api/sessions/<session_id>/stream")
def api_stream(session_id):
    with _sse_lock:
        q = _sse_queues.get(session_id)

    # If no live queue, return session state once
    if q is None:
        session  = db.get_session(session_id)
        students = db.list_students(session_id)
        snapshot = json.dumps({"type": "snapshot", "session": session, "students": students})
        def once():
            yield f"data: {snapshot}\n\n"
        return Response(once(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def generate():
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                except queue.Empty:
                    yield "data: {\"type\":\"heartbeat\"}\n\n"
                    continue

                if event.get("type") == "__END__":
                    # Final state push
                    session  = db.get_session(session_id)
                    students = db.list_students(session_id)
                    alerts   = db.list_plagiarism_alerts(session_id)
                    final    = json.dumps({"type": "final_state",
                                          "session": session,
                                          "students": students,
                                          "alerts": alerts})
                    yield f"data: {final}\n\n"
                    break

                yield f"data: {json.dumps(event)}\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                _sse_queues.pop(session_id, None)

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── API — Export ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<session_id>/export/csv")
def api_export_csv(session_id):
    session  = db.get_session(session_id)
    students = db.list_students(session_id)
    if not session or not students:
        return jsonify({"error": "No data"}), 404

    content = to_csv(students)
    name = (session.get("name") or "results").replace(" ", "_")
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'}
    )


@app.route("/api/sessions/<session_id>/export/excel")
def api_export_excel(session_id):
    session  = db.get_session(session_id)
    students = db.list_students(session_id)
    if not session or not students:
        return jsonify({"error": "No data"}), 404
    try:
        content = to_excel(students, session_name=session.get("name", ""))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    name = (session.get("name") or "results").replace(" ", "_")
    return Response(
        content,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'}
    )


# ── API — Batch CSV parse ─────────────────────────────────────────────────────

@app.route("/api/parse-csv", methods=["POST"])
def api_parse_csv():
    """Parse a CSV body and return structured student records."""
    raw = request.get_data(as_text=True)
    if not raw.strip():
        return jsonify({"error": "Empty body"}), 400

    # Strip BOM
    raw = raw.lstrip("\ufeff")

    reader = csv.DictReader(io.StringIO(raw))
    # Normalise headers
    field_aliases = {
        "name":         ["name", "student", "student_name", "nom", "etudiant"],
        "matricule":    ["matricule", "id", "student_id", "mat", "matricul"],
        "code_url":     ["code_url", "code", "repo", "code_repo", "url_code"],
        "doc_url":      ["doc_url", "doc", "documentation", "doc_repo", "url_doc"],
    }

    def resolve(row, field):
        for alias in field_aliases[field]:
            for key in row:
                if key.strip().lower() == alias:
                    return row[key].strip()
        return ""

    students = []
    errors   = []
    for i, row in enumerate(reader, 1):
        name     = resolve(row, "name")
        code_url = resolve(row, "code_url")
        if not name or not code_url:
            errors.append(f"Row {i}: 'name' and 'code_url' are required")
            continue
        students.append({
            "name":      name,
            "matricule": resolve(row, "matricule"),
            "code_url":  code_url,
            "doc_url":   resolve(row, "doc_url"),
        })

    return jsonify({"students": students, "errors": errors})


# ── Error handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404,
                           title="Page Not Found",
                           message="The page you requested does not exist."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500,
                           title="Server Error",
                           message="Something went wrong on our end. Please try again."), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)

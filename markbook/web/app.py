"""Web UI: a thin Flask layer over the same core the CLI uses.

State lives in the runs directory (see markbook.store); the only in-memory state is
the set of grading threads. Progress is polled from status.json, so a page
reload or a server restart never loses a run's history.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from .. import __version__, report as reports
from ..grader import grade_cohort, now_iso, preflight
from ..roster import RosterError, parse_roster
from ..sandbox import SandboxUnavailable
from ..spec import SpecError, load_spec
from ..store import StoreError, _write_atomic, current, load_overrides, record_decision, save_corpora, save_run

from ..views import MAX_REVIEWER, record_view

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_UPLOAD = 2 * 1024 * 1024
MAX_ROSTER_ROWS = 1000


def create_app(runs_dir: Path, *, runtime: str = "docker", jobs: int = 4, token: str | None = None,
               specs_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD, SECRET_KEY=secrets.token_hex(16))
    runs_dir = Path(runs_dir).resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    cache: dict[str, tuple[tuple, dict]] = {}

    # Runs that were mid-flight when a previous server stopped can never finish.
    for d in runs_dir.iterdir():
        st = d / "status.json"
        if st.is_file():
            try:
                data = json.loads(st.read_text())
                if data.get("state") == "running":
                    data.update(state="failed", error="Server restarted while grading; re-run it.")
                    _write_atomic(st, json.dumps(data))
            except (OSError, ValueError):
                pass

    # ── helpers ───────────────────────────────────────────────────────────────

    def run_path(run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            abort(404)
        p = runs_dir / run_id
        if not p.is_dir():
            abort(404)
        return p

    def read_status(p: Path) -> dict | None:
        f = p / "status.json"
        try:
            return json.loads(f.read_text()) if f.is_file() else None
        except (OSError, ValueError):
            return None

    def load_current(p: Path) -> dict:
        key_files = [p / "run.json", p / "overrides.json"]
        key = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in key_files)
        hit = cache.get(str(p))
        if hit and hit[0] == key:
            return hit[1]
        run = current(p)
        cache[str(p)] = (key, run)
        return run

    def queue_ids(run: dict) -> list[str]:
        return [s["id"] for s in run["submissions"] if s["triage"]["state"] == "review"]

    # ── security headers & CSRF ───────────────────────────────────────────────

    @app.before_request
    def csrf_guard():
        if request.method in ("POST", "PUT", "DELETE"):
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        return resp

    @app.template_filter("num")
    def num(v):
        return "—" if v is None else f"{v:g}"

    @app.template_filter("pct")
    def pct(v):
        return "—" if v is None else f"{v * 100:.0f}%"

    @app.context_processor
    def inject():
        return {"version": __version__, "runtime": runtime}

    # ── pages ─────────────────────────────────────────────────────────────────

    @app.get("/")
    def index():
        items = []
        for d in sorted((x for x in runs_dir.iterdir() if x.is_dir()), reverse=True):
            st = read_status(d)
            if (d / "run.json").is_file():
                run = load_current(d)
                items.append({"id": d.name, "name": run["assignment"]["name"], "created": run["created_at"],
                              "summary": run["summary"], "queue": len(queue_ids(run)), "state": "done"})
            elif st:
                items.append({"id": d.name, "name": st.get("name", d.name), "created": st.get("started_at", ""),
                              "summary": None, "queue": 0, "state": st["state"], "status": st})
        return render_template("index.html", runs=items)

    @app.route("/runs/new", methods=["GET", "POST"])
    def new_run():
        presets = sorted(p.name for p in specs_dir.glob("*.y*ml")) if specs_dir and specs_dir.is_dir() else []
        if request.method == "GET":
            return render_template("new.html", presets=presets, form={})
        form = request.form
        spec_text = (form.get("spec_text") or "").strip()
        if (f := request.files.get("spec_file")) and f.filename:
            spec_text = f.read().decode("utf-8", errors="replace")
        roster_text = (form.get("roster_text") or "").strip()
        if (f := request.files.get("roster_file")) and f.filename:
            roster_text = f.read().decode("utf-8-sig", errors="replace")

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        p = runs_dir / run_id
        inp = p / "input"
        inp.mkdir(parents=True)

        def fail(msg: str, code: int = 400):
            shutil.rmtree(p, ignore_errors=True)
            flash(msg, "error")
            return render_template("new.html", presets=presets, form=form), code

        preset = form.get("preset")
        try:
            if preset and specs_dir:
                src = (specs_dir / preset).resolve()
                if src.parent != specs_dir.resolve() or not src.is_file():
                    return fail("Unknown preset spec.")
                spec_path = src  # trusted, operator-provided: may use overlay/cases_file
                spec = load_spec(spec_path)
            else:
                if not spec_text:
                    return fail("Provide a spec (paste YAML or upload a file).")
                spec_path = inp / "spec.yaml"
                spec_path.write_text(spec_text, encoding="utf-8")
                spec = load_spec(spec_path, confine=True)
            entries = parse_roster(roster_text) if roster_text else None
            if not entries:
                return fail("Provide a roster CSV (id,name,email,repo).")
            if len(entries) > MAX_ROSTER_ROWS:
                return fail(f"Roster too large (max {MAX_ROSTER_ROWS} rows).")
            preflight(spec, runtime, build=False)   # the (slow) prepare build runs in the grading thread
        except (SpecError, RosterError) as exc:
            return fail(str(exc))
        except SandboxUnavailable as exc:
            return fail(str(exc), 503)
        (inp / "roster.csv").write_text(roster_text, encoding="utf-8")

        status = {"state": "running", "name": spec.name, "total": len(entries), "done": 0,
                  "active": [], "started_at": now_iso(), "error": None}
        _write_atomic(p / "status.json", json.dumps(status))

        def progress(ev: dict) -> None:
            with lock:
                if ev["event"] == "start":
                    status["active"].append(ev["id"])
                else:
                    status["done"] += 1
                    status["active"] = [a for a in status["active"] if a != ev["id"]]
                _write_atomic(p / "status.json", json.dumps(status))

        def work() -> None:
            try:
                corpora: dict = {}
                run = grade_cohort(spec, entries, runtime, jobs=jobs, token=token, allow_local=False,
                                   progress=progress, run_id=run_id, corpora_out=corpora,
                                   inputs={"spec_path": str(spec_path.resolve()), "allow_local": False})
                save_run(p, run)
                save_corpora(p, corpora)
                reports.write_reports(current(p), p)
                status.update(state="done", active=[])
            except Exception as exc:  # surface any failure to the UI instead of a silent dead thread
                status.update(state="failed", error=f"{type(exc).__name__}: {exc}", active=[])
            with lock:
                _write_atomic(p / "status.json", json.dumps(status))

        threading.Thread(target=work, name=f"grade-{run_id}", daemon=True).start()
        return redirect(url_for("run_view", run_id=run_id))

    @app.get("/runs/<run_id>")
    def run_view(run_id):
        p = run_path(run_id)
        if not (p / "run.json").is_file():
            st = read_status(p) or {"state": "failed", "error": "Run has no results."}
            return render_template("progress.html", run_id=run_id, status=st)
        run = load_current(p)
        subs = run["submissions"]
        flt = request.args.get("show", "review")
        shown = [s for s in subs if flt == "all" or s["triage"]["state"] == "review"]
        if flt == "similarity":
            shown = [s for s in subs if s.get("similarity")]
        sc = run["assignment"]["scale"]
        bins = [0] * 10
        for s in subs:
            if s["score"]["scaled"] is not None:
                bins[min(9, int(s["score"]["scaled"] / sc * 10))] += 1
        return render_template("run.html", run=run, run_id=run_id, subs=shown, flt=flt, bins=bins,
                               peak=max(bins) or 1, queue=queue_ids(run), total=len(subs))

    @app.get("/runs/<run_id>/status.json")
    def run_status(run_id):
        p = run_path(run_id)
        st = read_status(p) or {"state": "unknown"}
        if (p / "run.json").is_file():
            st["state"] = "done"
        return jsonify(st)

    @app.get("/runs/<run_id>/s/<sid>")
    def submission_view(run_id, sid):
        p = run_path(run_id)
        run = load_current(p)
        sub = next((s for s in run["submissions"] if s["id"] == sid), None)
        if not sub:
            abort(404)
        q = queue_ids(run)
        if sid in q:
            i = q.index(sid)
            prev_id, next_id = (q[i - 1] if i else None), (q[i + 1] if i + 1 < len(q) else None)
        else:
            ids = [s["id"] for s in run["submissions"]]
            i = ids.index(sid)
            prev_id, next_id = (ids[i - 1] if i else None), (ids[i + 1] if i + 1 < len(ids) else None)
        decisions = [d for d in load_overrides(p)["decisions"] if d["submission"] == sid]
        return render_template("submission.html", run=run, run_id=run_id, sub=sub, prev_id=prev_id,
                               next_id=next_id, in_queue=sid in q, queue_left=len(q), decisions=decisions)

    @app.post("/runs/<run_id>/s/<sid>/view")
    def view_event(run_id, sid):
        """Session boundary for `markbook stats`: who opened which submission, when. Nothing else is stored."""
        p = run_path(run_id)
        reviewer = (request.form.get("reviewer") or "web").strip() or "web"
        if len(reviewer) > MAX_REVIEWER:
            abort(400)
        if not (p / "run.json").is_file() or not any(s["id"] == sid for s in load_current(p)["submissions"]):
            abort(404)
        record_view(p, run_id, sid, reviewer)
        return Response(status=204)

    @app.post("/runs/<run_id>/s/<sid>/decide")
    def decide(run_id, sid):
        p = run_path(run_id)
        f = request.form
        reviewer = (f.get("reviewer") or "web").strip()[:60] or "web"
        comment = (f.get("comment") or "").strip()[:2000]
        action = f.get("action", "points")
        try:
            if action == "points":
                record_decision(p, sid, reviewer=reviewer, criterion=f.get("criterion"),
                                points=float(f["points"]), comment=comment)
            elif action == "accept_ai":
                record_decision(p, sid, reviewer=reviewer, criterion=f.get("criterion"), comment=comment,
                                from_suggestion=True)
            elif action == "waive_late":
                record_decision(p, sid, reviewer=reviewer, late_waived=True, comment=comment)
            elif action == "restore_late":
                record_decision(p, sid, reviewer=reviewer, late_waived=False, comment=comment)
            elif action in ("clear_similarity", "clear_borderline"):
                record_decision(p, sid, reviewer=reviewer, clear=action.split("_", 1)[1], comment=comment)
            elif action == "note":
                record_decision(p, sid, reviewer=reviewer, comment=comment)
            else:
                abort(400)
            run = current(p)
            reports.write_reports(run, p)
        except (StoreError, KeyError, ValueError) as exc:
            flash(f"Not saved: {exc}", "error")
            return redirect(url_for("submission_view", run_id=run_id, sid=sid))
        # Reviewing should be a flow: once this submission is fully resolved, jump to the next open one.
        me = next(s for s in run["submissions"] if s["id"] == sid)
        if me["triage"]["state"] == "auto":
            remaining = queue_ids(run)
            if remaining:
                flash(f"{me['name']} resolved. {len(remaining)} left in the queue.", "ok")
                return redirect(url_for("submission_view", run_id=run_id, sid=remaining[0]))
            flash("Review queue is empty. Every submission is resolved.", "ok")
            return redirect(url_for("run_view", run_id=run_id))
        flash("Decision saved.", "ok")
        return redirect(url_for("submission_view", run_id=run_id, sid=sid))

    @app.get("/runs/<run_id>/download/<name>")
    def download(run_id, name):
        p = run_path(run_id)
        if not (p / "run.json").is_file():
            abort(404)
        run = load_current(p)
        inc = request.args.get("pending") == "1"
        mime = {"report.json": "application/json", "junit.xml": "application/xml",
                "summary.md": "text/markdown"}
        if name == "report.json":
            body = json.dumps(run, indent=2, ensure_ascii=False)
        elif name == "grades.csv":
            body = reports.to_csv(run, "generic", inc)
        elif name in ("grades.canvas.csv", "grades.moodle.csv"):
            body = reports.to_csv(run, name.split(".")[1], inc)
        elif name == "junit.xml":
            body = reports.to_junit(run)
        elif name == "summary.md":
            body = reports.summary_md(run)
        elif name == "feedback.zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for s in run["submissions"]:
                    z.writestr(f"{s['id']}.md", reports.feedback_md(s, run["assignment"]))
            return Response(buf.getvalue(), mimetype="application/zip",
                            headers={"Content-Disposition": f"attachment; filename={run_id}-feedback.zip"})
        else:
            abort(404)
        return Response(body, mimetype=mime.get(name, "text/csv") + "; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={run_id}-{name}"})

    @app.post("/runs/<run_id>/delete")
    def delete_run(run_id):
        p = run_path(run_id)
        shutil.rmtree(p)
        cache.pop(str(p), None)
        flash("Run deleted.", "ok")
        return redirect(url_for("index"))

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", version=__version__)

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="That page does not exist."), 404

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message="Cross-site request blocked."), 403

    @app.errorhandler(413)
    def too_big(_e):
        return render_template("error.html", code=413, message="Upload too large (max 2 MB)."), 413

    return app

"""The grading engine: fetch → forensics → sandboxed checks → score → triage.

The raw result of a run is never edited. Human decisions live in a separate
overrides document, and `rescore()` derives scores and review state from
(raw result + overrides). That makes every report reproducible and every
change auditable.
"""
from __future__ import annotations

import math
import os
import shutil
import tempfile
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import SCHEMA_VERSION, __version__, checks, similarity
from .fetch import FetchError, checkout
from .gitinfo import read_history
from .roster import Entry
from .sandbox import SandboxUnavailable, docker_status, ensure_image, make_sandbox, podman_status
from .spec import Spec

MAX_REPO_BYTES = 200 * 1024 * 1024
Progress = Callable[[dict], None]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def _strip_symlinks(root: Path) -> int:
    """Remove symlinks from the working copy: they could redirect our own
    overlay writes, or reads, outside the tree. Student projects rarely need them."""
    n = 0
    for p in list(root.rglob("*")):
        if p.is_symlink():
            p.unlink()
            n += 1
    return n


def _late(spec: Spec, last_commit: datetime | None) -> dict:
    info = {"deadline": spec.deadline.isoformat() if spec.deadline else None,
            "last_commit": last_commit.isoformat(timespec="seconds") if last_commit else None,
            "is_late": False, "hours_late": 0.0, "penalty_pct": 0.0}
    if not spec.deadline or not last_commit:
        return info
    over = (last_commit - spec.deadline).total_seconds() - spec.late.grace_minutes * 60
    if over > 0:
        days = math.ceil(over / 86400)
        info.update(is_late=True, hours_late=round(over / 3600, 1),
                    penalty_pct=min(spec.late.max_percent, days * spec.late.percent_per_day))
    return info


def _criterion_record(c, max_points: float) -> dict:
    return {"id": c.id, "title": c.title, "type": c.type, "max_points": max_points, "points": 0.0,
            "status": "error", "evidence": [], "needs_review": False, "review_reason": None, "override": None}


def _run_criteria(spec: Spec, ctx: checks.Context, sandbox_error: str | None) -> list[dict]:
    results: dict[str, dict] = {}
    for c in spec.criteria:
        rec = _criterion_record(c, c.points)
        results[c.id] = rec
        blockers = [results[r] for r in c.requires if results[r]["status"] in ("failed", "error")]
        try:
            if blockers:
                hard = any(b["status"] == "error" for b in blockers)
                rec["status"] = "error" if hard else "failed"
                rec["evidence"] = [{"label": "skipped", "text": f"requires '{blockers[0]['id']}', which did not pass"}]
                if hard:
                    rec.update(needs_review=True, review_reason=f"dependency '{blockers[0]['id']}' errored")
                continue
            if c.type in ("command", "cases") and sandbox_error:
                rec["evidence"] = [{"label": "sandbox", "text": sandbox_error}]
                rec.update(needs_review=True, review_reason="sandbox unavailable for this submission")
                continue
            out = checks.REGISTRY[c.type](c.check, ctx)
            rec["status"] = out.status
            rec["points"] = round(out.fraction * c.points, 2)
            rec["evidence"] = [e.to_dict() for e in out.evidence]
            if out.review:
                rec.update(needs_review=True, review_reason=out.review)
        except Exception as exc:  # grader fault: surface it, never silently grade it 0
            rec["status"] = "error"
            rec["evidence"] = [{"label": "grader error", "text": f"{type(exc).__name__}: {exc}"}]
            rec.update(needs_review=True, review_reason="grader raised an exception",
                       points=0.0)
            traceback.print_exc()
    return [results[c.id] for c in spec.criteria]


def _attach_suggestions(spec: Spec, records: list[dict], reviewer, root) -> None:
    """Ask the AI reviewer about manual criteria marked `ai: true`. Suggestions never affect scores."""
    by_id = {r["id"]: r for r in records}
    for c in spec.criteria:
        if c.type != "manual" or not c.check.get("ai"):
            continue
        rec = by_id[c.id]
        others = [r for r in records if r["type"] != "manual"]
        res = reviewer.suggest(title=c.title, guidance=c.check.get("guidance", ""), max_points=c.points,
                               root=root, auto_results=others)
        if "error" in res:
            rec["evidence"].append({"label": "ai", "text": "suggestion unavailable: " + res["error"]})
        else:
            rec["suggestion"] = res


def grade_submission(spec: Spec, entry: Entry, runtime: str, *, token: str | None = None,
                     allow_local: bool = False, reviewer=None) -> tuple[dict, dict[str, set[int]]]:
    """Grade one repository. Returns (submission record, similarity corpus)."""
    t0 = datetime.now()
    sub: dict = {"id": entry.id, "name": entry.name, "email": entry.email, "repo": entry.repo,
                 "ref": entry.ref, "commit": None, "status": "graded", "error": None,
                 "criteria": [], "history": None, "late": None, "notes": [], "similarity": []}
    corpus: dict[str, set[int]] = {}
    tmp = Path(tempfile.mkdtemp(prefix="markbook_sub_"))
    sandbox = None
    try:
        try:
            # Full history is only needed for `git` checks; lateness needs just the tip commit.
            needs_history = any(c.type == "git" for c in spec.criteria)
            co = checkout(entry.repo, tmp / "clone", ref=entry.ref, token=token, allow_local=allow_local,
                          shallow=not needs_history)
        except FetchError as exc:
            sub.update(status="error", error={"code": exc.code, "message": exc.message})
            return sub, corpus
        sub["commit"] = co.commit

        if _dir_size(co.workdir) > MAX_REPO_BYTES:
            sub.update(status="error", error={"code": "too_large",
                                              "message": f"repository exceeds {MAX_REPO_BYTES // 2**20} MB"})
            return sub, corpus

        # Everything read from .git or used for similarity is captured *before*
        # any student code executes.
        history = read_history(co.path, shallow=co.shallow)
        if co.shallow:   # only the tip commit was fetched: don't report misleading history numbers
            sub["history"] = {"commits": None, "authors": None, "active_days": None,
                              "largest_commit_share": None, "shallow": True}
        else:
            sub["history"] = {"commits": history.count, "authors": len(history.authors),
                              "active_days": history.active_days,
                              "largest_commit_share": round(history.max_single_commit_share, 3),
                              "shallow": False}
        sub["late"] = _late(spec, history.last_commit)
        if spec.similarity.enabled:
            corpus = similarity.read_corpus(co.workdir)

        work = tmp / "work"
        shutil.copytree(co.workdir, work, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        removed = _strip_symlinks(work)
        if removed:
            sub["notes"].append(f"removed {removed} symlink(s) from the submission before grading")
        if spec.overlay:
            shutil.copytree(spec.overlay, work, dirs_exist_ok=True)  # teacher files win over student files

        sandbox_error = None
        if spec.needs_sandbox:
            try:
                sandbox = make_sandbox(runtime, spec.sandbox)
                sandbox.load(work)
            except SandboxUnavailable as exc:
                sandbox_error = str(exc)

        ctx = checks.Context(workdir=work, sandbox=sandbox, history=history,
                             default_timeout=spec.sandbox.timeout)
        sub["criteria"] = _run_criteria(spec, ctx, sandbox_error)
        if reviewer is not None:
            _attach_suggestions(spec, sub["criteria"], reviewer, co.workdir)
        return sub, corpus
    finally:
        if sandbox:
            sandbox.close()
        shutil.rmtree(tmp, ignore_errors=True)
        sub["duration_s"] = round((datetime.now() - t0).total_seconds(), 2)


# ── scoring & triage (pure functions of raw result + overrides) ───────────────

def rescore(run: dict, overrides: dict | None = None) -> dict:
    """Return a deep-ish copy of `run` with scores, triage and summary computed."""
    import copy
    run = copy.deepcopy(run)
    overrides = overrides or {}
    a = run["assignment"]
    for sub in run["submissions"]:
        _score_one(sub, a, overrides.get(sub["id"], {}))
    run["summary"] = summarize(run)
    return run


def _score_one(sub: dict, a: dict, ov: dict) -> None:
    crit_ov = ov.get("criteria", {})
    cleared = set(ov.get("cleared", []))
    reasons: list[dict] = []

    if sub["status"] == "error":
        sub["score"] = {"raw": None, "penalty": 0.0, "final": None, "max": a["max_points"],
                        "scaled": None, "scale": a["scale"], "pending_points": a["max_points"], "complete": False}
        sub["triage"] = {"state": "review", "reasons": [
            {"code": "fetch_failed", "detail": f"{sub['error']['code']}: {sub['error']['message']}"}]}
        return

    raw, pending = 0.0, 0.0
    for c in sub["criteria"]:
        o = crit_ov.get(c["id"])
        c["override"] = o
        needs_human = c["status"] in ("manual", "error")
        if o is not None:
            raw += float(o["points"])
        elif needs_human:
            pending += c["max_points"]
            c["needs_review"] = True
            reasons.append({"code": "criterion", "criterion": c["id"], "detail": c["review_reason"] or "needs review"})
        else:
            raw += c["points"]
            c["needs_review"] = False
        if o is not None:
            c["needs_review"] = False

    late = sub["late"] or {"penalty_pct": 0.0}
    late["waived"] = bool(ov.get("late_waived"))
    pct = 0.0 if late["waived"] else late.get("penalty_pct", 0.0)
    penalty = round(raw * pct / 100, 2)
    final = round(raw - penalty, 2)
    scaled = round(final / a["max_points"] * a["scale"], 2) if a["max_points"] else 0.0
    complete = pending == 0

    if "similarity" not in cleared and sub.get("similarity"):
        top = max(sub["similarity"], key=lambda s: s["score"])
        reasons.append({"code": "similarity", "detail": f"{top['score']:.0%} fingerprint overlap with {top['with_name']}"})
    if (complete and a["pass_mark"] is not None and "borderline" not in cleared
            and abs(scaled - a["pass_mark"]) <= a["borderline_margin"]):
        reasons.append({"code": "borderline", "detail": f"{scaled:g} is within {a['borderline_margin']:g} of the pass mark {a['pass_mark']:g}"})

    sub["score"] = {"raw": round(raw, 2), "penalty": penalty, "final": final, "max": a["max_points"],
                    "scaled": scaled, "scale": a["scale"], "pending_points": round(pending, 2), "complete": complete}
    sub["triage"] = {"state": "review" if reasons else "auto", "reasons": reasons}
    if ov.get("note"):
        sub["reviewer_note"] = ov["note"]


def summarize(run: dict) -> dict:
    a, subs = run["assignment"], run["submissions"]
    done = [s for s in subs if s["score"]["complete"]]
    scaled = sorted(s["score"]["scaled"] for s in done)
    n_crit = len(a["criteria"])
    baseline = len(subs) * n_crit
    review_items = 0
    for s in subs:
        if s["status"] == "error":
            review_items += 1
            continue
        review_items += sum(1 for r in s["triage"]["reasons"] if r["code"] == "criterion")
        review_items += sum(1 for r in s["triage"]["reasons"] if r["code"] in ("similarity", "borderline"))
    auto = [s for s in subs if s["triage"]["state"] == "auto"]
    suggestions = [c["suggestion"] for s in subs for c in s["criteria"] if c.get("suggestion")]
    mean = round(sum(scaled) / len(scaled), 2) if scaled else None
    median = None
    if scaled:
        m = len(scaled) // 2
        median = scaled[m] if len(scaled) % 2 else round((scaled[m - 1] + scaled[m]) / 2, 2)
    return {
        "submissions": len(subs),
        "graded": sum(1 for s in subs if s["status"] == "graded"),
        "errors": sum(1 for s in subs if s["status"] == "error"),
        "complete": len(done),
        "mean": mean, "median": median,
        "provisional_mean": round(sum(s["score"]["scaled"] for s in subs if s["status"] == "graded")
                                  / max(1, sum(1 for s in subs if s["status"] == "graded")), 2)
        if any(s["status"] == "graded" for s in subs) else None,
        "min": scaled[0] if scaled else None, "max": scaled[-1] if scaled else None,
        "pass_rate": round(sum(1 for v in scaled if v >= a["pass_mark"]) / len(scaled), 3)
        if scaled and a["pass_mark"] is not None else None,
        "ai_assist": {
            "suggestions": len(suggestions),
            "input_tokens": sum((x.get("usage") or {}).get("input_tokens") or 0 for x in suggestions),
            "output_tokens": sum((x.get("usage") or {}).get("output_tokens") or 0 for x in suggestions),
            "note": "suggestions are advisory and do not reduce review load: a human still decides every one",
        } if suggestions else None,
        "late": sum(1 for s in subs if (s.get("late") or {}).get("is_late")),
        "similarity_flags": sum(1 for s in subs if s.get("similarity")),
        "review_load": {
            "baseline_items": baseline,
            "review_items": review_items,
            "reduction_pct": round(100 * (1 - review_items / baseline), 1) if baseline else 0.0,
            "fully_automatic_submissions": len(auto),
            "definition": "baseline = every criterion of every submission checked by hand; "
                          "review items = unresolved manual/error criteria + similarity, borderline and fetch-failure flags",
        },
    }


# ── cohort ────────────────────────────────────────────────────────────────────

def assignment_block(spec: Spec, runtime: str) -> dict:
    return {
        "name": spec.name, "version": spec.version, "spec_sha256": spec.sha256,
        "scale": spec.scale, "max_points": spec.max_points, "pass_mark": spec.pass_mark,
        "borderline_margin": spec.borderline_margin,
        "deadline": spec.deadline.isoformat() if spec.deadline else None,
        "late_penalty": {"percent_per_day": spec.late.percent_per_day, "max_percent": spec.late.max_percent,
                         "grace_minutes": spec.late.grace_minutes},
        "sandbox": {"runtime": runtime, "image": spec.sandbox.image},
        "criteria": [{"id": c.id, "title": c.title, "type": c.type, "max_points": c.points} for c in spec.criteria],
    }


def preflight(spec: Spec, runtime: str, log: Callable[[str], None] | None = None) -> None:
    if spec.needs_sandbox and runtime in ("docker", "podman"):
        ok, detail = docker_status() if runtime == "docker" else podman_status()
        if not ok:
            name = runtime.capitalize()
            raise SandboxUnavailable(
                f"{name} is required to run student code safely, but it is not usable: {detail}.\n"
                f"Start {name}, or, if this machine is already an isolated environment (CI job, VM, "
                "LXD container), re-run with --sandbox none.")
        ensure_image(spec.sandbox.image, log, runtime)


def grade_entries(spec: Spec, entries: list[Entry], runtime: str, *, jobs: int = 4, token: str | None = None,
                  allow_local: bool = False, progress: Progress | None = None, reviewer=None
                  ) -> tuple[list[dict], dict[str, dict[str, set[int]]]]:
    """Grade `entries` in parallel. Returns (submissions in roster order, similarity corpora by id).

    Ctrl-C stops *scheduling* new submissions at once; those already running finish (bounded
    by their timeouts) and clean up their containers, then KeyboardInterrupt propagates.
    """
    emit = progress or (lambda e: None)

    def work(entry: Entry):
        emit({"event": "start", "id": entry.id})
        try:
            sub, corpus = grade_submission(spec, entry, runtime, token=token, allow_local=allow_local,
                                           reviewer=reviewer)
        except Exception as exc:  # last-resort guard: one bad repo never sinks the cohort
            sub = {"id": entry.id, "name": entry.name, "email": entry.email, "repo": entry.repo,
                   "ref": entry.ref, "commit": None, "status": "error", "criteria": [], "history": None,
                   "late": None, "notes": [], "similarity": [], "duration_s": 0.0,
                   "error": {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}}
            corpus = {}
        emit({"event": "done", "id": entry.id, "status": sub["status"]})
        return sub, corpus

    pool = ThreadPoolExecutor(max_workers=max(1, jobs))
    futures = [pool.submit(work, e) for e in entries]
    try:
        results = [f.result() for f in futures]
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    subs = [r[0] for r in results]
    corpora = {s["id"]: r[1] for s, r in zip(subs, results) if r[1]}
    return subs, corpora


def assemble_run(spec: Spec, runtime: str, subs: list[dict], corpora: dict[str, dict[str, set[int]]], *,
                 run_id: str, created: str, inputs: dict | None = None) -> dict:
    """Cohort-level step: similarity across all submissions, then scores and summary."""
    for s in subs:
        s["similarity"] = []
    names = {s["id"]: s["name"] for s in subs}
    pairs = []
    if spec.similarity.enabled and len(corpora) > 1:
        starter = similarity.read_corpus(spec.starter) if spec.starter else {}
        pairs = similarity.compare(corpora, threshold=spec.similarity.threshold,
                                   min_fingerprints=spec.similarity.min_fingerprints, starter=starter)
        by_id = {s["id"]: s for s in subs}
        for p in pairs:
            for me, other in ((p.a, p.b), (p.b, p.a)):
                by_id[me]["similarity"].append({"with": other, "with_name": names[other],
                                                "score": round(p.score, 3), "files": p.files})
    run = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "markbook", "version": __version__},
        "run_id": run_id,
        "created_at": created, "finished_at": now_iso(),
        "assignment": assignment_block(spec, runtime),
        "submissions": subs,
        "similarity": [p.to_dict() for p in pairs],
    }
    if inputs:
        run["inputs"] = inputs   # where the spec/roster came from; lets `retry` re-grade
    return rescore(run)


def grade_cohort(spec: Spec, entries: list[Entry], runtime: str = "docker", *, jobs: int = 4,
                 token: str | None = None, allow_local: bool = False,
                 progress: Progress | None = None, run_id: str | None = None,
                 inputs: dict | None = None, log: Callable[[str], None] | None = None,
                 corpora_out: dict | None = None, reviewer=None) -> dict:
    preflight(spec, runtime, log)
    created = now_iso()
    subs, corpora = grade_entries(spec, entries, runtime, jobs=jobs, token=token, allow_local=allow_local,
                                  progress=progress, reviewer=reviewer)
    if corpora_out is not None:
        corpora_out.update(corpora)
    return assemble_run(spec, runtime, subs, corpora, run_id=run_id or uuid.uuid4().hex[:12], created=created,
                        inputs=inputs)

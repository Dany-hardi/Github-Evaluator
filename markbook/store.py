"""On-disk run store: plain files, no database.

    <run_dir>/run.json         raw machine result (never edited after grading)
    <run_dir>/overrides.json   human decisions, append-only audit trail
    <run_dir>/report.json      derived: run + overrides applied, schema-valid
    <run_dir>/...              derived: CSVs, feedback/, summary.md, junit.xml

Files are diff-able, greppable, easy to back up and to attach to a ticket.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

try:
    import fcntl
except ImportError:  # non-POSIX: no inter-process lock (single writer assumed)
    fcntl = None

from .grader import now_iso, rescore


class StoreError(ValueError):
    pass


def _write_atomic(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(run_dir: Path):
    """Serialise read-modify-write of overrides.json across processes (CLI + web UI)."""
    if fcntl is None:
        yield
        return
    with open(Path(run_dir) / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def save_corpora(run_dir: Path, corpora: dict) -> None:
    """Similarity fingerprints per student, so `retry` can recompute similarity for the whole cohort."""
    data = {sid: {f: sorted(fp) for f, fp in files.items()} for sid, files in corpora.items()}
    _write_atomic(Path(run_dir) / "fingerprints.json", json.dumps(data))


def load_corpora(run_dir: Path) -> dict:
    p = Path(run_dir) / "fingerprints.json"
    if not p.is_file():
        return {}
    return {sid: {f: set(fp) for f, fp in files.items()} for sid, files in json.loads(p.read_text()).items()}


def save_run(run_dir: Path, run: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(run_dir / "run.json", json.dumps(run, indent=2, ensure_ascii=False))


def load_run(run_dir: Path) -> dict:
    p = Path(run_dir) / "run.json"
    if not p.is_file():
        raise StoreError(f"{run_dir} is not a markbook run directory (no run.json)")
    return json.loads(p.read_text(encoding="utf-8"))


def load_overrides(run_dir: Path) -> dict:
    p = Path(run_dir) / "overrides.json"
    if not p.is_file():
        return {"decisions": [], "effective": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def effective_overrides(doc: dict) -> dict:
    return doc.get("effective", {})


def current(run_dir: Path) -> dict:
    """The run with every human decision applied and scores recomputed."""
    return rescore(load_run(run_dir), effective_overrides(load_overrides(run_dir)))


def _find(run: dict, sub_id: str) -> dict:
    for s in run["submissions"]:
        if s["id"] == sub_id:
            return s
    raise StoreError(f"no submission {sub_id!r} in this run")


def record_decision(run_dir: Path, sub_id: str, *, reviewer: str, criterion: str | None = None,
                    points: float | None = None, comment: str = "", late_waived: bool | None = None,
                    clear: str | None = None, from_suggestion: bool = False) -> dict:
    """Apply one human decision. Appends to the audit log and updates the effective view."""
    with _locked(Path(run_dir)):
        return _record(run_dir, sub_id, reviewer, criterion, points, comment, late_waived, clear, from_suggestion)


def _record(run_dir, sub_id, reviewer, criterion, points, comment, late_waived, clear, from_suggestion=False) -> dict:
    run = load_run(run_dir)
    sub = _find(run, sub_id)
    doc = load_overrides(run_dir)
    eff = doc["effective"].setdefault(sub_id, {})

    entry = {"at": now_iso(), "reviewer": reviewer, "submission": sub_id, "comment": comment}
    if criterion is not None:
        crit = next((c for c in sub["criteria"] if c["id"] == criterion), None)
        if crit is None:
            raise StoreError(f"submission {sub_id!r} has no criterion {criterion!r}")
        sug = crit.get("suggestion")
        if from_suggestion:
            if not sug:
                raise StoreError(f"{criterion!r} has no AI suggestion to accept")
            points = sug["points"] if points is None else points
        if points is None or not 0 <= points <= crit["max_points"]:
            raise StoreError(f"points must be between 0 and {crit['max_points']:g} for {criterion!r}")
        eff.setdefault("criteria", {})[criterion] = {"points": points, "comment": comment,
                                                    "reviewer": reviewer, "at": entry["at"]}
        entry.update(action="set_points", criterion=criterion, points=points,
                     was=crit["points"], was_status=crit["status"])
        if sug:   # always record what the AI said next to what the human decided, so deviations are visible
            entry["ai"] = {"suggested_points": sug["points"], "model": sug.get("model"),
                           "confidence": sug.get("confidence"), "accepted": from_suggestion,
                           "unchanged": points == sug["points"]}
            eff["criteria"][criterion]["ai_assisted"] = from_suggestion
    if late_waived is not None:
        eff["late_waived"] = late_waived
        entry.update(action="waive_late" if late_waived else "restore_late")
    if clear is not None:
        if clear not in ("similarity", "borderline"):
            raise StoreError("can only clear 'similarity' or 'borderline' flags")
        eff.setdefault("cleared", [])
        if clear not in eff["cleared"]:
            eff["cleared"].append(clear)
        entry.update(action="clear_flag", flag=clear)
    if comment and "action" not in entry:
        eff["note"] = comment
        entry["action"] = "note"
    if "action" not in entry:
        raise StoreError("nothing to record: give a criterion+points, a waiver, a flag to clear, or a comment")

    doc["decisions"].append(entry)
    _write_atomic(Path(run_dir) / "overrides.json", json.dumps(doc, indent=2, ensure_ascii=False))
    return entry
